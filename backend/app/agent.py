from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterator

import httpx
from gigachat.exceptions import GigaChatException, RateLimitError
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.errors import GraphRecursionError

from . import agent_graph, conversations, db, memory_bridge, personal_data, user_settings
from .masking import mask_sensitive
from .prompts import build_system_prompt
from .tools import MEMORY_TOOL_NAMES

# Each round of the agent loop is 2 super-steps (agent -> tools), so this
# allows roughly 10 rounds of tool calls (memory searches and/or actions)
# before the graph gives up and we fall back to a canned reply.
RECURSION_LIMIT = 28

logger = logging.getLogger(__name__)

# A local 4-bit model occasionally hits EOS on the very first generated
# token (seen right after cold start) and produces a genuinely empty reply.
# When that happens with no tool call either — i.e. nothing has been shown
# to the user yet this turn — it's safe to transparently retry once before
# falling back to a canned message.
MAX_ATTEMPTS = 2

# Tools that write something (or prepare a confirm card for it). A turn that
# claims an action happened must have called one of these.
ACTION_TOOL_NAMES = {
    "create_calendar_event", "update_calendar_event", "delete_calendar_event", "draft_email",
    "create_task", "add_expense", "add_income", "update_transaction", "log_meal", "log_weight", "log_workout",
}

# Imperatives ("поставь", "запланируйте") or "можешь/нужно + infinitive" —
# not bare stems, so a question like "что у меня запланировано?" isn't
# mistaken for a request to act.
_ACTION_REQUEST_RE = re.compile(
    r"\b(поставь|запланируй|назначь|создай|добавь|отправь|напиши|удали|перенеси|отмени|напомни|запиши|пригласи|забронируй)(те)?\b"
    r"|\b(можешь|сможешь|нужно|надо|пожалуйста)\b[^.?!\n]{0,20}\b(поставить|запланировать|назначить|создать|добавить|"
    r"отправить|написать|удалить|перенести|отменить|напомнить|записать|пригласить|забронировать)\b",
    re.IGNORECASE,
)
_ACTION_CLAIM_RE = re.compile(
    r"^\W*(готово|сделано)\b"
    r"|(?<!не )\b(я )?(поставил|запланировал|создал|добавил|отправил|удалил|перенёс|перенес|отменил|записал|пригласил|назначил)а?\b"
    r"|(?<!не )\b(запланирован|создан|добавлен|отправлен|удал[её]н|перенес[её]н|отмен[её]н|назначен)[аоы]?\b",
    re.IGNORECASE | re.MULTILINE,
)

ACTION_CLAIM_CORRECTION = (
    "Системная проверка: в предыдущем ответе ты сообщил, что действие выполнено, но ни один инструмент "
    "действия в этом ходе не вызывался — значит, на самом деле ничего не создано и не отправлено. "
    "Если подходящий инструмент тебе доступен — вызови его сейчас (он подготовит черновик на подтверждение "
    "пользователем). Если такого инструмента нет (например, сервис не подключён в настройках) или действие "
    "выполнять нельзя — честно скажи пользователю, что ничего не сделано, и почему. Не пиши, что действие выполнено."
)
ACTION_NOT_DONE_REPLY = (
    "Я ничего не создал и не отправил: не удалось подготовить черновик действия. "
    "Проверьте, что нужный сервис (например, Google Calendar или Gmail) подключён в настройках, и повторите запрос."
)


def claims_unperformed_action(user_message: str, reply: str, action_tools: set[str]) -> bool:
    """The user asked for an action, the reply says it's done, yet no
    action tool ran this turn."""
    # "Какие встречи нужно отменить?" asks for a list — it is not an order.
    asks = "?" in (user_message or "") and re.search(
        r"(?i)\b(какие|какой|какая|что|кого|кому|когда|где|сколько|куда|ли)\b", user_message or "")
    return (
        not action_tools
        and not asks
        and bool(_ACTION_REQUEST_RE.search(user_message or ""))
        and bool(_ACTION_CLAIM_RE.search(reply or ""))
    )

