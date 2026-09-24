from __future__ import annotations

import json
import logging
import os
import time
import uuid
from threading import Lock
from typing import Any, Iterator, Optional, Sequence

import httpx
from gigachat import GigaChat
from gigachat.exceptions import RateLimitError, ResponseError, ServerError
from gigachat.models import Chat, Function, FunctionCall, Messages, MessagesRole
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

logger = logging.getLogger(__name__)

# Matches the env var name the team already set locally — kept as-is rather
# than renamed, so switching this on needs no extra setup beyond what's
# already in place.
GIGACHAT_CREDENTIALS = os.environ.get("GIGA_CHAT_IDE")
GIGACHAT_SCOPE = os.environ.get("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
GIGACHAT_BASE_URL = os.environ.get("GIGACHAT_BASE_URL", "https://api.giga.chat/v1")
GIGACHAT_MODEL = os.environ.get("GIGACHAT_MODEL", "GigaChat-3-Ultra")
GIGACHAT_VERIFY_SSL = os.environ.get("GIGACHAT_VERIFY_SSL", "0") == "1"

_client_lock = Lock()
_client: GigaChat | None = None


def gigachat_is_configured() -> bool:
    return bool(GIGACHAT_CREDENTIALS)


def get_gigachat_client() -> GigaChat:
    """Lazily build the single process-wide GigaChat client (mirrors
    memory_bridge.get_qwen_model()'s lazy-singleton pattern) — a GigaChat
    instance owns an httpx client and its own OAuth token refresh, so it
    should be reused across requests rather than rebuilt per call."""
    global _client
    if _client is not None:
        return _client

    with _client_lock:
        if _client is not None:
            return _client

        if not GIGACHAT_CREDENTIALS:
            logger.error("GIGA_CHAT_IDE is not set — GigaChat backend was selected but has no credentials.")
            raise RuntimeError(
                "GIGA_CHAT_IDE is not set — GigaChat backend was selected but has no credentials."
            )

        logger.info(
            "giga client init: base_url=%s scope=%s model=%s verify_ssl=%s",
            GIGACHAT_BASE_URL, GIGACHAT_SCOPE, GIGACHAT_MODEL, GIGACHAT_VERIFY_SSL,
        )
        _client = GigaChat(
            base_url=GIGACHAT_BASE_URL,
            credentials=GIGACHAT_CREDENTIALS,
            scope=GIGACHAT_SCOPE,
            verify_ssl_certs=GIGACHAT_VERIFY_SSL,
        )

    return _client


# ============================================================
# LangChain tool -> GigaChat function schema
# ============================================================

_JSON_SCALAR_TYPES = {"string", "number", "integer", "boolean", "array", "object"}


def _flatten_property(prop: dict) -> dict:
    """A LangChain tool's args_schema is a pydantic v2 model, whose
    `model_json_schema()` renders an `Optional[X]` field as
    `{"anyOf": [{"type": "X"}, {"type": "null"}]}` rather than a flat
    `"type"` key. GigaChat's function-parameters schema (an older,
    OpenAI-functions-style shape) only understands a single flat `type` per
    property, so this picks the first non-null branch out of `anyOf` before
    falling back to "string" for anything else unrecognized (a bare `$ref`,
    an enum-only fragment, ...) rather than raising."""
    if prop.get("type") in _JSON_SCALAR_TYPES:
        return prop
    for branch in prop.get("anyOf", []):
        if branch.get("type") in _JSON_SCALAR_TYPES:
            return branch
    return {**prop, "type": "string"}


def tool_to_function(tool: BaseTool) -> Function:
    schema = tool.args_schema.model_json_schema() if tool.args_schema else {}
    properties: dict[str, dict] = {}

    for name, prop in schema.get("properties", {}).items():
        resolved = _flatten_property(prop)
        entry: dict[str, Any] = {
            "type": resolved.get("type", "string"),
            "description": prop.get("description") or resolved.get("description") or "",
        }
        if "enum" in resolved:
            entry["enum"] = resolved["enum"]
        properties[name] = entry

    return Function(
        name=tool.name,
        description=tool.description,
        parameters={
            "type": "object",
            "properties": properties,
            "required": schema.get("required", []),
        },
    )


# ============================================================
# LangChain messages <-> GigaChat messages
# ============================================================


def _ensure_json_content(content: str) -> str:
    """GigaChat requires a function-role message's content to itself be
    valid JSON (a 422 INVALID_PARAMS otherwise) — but our tools return
    plain human-readable text (e.g. search_memory's "По этому запросу в
    памяти ничего не найдено."), same as every other LangChain-style tool.
    Wrap it as a JSON string when it isn't already valid JSON, rather than
    changing every tool's return type just for this one backend."""
    try:
        json.loads(content)
        return content
    except (json.JSONDecodeError, ValueError):
        return json.dumps(content, ensure_ascii=False)


def _to_gigachat_messages(messages: Sequence[BaseMessage]) -> list[Messages]:
    out: list[Messages] = []
    # ToolMessage.name isn't reliably populated by every LangGraph ToolNode
    # code path (its own error-handling branches set it explicitly; the
    # plain success path doesn't consistently) — track each tool_call_id ->
    # name from the AIMessage that requested it, and fall back to that
    # instead of trusting ToolMessage.name alone, since GigaChat requires a
    # real function name on a role="function" message.
    call_names: dict[str, str] = {}

    for m in messages:
        if isinstance(m, SystemMessage):
            out.append(Messages(role=MessagesRole.SYSTEM, content=str(m.content)))
        elif isinstance(m, HumanMessage):
            out.append(Messages(role=MessagesRole.USER, content=str(m.content)))
        elif isinstance(m, AIMessage):
            if m.tool_calls:
                tc = m.tool_calls[0]
                call_names[tc["id"]] = tc["name"]
                out.append(
                    Messages(
                        role=MessagesRole.ASSISTANT,
                        content="",
                        function_call=FunctionCall(name=tc["name"], arguments=tc.get("args") or {}),
                    )
                )
            else:
                out.append(Messages(role=MessagesRole.ASSISTANT, content=str(m.content or "")))
        elif isinstance(m, ToolMessage):
            name = m.name or call_names.get(m.tool_call_id) or "unknown_tool"
            content = m.content if isinstance(m.content, str) else str(m.content)
            out.append(Messages(role=MessagesRole.FUNCTION, content=_ensure_json_content(content), name=name))

    return out


def _parse_message(message: Messages) -> AIMessage:
    if message.function_call:
        call_id = f"call_{uuid.uuid4().hex[:8]}"
        return AIMessage(
            content=message.content or "",
            tool_calls=[
                {
                    "name": message.function_call.name,
                    "args": message.function_call.arguments or {},
                    "id": call_id,
                    "type": "tool_call",
                }
            ],
        )
    return AIMessage(content=message.content or "")


# A pooled keep-alive connection the server already closed while we were
# busy (e.g. a tool call took a few seconds) fails instantly with
# "Server disconnected without sending a response". Nothing reached the
# user yet, so the request is simply resent — httpx drops the dead
# connection and opens a fresh one.
_TRANSIENT_NETWORK_ERRORS = (httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError, httpx.ConnectError)
_NETWORK_RETRIES = 2
# 429 Too Many Requests (e.g. while a dataset import is extracting facts in
# the background) or a transient 5xx such as 504 Gateway Time-out: wait and
# resend, same "only before the first piece" rule.
_RATE_LIMIT_RETRIES = 7


# When the main model's paid quota is used up (402 Payment Required) every
# answer failed with "ошибка на стороне GigaChat". Switch the whole process to
# the fallback model instead: a weaker answer beats no answer.
GIGACHAT_FALLBACK_MODEL = os.environ.get("GIGACHAT_FALLBACK_MODEL", "GigaChat-2")
_exhausted_models: set[str] = set()


def _is_payment_error(exc: Exception) -> bool:
    return getattr(exc, "status_code", None) == 402 or " 402 " in f" {exc} " or "Payment Required" in str(exc)


def effective_model(model: str) -> str:
    return GIGACHAT_FALLBACK_MODEL if model in _exhausted_models else model


def _switch_to_fallback(chat: Chat, exc: Exception) -> bool:
    if not _is_payment_error(exc) or chat.model == GIGACHAT_FALLBACK_MODEL:
        return False
    logger.error("giga: %s quota exhausted (402) — switching to %s", chat.model, GIGACHAT_FALLBACK_MODEL)
    _exhausted_models.add(chat.model)
    chat.model = GIGACHAT_FALLBACK_MODEL
    return True


def _rate_limit_delay(exc: RateLimitError, retry: int) -> float:
    retry_after = getattr(exc, "retry_after", 0) or 0
    return float(retry_after) if retry_after > 0 else min(2.0 ** (retry + 1), 30.0)


class ChatGigaChat(BaseChatModel):
    """LangChain chat-model adapter around the GigaChat API (via the
    official `gigachat` SDK), as a drop-in alternative to ChatQwen — same
    bind_tools()/_generate()/_stream() surface, so agent_graph.py and every
    existing tool need no changes to switch backends.

    Unlike ChatQwen (which collapses the whole conversation into one
    system+user text prompt because the local Qwen call only takes those
    two strings), GigaChat's API takes genuine multi-turn structured
    messages and real function-calling, so this passes the LangChain
    message list straight through instead of flattening it."""

    model: str = GIGACHAT_MODEL
    temperature: float = 0.3
    # Accepted for interface parity with ChatQwen (the Composer's
    # "Размышления" toggle passes this through agent_graph.build_graph) but
    # not forwarded to the API yet — GigaChat's reasoning_effort parameter
    # support varies by model and hasn't been validated against this
    # account, so leaving it unset is the safer default for now.
    enable_thinking: bool = True

    @property
    def _llm_type(self) -> str:
        return "gigachat"

    def bind_tools(self, tools: Sequence[BaseTool], **kwargs: Any):
        return self.bind(tools=list(tools), **kwargs)

    def _build_chat(self, messages: Sequence[BaseMessage], tools: Sequence[BaseTool], stream: bool) -> Chat:
        functions = [tool_to_function(t) for t in tools] if tools else None
        return Chat(
            model=effective_model(self.model),
            messages=_to_gigachat_messages(messages),
            temperature=self.temperature,
            functions=functions,
            stream=stream,
        )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        chat = self._build_chat(messages, kwargs.get("tools") or [], stream=False)
        client = get_gigachat_client()
        logger.info("giga request sending: model=%s messages=%d tools=%d", self.model, len(chat.messages), len(chat.functions or []))
        started = time.monotonic()
        rate_limited = network_failed = 0
        for _ in range(_NETWORK_RETRIES + _RATE_LIMIT_RETRIES + 1):
            try:
                resp = client.chat(chat)
                break
            except (RateLimitError, ServerError) as exc:
                if rate_limited == _RATE_LIMIT_RETRIES:
                    logger.exception("giga request failed after %.1fs", time.monotonic() - started)
                    raise
                delay = _rate_limit_delay(exc, rate_limited)
                rate_limited += 1
                logger.warning("giga request: 429, waiting %.1fs (%d/%d)", delay, rate_limited, _RATE_LIMIT_RETRIES)
                time.sleep(delay)
            except _TRANSIENT_NETWORK_ERRORS as exc:
                if network_failed == _NETWORK_RETRIES:
                    logger.exception("giga request failed after %.1fs", time.monotonic() - started)
                    raise
                network_failed += 1
                logger.warning("giga request: %s, retrying (%d/%d)", exc, network_failed, _NETWORK_RETRIES)
            except ResponseError as exc:
                if _switch_to_fallback(chat, exc):
                    continue
                logger.exception("giga request failed after %.1fs", time.monotonic() - started)
                raise
            except Exception:
                logger.exception("giga request failed after %.1fs", time.monotonic() - started)
                raise
        logger.info("giga request done: took %.1fs", time.monotonic() - started)
        message = _parse_message(resp.choices[0].message)
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        chat = self._build_chat(messages, kwargs.get("tools") or [], stream=True)
        client = get_gigachat_client()
        logger.info("giga stream sending: model=%s messages=%d tools=%d", self.model, len(chat.messages), len(chat.functions or []))
        started = time.monotonic()
        first_piece = True

        pending_call: FunctionCall | None = None

        rate_limited = network_failed = 0
        for _ in range(_NETWORK_RETRIES + _RATE_LIMIT_RETRIES + 1):
            try:
                for piece in client.stream(chat):
                    if first_piece:
                        logger.info("giga stream first piece after %.1fs", time.monotonic() - started)
                        first_piece = False
                    if not piece.choices:
                        continue
                    delta = piece.choices[0].delta
                    if delta.function_call:
                        pending_call = delta.function_call
                        continue
                    if delta.content:
                        out = ChatGenerationChunk(message=AIMessageChunk(content=delta.content))
                        if run_manager:
                            run_manager.on_llm_new_token(delta.content, chunk=out)
                        yield out
                break
            except (RateLimitError, ServerError) as exc:
                if not first_piece or rate_limited == _RATE_LIMIT_RETRIES:
                    logger.exception("giga stream failed after %.1fs", time.monotonic() - started)
                    raise
                delay = _rate_limit_delay(exc, rate_limited)
                rate_limited += 1
                logger.warning("giga stream: 429, waiting %.1fs (%d/%d)", delay, rate_limited, _RATE_LIMIT_RETRIES)
                time.sleep(delay)
            except _TRANSIENT_NETWORK_ERRORS as exc:
                # Only before anything was received — a retry after partial
                # output would duplicate text already shown to the user.
                if not first_piece or network_failed == _NETWORK_RETRIES:
                    logger.exception("giga stream failed after %.1fs", time.monotonic() - started)
                    raise
                network_failed += 1
                logger.warning("giga stream: %s, retrying (%d/%d)", exc, network_failed, _NETWORK_RETRIES)
            except ResponseError as exc:
                if first_piece and _switch_to_fallback(chat, exc):
                    continue
                logger.exception("giga stream failed after %.1fs", time.monotonic() - started)
                raise
            except Exception:
                logger.exception("giga stream failed after %.1fs", time.monotonic() - started)
                raise
        logger.info("giga stream done: took %.1fs", time.monotonic() - started)

        if pending_call:
            call_id = f"call_{uuid.uuid4().hex[:8]}"
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": pending_call.name,
                            "args": pending_call.arguments or {},
                            "id": call_id,
                            "type": "tool_call",
                        }
                    ],
                )
            )
