from __future__ import annotations

import json
import re
import uuid
from typing import Any, Iterator, Optional, Sequence

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import BaseTool

from . import memory_bridge

# Qwen3's own tool-call format uses <tool_call> tags; the system prompt in
# prompts.py additionally asks for a ```tool_call fenced block, which is
# easier for a small local model to produce reliably. Both are accepted.
TOOL_CALL_RE = re.compile(
    r"```tool_call\s*\n?(\{.*?\})\s*```|<tool_call>\s*(\{.*?\})\s*</tool_call>",
    re.S,
)

# A plain-text reply never legitimately starts this way — only a tool_call
# block does — so this prefix is enough to decide, from the first streamed
# chunk, whether to show tokens live or hold them back until the tool runs.
_TOOL_CALL_PREFIXES = ("```", "<tool_call>")


def _tools_block(tools: Sequence[BaseTool]) -> str:
    if not tools:
        return ""

    lines = ["Доступные функции:"]
    for t in tools:
        args = "без аргументов"
        schema = getattr(t, "args_schema", None)
        if schema is not None:
            json_schema = schema.model_json_schema()
            props = json_schema.get("properties", {})
            required = set(json_schema.get("required", []))
            if props:
                args = ", ".join(
                    f"{name} ({info.get('type', 'any')}{'' if name in required else ', опционально'})"
                    for name, info in props.items()
                )
        lines.append(f"- {t.name}({args}) — {t.description}")
    return "\n".join(lines)


def _render_transcript(messages: Sequence[BaseMessage]) -> str:
    """Collapse everything after the system message into one user-turn
    transcript, since the underlying Qwen call is (system_prompt,
    user_prompt) rather than a full chat history."""
    lines: list[str] = []
    has_tool_results = False

    for m in messages:
        if isinstance(m, SystemMessage):
            continue
        if isinstance(m, HumanMessage):
            lines.append(f'Вопрос пользователя: "{m.content}"')
        elif isinstance(m, AIMessage) and m.tool_calls:
            for tc in m.tool_calls:
                args = json.dumps(tc.get("args") or {}, ensure_ascii=False)
                lines.append(f"[Вызов функции] {tc['name']}({args})")
        elif isinstance(m, AIMessage) and m.content:
            lines.append(f"[Твой предыдущий ответ] {m.content}")
        elif isinstance(m, ToolMessage):
            has_tool_results = True
            lines.append(f"[Результат функции {m.name}] {m.content}")

    if has_tool_results:
        lines.append(
            "Теперь либо вызови ещё одну функцию, если нужно больше "
            "информации, либо ответь пользователю обычным текстом на "
            "основе того, что уже узнал. Не вызывай функцию с теми же "
            "аргументами повторно."
        )

    return "\n".join(lines)


def _parse_tool_call(raw: str) -> AIMessage:
    match = TOOL_CALL_RE.search(raw)
    if not match:
        return AIMessage(content=raw)

    payload = match.group(1) or match.group(2)
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return AIMessage(content=raw)

    if not isinstance(parsed, dict) or "name" not in parsed:
        return AIMessage(content=raw)

    arguments = parsed.get("arguments") or {}
    call_id = f"call_{uuid.uuid4().hex[:8]}"
    return AIMessage(
        content="",
        tool_calls=[{"name": parsed["name"], "args": arguments, "id": call_id, "type": "tool_call"}],
    )


class ChatQwen(BaseChatModel):
    """LangChain chat-model adapter around the shared local Qwen3-4B
    instance. Qwen isn't natively wired for LangChain's structured tool
    calling, so this renders bound tools into the system prompt (matching
    the model's existing ```tool_call convention) and parses that block back
    out of the raw text into proper AIMessage.tool_calls — everything above
    this class (LangGraph's ToolNode, the agent loop) only ever sees
    standard LangChain messages."""

    max_new_tokens: int = 512

    @property
    def _llm_type(self) -> str:
        return "qwen3-local"

    def bind_tools(self, tools: Sequence[BaseTool], **kwargs: Any):
        return self.bind(tools=list(tools), **kwargs)

    def _render(self, messages: Sequence[BaseMessage], tools: Sequence[BaseTool]) -> tuple[str, str]:
        system_text = next((m.content for m in messages if isinstance(m, SystemMessage)), "")
        block = _tools_block(tools)
        system_prompt = f"{system_text}\n\n{block}" if block else system_text
        user_prompt = _render_transcript(messages)
        return system_prompt, user_prompt

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        system_prompt, user_prompt = self._render(messages, kwargs.get("tools") or [])
        qwen = memory_bridge.get_qwen_model()
        raw = qwen.generate(system_prompt=system_prompt, user_prompt=user_prompt, max_new_tokens=self.max_new_tokens)
        message = _parse_tool_call(raw)
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        system_prompt, user_prompt = self._render(messages, kwargs.get("tools") or [])
        qwen = memory_bridge.get_qwen_model()

        def emit(text: str) -> ChatGenerationChunk:
            chunk = ChatGenerationChunk(message=AIMessageChunk(content=text))
            if run_manager:
                run_manager.on_llm_new_token(text, chunk=chunk)
            return chunk

        buffer = ""
        decided = False
        is_tool_call = False
        flushed = False

        for piece in qwen.generate_stream(
            system_prompt=system_prompt, user_prompt=user_prompt, max_new_tokens=self.max_new_tokens
        ):
            buffer += piece
            if not decided and len(buffer) >= 4:
                decided = True
                is_tool_call = buffer.lstrip().startswith(_TOOL_CALL_PREFIXES)
            if decided and not is_tool_call:
                if not flushed:
                    flushed = True
                    yield emit(buffer)
                else:
                    yield emit(piece)

        if not decided:
            is_tool_call = buffer.lstrip().startswith(_TOOL_CALL_PREFIXES)
            if not is_tool_call:
                flushed = True
                yield emit(buffer)

        if is_tool_call:
            message = _parse_tool_call(buffer)
            if message.tool_calls:
                yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_calls=message.tool_calls))
            elif not flushed:
                # Looked like a tool_call block but failed to parse — fall
                # back to showing the raw text, since it was withheld above.
                yield emit(buffer)