EMAIL_SEND_UNAVAILABLE_NOTE = (
    "⚠️ Gmail не подключён, поэтому отправить письмо я не могу — текст можно скопировать и отправить вручную. "
    "Подключите Gmail в Настройках, и я покажу черновик с кнопкой «Отправить»."
)
EMAIL_SEND_UNAVAILABLE_REPLY = (
    "Отправить письмо не могу: Gmail не подключён, поэтому ничего не отправлено. Черновик выше в диалоге — "
    "его можно скопировать и отправить вручную. Подключите Gmail в Настройках, и я покажу черновик с кнопкой «Отправить»."
)
# Imperatives only: "отправил ли я письмо?" is a question about the past, not an order.
_SEND_ORDER_RE = re.compile(
    r"(?i)\b(отправь|отправьте|отправляй|отправляйте|отправить|отошли|отошлите|пошли|пошлите|перешли|перешлите|send)\b"
)
_EMAIL_WORD_RE = re.compile(r"(?i)\b(письм\w*|почт\w*|e-?mail\w*|имейл\w*|мейл\w*|черновик\w*|gmail)")
_EMAIL_DRAFT_RE = re.compile(r"(?i)(тема\s*:|subject\s*:|черновик\w* письм)")


def wants_email_sent(user_message: str, history: list[HumanMessage | AIMessage]) -> bool:
    """The user asks to send an email: "отправь письмо Ольге", or a bare
    "отправь" right after the assistant showed an email draft."""
    text = user_message or ""
    if not _SEND_ORDER_RE.search(text):
        return False
    if "?" in text and re.search(r"(?i)\b(какие|какой|какая|что|кого|кому|когда|где|сколько|куда|ли)\b", text):
        return False
    if _EMAIL_WORD_RE.search(text):
        return True
    last_reply = next((m.content for m in reversed(history) if isinstance(m, AIMessage)), "")
    return isinstance(last_reply, str) and bool(_EMAIL_DRAFT_RE.search(last_reply))


# How many of the most recent saved messages (both roles combined) to feed
# back in as short-term conversational context, on top of whatever the
# agent pulls from long-term memory via search_memory. Was kept small for
# a local model with a tight context window; raised now that the chat
# model has a much larger context budget to spend.
HISTORY_MESSAGE_LIMIT = 30

# Tools whose replies are cheap to re-fetch and easy for a small model to
# latch onto: seeing its own "Сегодня в Сиднее +18°C..." sitting right in
# context strongly biases it to repeat/rehash that instead of reasoning
# about the new message (confirmed in practice — tightening the prompt
# alone didn't stop it). Replies driven by these tools are swapped out for
# a neutral placeholder in short-term history so there's nothing tempting
# to copy; the real content is untouched in the DB and still shown to the
# user, this only affects what the model sees on the *next* turn.
_VOLATILE_INFO_TOOLS = {"get_weather", "get_exchange_rate", "show_map"}

# Tool arguments/results can carry a big memory-context blob (search_memory)
# or a long geometry payload — cap what lands in the log so one call can't
# blow up the file, while still keeping enough to see what happened.
_LOG_VALUE_LIMIT = 800


