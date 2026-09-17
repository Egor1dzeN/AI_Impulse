from __future__ import annotations

import json
import re
from typing import Any, Iterator

from . import memory_bridge
from .tools import TOOL_SPECS

TOOL_CALL_RE = re.compile(
    r"```tool_call\s*\n?(\{.*?\})\s*```|<tool_call>\s*(\{.*?\})\s*</tool_call>",
    re.S,
)

PERSONA = (
    "Ты — Impulse, персональный ассистент пользователя. Отвечай по-русски, "
    "кратко и по делу. У тебя есть долгая память о пользователе (см. ниже, "
    "если есть) и набор функций, которыми ты можешь реально пользоваться, "
    "чтобы действовать, а не только отвечать."
)

TOOL_INSTRUCTIONS = (
    "Если для ответа нужно выполнить действие (создать задачу, записать "
    "трату, записать приём пищи или вес, посмотреть сводку) — ответь "
    "ТОЛЬКО одним блоком вида:\n"
    "```tool_call\n"
    '{{"name": "имя_функции", "arguments": {{"ключ": "значение"}}}}\n'
    "```\n"
    "без какого-либо другого текста вокруг. Если действие не требуется — "
    "просто ответь пользователю обычным текстом. Никогда не выдумывай "
    "функции, которых нет в списке. Сегодняшняя дата: {today}."
)


def _tools_block() -> str:
    lines = ["Доступные функции:"]
    for name, spec in TOOL_SPECS.items():
        args = ", ".join(f"{k} ({v})" for k, v in spec["arguments"].items()) or "без аргументов"
        lines.append(f"- {name}({args}) — {spec['description']}")
    return "\n".join(lines)


def build_system_prompt(memory_context: str, today: str) -> str:
    parts = [PERSONA, _tools_block(), TOOL_INSTRUCTIONS.format(today=today)]
    if memory_context:
        parts.append(memory_context)
    return "\n\n".join(parts)


def parse_tool_call(text: str) -> dict[str, Any] | None:
    match = TOOL_CALL_RE.search(text)
    if not match:
        return None
    raw = match.group(1) or match.group(2)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict) or "name" not in parsed:
        return None
    parsed.setdefault("arguments", {})
    return parsed


def generate_reply(system_prompt: str, user_prompt: str) -> str:
    qwen = memory_bridge.get_qwen_model()
    return qwen.generate(system_prompt=system_prompt, user_prompt=user_prompt)


def generate_reply_stream(system_prompt: str, user_prompt: str) -> Iterator[str]:
    qwen = memory_bridge.get_qwen_model()
    yield from qwen.generate_stream(system_prompt=system_prompt, user_prompt=user_prompt)


def _followup_prompt(user_message: str, tool_name: str, arguments: dict[str, Any], result: dict[str, Any]) -> str:
    return (
        f"Пользователь написал: \"{user_message}\"\n\n"
        f"Ты вызвал функцию {tool_name} с аргументами {json.dumps(arguments, ensure_ascii=False)} "
        f"и получил результат: {json.dumps(result, ensure_ascii=False)}\n\n"
        "Напиши короткий дружелюбный ответ пользователю по-русски на основе этого результата. "
        "Не вызывай функции снова, отвечай обычным текстом."
    )


def generate_followup(system_prompt: str, user_message: str, tool_name: str, arguments: dict[str, Any], result: dict[str, Any]) -> str:
    return generate_reply(system_prompt, _followup_prompt(user_message, tool_name, arguments, result))


def generate_followup_stream(system_prompt: str, user_message: str, tool_name: str, arguments: dict[str, Any], result: dict[str, Any]) -> Iterator[str]:
    yield from generate_reply_stream(system_prompt, _followup_prompt(user_message, tool_name, arguments, result))