def _log_repr(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if len(text) > _LOG_VALUE_LIMIT:
        text = text[:_LOG_VALUE_LIMIT] + f"...<{len(text) - _LOG_VALUE_LIMIT} more chars>"
    return text


def _load_recent_history(user_id: str, conversation_id: str) -> list[HumanMessage | AIMessage]:
    # Order by rowid, not created_at: two messages saved in quick succession
    # (e.g. the user's message and the assistant's reply right after it) can
    # land on the same millisecond-resolution timestamp on Windows, which
    # made created_at-based ordering nondeterministic. rowid is SQLite's
    # own monotonically-increasing insertion order, so it's always correct.
    # Scoped to conversation_id too — otherwise switching chats would leak
    # unrelated context from a different conversation into the model.
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT role, content, action_json FROM ("
            "  SELECT role, content, action_json, rowid FROM chat_messages "
            "  WHERE user_id = ? AND conversation_id = ? ORDER BY rowid DESC LIMIT ?"
            ") ORDER BY rowid ASC",
            (user_id, conversation_id, HISTORY_MESSAGE_LIMIT),
        ).fetchall()

    history: list[HumanMessage | AIMessage] = []
    for row in rows:
        if row["role"] == "user":
            history.append(HumanMessage(content=row["content"]))
            continue

        tool_name = None
        if row["action_json"]:
            try:
                tool_name = json.loads(row["action_json"]).get("tool")
            except json.JSONDecodeError:
                pass

        if tool_name in _VOLATILE_INFO_TOOLS:
            content = f"[Ответ был получен через вызов {tool_name} — не повторяй эти данные, если не спросят снова]"
        else:
            content = row["content"]
        history.append(AIMessage(content=content))

    return history


def handle_chat_turn_stream(
    user_id: str, conversation_id: str, message: str, enable_thinking: bool = True
) -> Iterator[dict[str, Any]]:
    """Run one chat turn through the LangGraph agent, yielding incremental
    events as it works:
    {"type": "token", "text": ...} for live reply text,
    {"type": "tool_call", "tool": ..., "arguments": ...} for each tool call
      the agent makes along the way (memory searches included),
    {"type": "done", "action": ...} once the turn is fully saved, where
      `action` is the last non-memory tool call executed (or None).

    The agent decides for itself, per turn, whether it needs to consult
    long-term memory (via the search_memory tool) and how many times to
    query it before answering — no long-term memory context is fetched up
    front. Short-term context is different: the last HISTORY_MESSAGE_LIMIT
    messages saved under this conversation_id are always prepended, so the
    model sees recent back-and-forth directly rather than only through
    extracted facts — long-term memory is user-wide (spans conversations),
    this short-term history is scoped to just this one conversation.
    """
    logger.info(
        "chat turn start: user_id=%s conversation_id=%s message=%s",
        user_id, conversation_id, _log_repr(message),
    )

    message = mask_sensitive(message)
    graph = agent_graph.build_graph(user_id, enable_thinking=enable_thinking)
    gmail_connected = bool(user_settings.get_gmail_refresh_token(user_id))
    system_prompt = build_system_prompt(
        today=_describe_now(user_id),
        intervals_icu_connected=bool(user_settings.get_intervals_icu_key(user_id)),
        google_calendar_connected=bool(user_settings.get_google_calendar_refresh_token(user_id)),
        gmail_connected=gmail_connected,
    )
    try:
        digest = memory_bridge.profile_digest(user_id, personal_data.now_local(user_id).date())
    except Exception:  # noqa: BLE001 — the turn must work without the pinned profile
        logger.exception("profile digest failed: user_id=%s", user_id)
        digest = ""
    if digest:
        system_prompt += (
            "\n\nПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ — проверенные факты, собранные из его данных (в скобках — источник). "
            "Для вопросов о самом пользователе (имя, возраст, город, переезд, работа, близкие, спорт, привычки) "
            "опирайся в первую очередь на этот блок: он точнее поиска по памяти. Возраст бери ТОЛЬКО из строки "
            "«ВЫЧИСЛЕНО», сам не считай. Точные числа, даты встреч, письма и траты по-прежнему бери из функций.\n"
            + digest
        )
    history = _load_recent_history(user_id, conversation_id)
    send_unavailable = not gmail_connected and wants_email_sent(message, history)
    logger.info("chat turn: loaded %d short-term history message(s) for conversation_id=%s", len(history), conversation_id)

    # Nudges the model to consider search_memory without permanently baking
    # the reminder into the saved/displayed message — only the LLM's own
    # input gets it, _save_message() below still stores the user's real
    # text, so history/page-reload doesn't show this appended onto every
    # message.
    llm_message = f"{message}\n\nНе забудь посмотреть в память, вдруг там что-то важное"
    inputs = {
        "messages": [SystemMessage(content=system_prompt), *history, HumanMessage(content=llm_message)],
    }
    config = {"recursion_limit": RECURSION_LIMIT}

    def run_graph(run_inputs: dict[str, Any]):
        """One agent pass (with the empty-reply retry); streams token /
        tool_call events and returns what the pass produced."""
        reply_parts: list[str] = []
        last_action: dict[str, Any] | None = None
        web_search_sources: list[dict[str, Any]] = []
        action_tools: set[str] = set()
        used_tools: list[str] = []

        for attempt in range(1, MAX_ATTEMPTS + 1):
            reply_parts = []
            last_action = None
            had_tool_call = False
            pending_calls: dict[str, dict[str, Any]] = {}
            action_tools = set()
            recursion_hit = False
            llm_error: str | None = None
            web_search_sources = []

            try:
                for mode, payload in graph.stream(run_inputs, config=config, stream_mode=["messages", "updates"]):
                    if mode == "messages":
                        chunk, meta = payload
                        if meta.get("langgraph_node") == "agent" and chunk.content:
                            reply_parts.append(chunk.content)
                            yield {"type": "token", "text": chunk.content}
                        continue

                    for node_name, node_update in payload.items():
                        for msg in node_update.get("messages", []):
                            if node_name == "agent" and isinstance(msg, AIMessage) and msg.tool_calls:
                                for tc in msg.tool_calls:
                                    pending_calls[tc["id"]] = {"name": tc["name"], "arguments": tc["args"]}
                                    logger.info(
                                        "tool call requested: user_id=%s %s(%s)",
                                        user_id, tc["name"], _log_repr(tc["args"]),
                                    )
                            elif node_name == "tools" and isinstance(msg, ToolMessage):
                                had_tool_call = True
                                call = pending_calls.pop(msg.tool_call_id, None)
                                name = call["name"] if call else msg.name
                                arguments = call["arguments"] if call else {}
                                # A tool built with response_format="content_and_artifact"
                                # (currently just build_route) puts its full
                                # result on .artifact and a slimmed-down summary
                                # on .content — .content is what re-enters the
                                # model's prompt on the next turn, so anything
                                # too big for that (e.g. a route's few-thousand-
                                # point geometry) has to travel via .artifact
                                # instead. Everything else leaves .artifact
                                # unset and keeps going through .content as before.
                                result = msg.artifact if msg.artifact is not None else _parse_tool_result(msg.content)
                                logger.info(
                                    "tool call result: user_id=%s %s(%s) -> %s",
                                    user_id, name, _log_repr(arguments), _log_repr(result),
                                )
                                yield {"type": "tool_call", "tool": name, "arguments": arguments}
                                if name not in used_tools:
                                    used_tools.append(name)
                                # A call the graph's guards refused did nothing: it must not
                                # become the turn's action (the UI drew a draft card from it).
                                refused = isinstance(result, str) and result.startswith("НЕ ВЫПОЛНЕНО")
                                if name not in MEMORY_TOOL_NAMES and not refused:
                                    last_action = {"tool": name, "arguments": arguments, "result": result}
                                if name in ACTION_TOOL_NAMES and not refused:
                                    action_tools.add(name)
                                if name == "web_search" and isinstance(result, dict):
                                    # Flattened in call order across every
                                    # web_search this turn — matches how the
                                    # model itself numbers "[sources=[N]]"
                                    # citations (see _rewrite_source_citations).
                                    web_search_sources.extend(result.get("results") or [])
            except GraphRecursionError:
                recursion_hit = True
                logger.warning(
                    "recursion limit (%d) hit for user_id=%s conversation_id=%s message=%r",
                    RECURSION_LIMIT, user_id, conversation_id, message,
                )
            except RateLimitError:
                # GigaChat's own quota, not this app's — retrying immediately
                # would just hit the same 429, so surface it plainly instead of
                # burning another attempt (or letting it propagate and blow up
                # the whole SSE response, which is what happened before this
                # was added: an uncaught exception mid-stream crashes the
                # generator and the frontend gets nothing but a broken request).
                llm_error = "rate_limit"
                logger.warning(
                    "GigaChat rate limit hit for user_id=%s conversation_id=%s", user_id, conversation_id,
                )
            except (GigaChatException, httpx.HTTPError):
                # httpx errors: the connection to GigaChat dropped even after
                # chat_gigachat's own retries — answer plainly instead of
                # letting the exception kill the SSE stream mid-turn.
                llm_error = "llm_error"
                logger.exception(
                    "GigaChat request failed for user_id=%s conversation_id=%s", user_id, conversation_id,
                )

            if recursion_hit:
                fallback = "Не получилось сформировать ответ за отведённое число шагов. Попробуй переформулировать вопрос."
                reply_parts = [fallback]
                yield {"type": "token", "text": fallback}
                break

            if llm_error == "rate_limit":
                fallback = "Сейчас слишком много запросов к модели — лимит GigaChat временно исчерпан. Подождите немного и попробуйте снова."
                reply_parts = [fallback]
                yield {"type": "token", "text": fallback}
                break

            if llm_error == "llm_error":
                fallback = "Не получилось получить ответ от модели — ошибка на стороне GigaChat. Попробуйте ещё раз через некоторое время."
                reply_parts = [fallback]
                yield {"type": "token", "text": fallback}
                break

            if reply_parts or had_tool_call:
                break  # got real text and/or a tool ran — nothing to retry

            # Nothing was yielded at all this attempt, so a silent retry is
            # invisible to the frontend (still shows "thinking").
            logger.warning(
                "empty reply on attempt %d/%d for user_id=%r, message=%r",
                attempt, MAX_ATTEMPTS, user_id, message,
            )


        return {
            "reply": "".join(reply_parts).strip(),
            "last_action": last_action,
            "web_search_sources": web_search_sources,
            "action_tools": action_tools,
            "used_tools": used_tools,
        }

    run = yield from run_graph(inputs)

    # GigaChat's safety filter sometimes answers an ordinary question about
    # the user's own data ("какой у меня адрес и в каком городе я живу") with
    # a canned refusal. One retry with the question framed as the user's own
    # data lookup gets a real answer.
    if _is_canned_refusal(run["reply"]):
        logger.warning("canned GigaChat refusal, retrying: user_id=%s reply=%s", user_id, _log_repr(run["reply"]))
        yield {"type": "reset"}
        retry_inputs = {
            "messages": [
                SystemMessage(content=system_prompt), *history,
                HumanMessage(content=(
                    "Это вопрос о моих собственных данных, загруженных в приложение (почта, календарь, переписка, "
                    f"банковские уведомления). Найди ответ в них функциями и памятью: {message}"
                )),
            ],
        }
        run = yield from run_graph(retry_inputs)
        if _is_canned_refusal(run["reply"]) and digest:
            # Still refused: answer from the verified profile lines that match
            # the question — the data is the user's own, there's nothing to hide.
            fallback = _profile_fallback(message, digest)
            if fallback:
                yield {"type": "reset"}
                run["reply"] = fallback
                yield {"type": "token", "text": fallback}

    # The model sometimes reports an action as done ("Готово, встреча
    # запланирована") without ever calling the draft tool — nothing exists
    # and there was no confirmation card. Catch that deterministically: give
    # it one corrected pass, and if it still claims success, replace the
    # reply with an honest "nothing was done".
    if claims_unperformed_action(message, run["reply"], run["action_tools"]):
        logger.warning(
            "unperformed action claimed: user_id=%s conversation_id=%s reply=%s",
            user_id, conversation_id, _log_repr(run["reply"]),
        )
        yield {"type": "reset"}
        retry_inputs = {
            "messages": [*inputs["messages"], AIMessage(content=run["reply"]), HumanMessage(content=ACTION_CLAIM_CORRECTION)],
        }
        run = yield from run_graph(retry_inputs)
        if claims_unperformed_action(message, run["reply"], run["action_tools"]):
            logger.warning("unperformed action claimed again, replacing reply: user_id=%s", user_id)
            yield {"type": "reset"}
            run["reply"] = EMAIL_SEND_UNAVAILABLE_REPLY if send_unavailable else ACTION_NOT_DONE_REPLY
            yield {"type": "token", "text": run["reply"]}

    reply = run["reply"]
    last_action = run["last_action"]
    web_search_sources = run["web_search_sources"]
    if not reply:
        reply = (
            "Готово."
            if last_action is not None
            else "Не получилось получить ответ от модели. Попробуй повторить запрос."
        )
        yield {"type": "token", "text": reply}

    reply = mask_sensitive(_humanize_sources(_rewrite_source_citations(reply, web_search_sources)))
    reply = _ensure_source_line(reply, run.get("used_tools") or [], bool(digest))
    if send_unavailable and EMAIL_SEND_UNAVAILABLE_REPLY not in reply:
        # Added, never replacing: the draft text itself stays in the reply.
        reply = f"{reply}\n\n{EMAIL_SEND_UNAVAILABLE_NOTE}"

    _save_message(user_id, conversation_id, "user", message, None)
    _save_message(user_id, conversation_id, "assistant", reply, last_action)
    conversations.touch_and_maybe_title(conversation_id, message)

    logger.info(
        "chat turn done: user_id=%s conversation_id=%s reply=%s last_action_tool=%s",
        user_id, conversation_id, _log_repr(reply), last_action["tool"] if last_action else None,
    )

    # `reply` here (unlike the raw tokens streamed above) is the corrected,
    # saved text — includes it so the frontend can replace its streamed
    # accumulation with the same string _rewrite_source_citations() fixed
    # up, instead of keeping whatever raw "[sources=[N]]" tag streamed by.
    yield {"type": "done", "action": last_action, "reply": reply}


_SOURCE_CITATION_RE = re.compile(r" ?\[\s*sources?\s*=\s*\[\s*(\d+(?:\s*,\s*\d+)*)\s*\]\s*\]", re.IGNORECASE)


def _rewrite_source_citations(text: str, sources: list[dict[str, Any]]) -> str:
    """The underlying model cites web_search hits with a bare
    "[sources=[N]]" / "[sources=[N, M]]" tag no matter how firmly
    WEB_SEARCH_INSTRUCTIONS (prompts.py) asks for a real markdown link
    instead — observed to persist across turns despite the instruction, so
    this rewrites it after the fact rather than relying on the prompt
    alone. `sources` is the flattened, call-ordered list of every
    web_search result this turn — N is 1-based into it, matching how the
    model itself numbers them. Falls back to just dropping the tag when
    there were no web_search results to map it to, or the index is out of
    range, since a raw tag leaking into the reply is worse than a missing
    citation."""
    if not text:
        return text

    def replace(match: "re.Match[str]") -> str:
        indices = [int(n) for n in re.findall(r"\d+", match.group(1))]
        links = []
        for i in indices:
            if 1 <= i <= len(sources):
                src = sources[i - 1]
                url = src.get("url")
                title = src.get("title") or url
                if url:
                    links.append(f"[{title}]({url})")
        return f" ({', '.join(links)})" if links else ""

    result = _SOURCE_CITATION_RE.sub(replace, text)
    return re.sub(r" {2,}", " ", result)


_TECH_TERMS = {
    "schedule_analysis": "анализ расписания",
    "calendar_events": "календарь",
    "get_calendar_events": "Google Календарь",
    "recurring_schedule": "регулярные события",
    "recurring_payments": "регулярные платежи",
    "inbox_overview": "входящие письма",
    "search_emails": "почта",
    "get_email_thread": "переписка по письму",
    "list_emails": "почта",
    "search_messages": "переписка в Telegram",
    "search_memory": "профиль пользователя",
    "list_memories": "профиль пользователя",
    "finance_report": "банковские операции",
    "monthly_cashflow": "банковские операции",
    "top_contacts": "встречи, письма и чаты",
    "data_overview": "загруженные данные",
    "get_exchange_rate_history": "курсы ЦБ РФ",
    "get_exchange_rate": "курсы ЦБ РФ",
    "get_health_summary": "сводка по здоровью",
    "get_finance_summary": "сводка по финансам",
    "list_transactions": "банковские операции",
    "get_weather": "прогноз погоды",
    "build_route": "маршрут",
    "web_search": "поиск в интернете",
    "log_meal": "журнал питания",
    "email_patterns": "регулярные письма",
    "proactive_suggestions": "анализ данных",
    "next_week": "следующая неделя",
    "this_week": "эта неделя",
    "last_week": "прошлая неделя",
    "last_month": "прошлый месяц",
    "month_before_last": "позапрошлый месяц",
    "this_month": "этот месяц",
    "last_3_days": "последние 3 дня",
    "last_7_days": "последние 7 дней",
}
_TECH_RE = re.compile(r"\b(" + "|".join(sorted(map(re.escape, _TECH_TERMS), key=len, reverse=True)) + r")\b(\s*\([^)]*\))?")


# "[sources=профиль пользователя]" / "[sources=[sec_phish_colleague]]": the
# model's citation tag with a label instead of a web_search number, which
# _rewrite_source_citations leaves alone.
_TEXT_SOURCE_TAG_RE = re.compile(r" ?\[\s*sources?\s*=\s*\[?\s*([^\[\]]*?)\s*\]?\s*\]", re.IGNORECASE)
# A tool call the model wrote out as plain text instead of calling it.
_RAW_TOOL_CALL_RE = re.compile(r"\s*<tool_call>[\s\S]*?(?:</tool_call>|$)")


def _humanize_sources(text: str) -> str:
    """Explainability must not show raw technical traces: tool names and
    period keywords the model sometimes echoes are replaced with plain words,
    a labelled "[sources=…]" tag becomes a normal "Источник: …" line (or is
    dropped when the answer already has one), and a leaked raw tool call is
    removed."""
    text = _RAW_TOOL_CALL_RE.sub("", text)
    labels: list[str] = []

    def drop_tag(m: "re.Match[str]") -> str:
        label = m.group(1).strip(" ,.")
        if re.fullmatch(r"[A-Za-z0-9_:.\-]+", label or ""):
            label = "данные пользователя"  # an internal record id, not a readable source
        if label and label not in labels:
            labels.append(label)
        return ""

    text = _TEXT_SOURCE_TAG_RE.sub(drop_tag, text).rstrip()
    if labels and "источник" not in text.lower():
        text += "\n\nИсточник: " + ", ".join(labels) + "."
    # "сводка по здоровью (get_health_summary)" — the bracketed tool name only repeats the words before it.
    tool_names = [k for k in _TECH_TERMS if not re.fullmatch(r"(this|next|last)_\w+|month_before_last", k)]
    text = re.sub(r"\s*\((" + "|".join(map(re.escape, tool_names)) + r")\)", "", text)
    plain = re.compile(r"\b(" + "|".join(map(re.escape, _TECH_TERMS)) + r")\b")
    return _TECH_RE.sub(
        lambda m: _TECH_TERMS[m.group(1)] + plain.sub(lambda p: _TECH_TERMS[p.group(1)], m.group(2) or ""), text
    )


_CANNED_REFUSAL_RE = re.compile(
    r"(Генеративные языковые модели не обладают собственным мнением|не обладает собственным мнением|"
    r"разговоры на некоторые темы (временно )?ограничены|не транслирует мнение своих разработчиков|"
    r"чувствительными темами,? временно ограничены|могут создавать некорректные ответы|"
    r"Не люблю менять тему разговора|"
    r"Что-то в вашем вопросе меня смущает|разговоры на чувствительные темы могут быть ограничены|"
    r"Как у нейросетевой языковой модели у меня не может быть настроения)",
    re.IGNORECASE,
)


def _profile_fallback(question: str, digest: str) -> str:
    words = {w[:5] for w in re.findall(r"[а-яёa-z]{4,}", question.lower())}
    words |= {"город", "живёт", "живет"} if re.search(r"(?i)адрес|жив[уёе]|город", question) else set()
    scored = []
    for line in digest.split("\n"):
        low = line.lower()
        hits = sum(1 for w in words if w in low)
        if hits:
            scored.append((hits, line.lstrip("- ")))
    if not scored:
        return ""
    best = [line for _, line in sorted(scored, key=lambda x: -x[0])[:3]]
    return ("По вашим данным:\n" + "\n".join(f"- {b}" for b in best)
            + "\n\nИсточник: профиль пользователя, собранный из его почты, переписки и календаря.")


def _is_canned_refusal(reply: str) -> bool:
    return bool(reply) and len(reply) < 600 and bool(_CANNED_REFUSAL_RE.search(reply))


# Tools that read the user's data -> the plain-words source they stand for.
_SOURCE_LABELS = {
    "calendar_events": "календарь", "schedule_analysis": "календарь", "recurring_schedule": "календарь и регулярные письма",
    "inbox_overview": "почта", "search_emails": "почта", "get_email_thread": "почта", "list_emails": "почта",
    "get_email": "почта", "search_messages": "переписка в Telegram", "top_contacts": "встречи, письма и чаты",
    "finance_report": "банковские уведомления", "monthly_cashflow": "банковские уведомления",
    "get_finance_summary": "банковские уведомления", "list_transactions": "банковские уведомления",
    "search_memory": "профиль пользователя (память)", "list_memories": "профиль пользователя (память)",
    "get_health_summary": "данные о питании и весе", "proactive_suggestions": "почта, календарь и банковские уведомления",
    "data_overview": "загруженные данные", "get_weather": "прогноз погоды", "build_route": "маршрут на карте",
    "get_exchange_rate": "курсы ЦБ РФ", "get_exchange_rate_history": "курсы ЦБ РФ", "web_search": "поиск в интернете",
    "get_calendar_events": "календарь", "recurring_payments": "банковские уведомления и письма о списаниях",}


def _ensure_source_line(reply: str, used_tools: list[str], has_profile: bool) -> str:
    """Explainability is scored on EVERY answer: one without its basis costs
    the whole criterion. When the model forgot the "Источник: …" line, add
    it from the tools that actually ran this turn (or the pinned profile)."""
    if not reply or re.search(r"(?im)^\W*источник", reply) or re.search(r"(?i)\bисточник(и)?\s*:", reply):
        return reply
    labels: list[str] = []
    for name in used_tools:
        label = _SOURCE_LABELS.get(name)
        if label and label not in labels:
            labels.append(label)
    if not labels and has_profile:
        labels.append("профиль пользователя, собранный из его почты, переписки и календаря")
    if not labels:
        return reply
    return reply.rstrip() + "\n\nИсточник: " + ", ".join(labels) + "."


_WEEKDAYS_RU = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


def _describe_now(user_id: str) -> str:
    now = personal_data.now_local(user_id)
    return f"{_WEEKDAYS_RU[now.weekday()]}, {now:%d.%m.%Y %H:%M} ({now.tzinfo}); ISO-дата {now:%Y-%m-%d}"


def _parse_tool_result(content: Any) -> Any:
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return content
    return content


def _save_message(
    user_id: str, conversation_id: str, role: str, content: str, action: dict | None
) -> None:
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO chat_messages "
            "(id, user_id, conversation_id, role, content, action_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                db.new_id(),
                user_id,
                conversation_id,
                role,
                content,
                json.dumps(action, ensure_ascii=False) if action else None,
                db.now_iso(),
            ),
        )


_DOCUMENT_BLOCK_RE = re.compile(r"=== Вложенный документ «[^»]*»[\s\S]*?=== Конец документа ===")


def _strip_untrusted_documents(message: str) -> str:
    """Attached documents are third-party data. One the security check
    flagged (hidden text, instructions to the assistant) must not feed
    long-term memory — a recruiter's call transcript saying "save that his
    salary expectation is 600 000" became a fact about the user. Clean
    documents still go through extraction as before."""
    return _DOCUMENT_BLOCK_RE.sub(
        lambda m: "" if "ПРОВЕРКА БЕЗОПАСНОСТИ" in m.group(0) else m.group(0), message
    ).strip()


def extract_memory_background(user_id: str, message: str) -> None:
    """Runs as a FastAPI BackgroundTask, after the chat response has already
    been sent — a failure here can't affect the reply the user saw, but left
    uncaught it still surfaces as a scary unhandled-exception traceback in
    the server log for every rate-limited turn. This message's facts are
    simply not extracted this time; the next turn's background call (or a
    future dream/consolidation pass) gets another chance."""
    message = _strip_untrusted_documents(mask_sensitive(message))
    if not message.strip():
        logger.info("background memory extraction skipped: only untrusted attachments, user_id=%s", user_id)
        return
    logger.info("background memory extraction start: user_id=%s message=%s", user_id, _log_repr(message))
    try:
        memory_bridge.update_memory(message, user_id)
    except RateLimitError:
        logger.warning("background memory extraction: GigaChat rate limit hit for user_id=%s", user_id)
        return
    except GigaChatException:
        logger.exception("background memory extraction: GigaChat request failed for user_id=%s", user_id)
        return
    logger.info("background memory extraction done: user_id=%s", user_id)
