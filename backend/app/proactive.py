"""Proactive findings: reasons to act that the assistant notices in the
user's own data without being asked, each paired with a concrete next
step through an integration (draft reply, calendar event, budget).

Detectors are deterministic, so the same data always yields the same
suggestions; the agent (tool `proactive_suggestions`) and the start
screen (GET /api/proactive) share them.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import Any

from . import personal_analytics as analytics
from .personal_analytics import WEEKDAYS_RU
from .personal_data import now_local, user_timezone

logger = logging.getLogger(__name__)

_PRIORITY = {"high": 0, "medium": 1, "low": 2}


def _item(priority: str, kind: str, title: str, details: str, action: str, prompt: str, source: str) -> dict[str, Any]:
    return {"priority": priority, "kind": kind, "title": title, "details": details,
            "suggested_action": action, "prompt": prompt, "source": source}


def _email_items(user_id: str) -> list[dict[str, Any]]:
    items = []
    inbox = analytics.inbox_overview(user_id, period="last_7_days")
    for e in inbox["emails"]:
        sender = e["from"].split("<")[0].strip() or e["from"]
        if e.get("security_warning"):
            items.append(_item(
                "high", "security", f"Подозрительное письмо: «{e['subject']}»",
                e["security_warning"].split(".")[0] + f". Отправитель {e['from']}.",
                "Не отвечать и не переходить по ссылкам; при необходимости связаться с отправителем по известному каналу.",
                f"Разбери подозрительное письмо «{e['subject']}» — что в нём не так?", f"почта, {e['date'][:10]}",
            ))
            continue
        deadline = e.get("deadline") or {}
        if e["user_replied"] or e["thread_already_answered_by_user_earlier"]:
            continue
        if deadline.get("status") in ("просрочено", "сегодня"):
            when = "срок был " + deadline["deadline_date"] if deadline["status"] == "просрочено" else "срок сегодня"
            items.append(_item(
                "high", "email", f"Нет ответа: «{e['subject']}» от {sender}", f"{when}; просьба: {e['snippet'][:140]}",
                "Подготовить черновик ответа (отправка — только после вашего подтверждения).",
                f"Подготовь черновик ответа на письмо «{e['subject']}» от {sender}", f"почта, {e['date'][:10]}",
            ))
    return items


def _place(location: str) -> str:
    """Address without the venue suffix, lowercased — two rooms in one office are one place."""
    parts = [p.strip().lower() for p in location.split(",") if p.strip()]
    return ", ".join(parts[:3])


def _calendar_items(user_id: str) -> list[dict[str, Any]]:
    items = []
    tz = user_timezone(user_id)
    now = now_local(user_id)
    tomorrow = now.date() + timedelta(days=1)
    occ = analytics._occurrences(user_id, datetime.combine(tomorrow, time(0), tz),
                                 datetime.combine(tomorrow + timedelta(days=1), time(0), tz))
    timed = [o for o in occ if not o["all_day"] and o["busy"]]
    for a, b in zip(timed, timed[1:]):
        if b["start"] < a["end"]:
            items.append(_item(
                "high", "calendar", f"Накладка завтра: «{a['summary']}» и «{b['summary']}»",
                f"{a['start']:%H:%M}–{a['end']:%H:%M} пересекается с {b['start']:%H:%M}–{b['end']:%H:%M}.",
                "Перенести одну из встреч — подготовлю изменение в календаре на подтверждение.",
                f"Завтра накладка между «{a['summary']}» и «{b['summary']}» — что лучше перенести?", "календарь",
            ))
    # Back-to-back meetings at different addresses: not enough time to get there.
    for a, b in zip(timed, timed[1:]):
        loc_a, loc_b = (a["location"] or "").strip(), (b["location"] or "").strip()
        gap = (b["start"] - a["end"]).total_seconds() / 60
        if loc_a and loc_b and _place(loc_a) != _place(loc_b) and 0 <= gap < 30:
            items.append(_item(
                "high", "travel", f"Завтра можно не успеть доехать: «{a['summary']}» → «{b['summary']}»",
                f"{a['end']:%H:%M} заканчивается на {loc_a}, в {b['start']:%H:%M} начало на {loc_b} — на дорогу {int(gap)} мин.",
                "Проверить маршрут и предупредить участников / сдвинуть вторую встречу — изменение только после подтверждения.",
                f"Успею ли я завтра с «{a['summary']}» на «{b['summary']}»?", "календарь",
            ))
    for o in timed:
        if (o["importance"] or "") == "critical":
            items.append(_item(
                "medium", "calendar", f"Завтра важное: «{o['summary']}» в {o['start']:%H:%M}",
                "Событие с критичной важностью.",
                "Напомнить заранее и проверить, всё ли готово (материалы, участники).",
                f"Что мне подготовить к «{o['summary']}» завтра?", "календарь",
            ))

    week = analytics.schedule_analysis(user_id, period="next_week")
    busiest = week.get("busiest_day")
    if busiest and busiest["load_percent"] >= 70 and busiest.get("reschedule_candidates"):
        movable = ", ".join(f"«{c['summary']}» ({c['time']})" for c in busiest["reschedule_candidates"][:2])
        items.append(_item(
            "medium", "calendar", f"Перегруз: {busiest['weekday']} {busiest['date']} занят на {busiest['load_percent']}%",
            f"Можно разгрузить: {movable}.",
            "Предложить перенос необязательных событий — изменения в календаре только после подтверждения.",
            "Помоги разгрузить самый загруженный день следующей недели", "календарь",
        ))

    # Regular activities known only from email (e.g. trainings) vs. the calendar.
    for pattern in analytics.recurring_schedule(user_id)["email_patterns"]:
        if pattern["is_newsletter"] or not pattern["weekdays_mentioned"] or not pattern["times_mentioned"]:
            continue
        clock = next(iter(pattern["times_mentioned"]))
        hour, minute = (int(x) for x in clock.split(":"))
        for offset in range(1, 8):
            day = now.date() + timedelta(days=offset)
            if WEEKDAYS_RU[day.weekday()] not in pattern["weekdays_mentioned"]:
                continue
            start = datetime.combine(day, time(hour, minute), tz)
            end = start + timedelta(hours=1)
            clash = [o for o in analytics._occurrences(user_id, start, end) if o["busy"] and not o["all_day"]]
            if clash:
                items.append(_item(
                    "high", "routine",
                    f"{WEEKDAYS_RU[day.weekday()].capitalize()} {day:%d.%m} в {clock} — обычное время: «{pattern['subject_example']}»",
                    f"В календаре на это время стоит «{clash[0]['summary']}».",
                    "Перенести встречу или найти другое окно для регулярной активности.",
                    f"У меня {day:%d.%m} в {clock} пересекается «{clash[0]['summary']}» с моим обычным расписанием — что делать?",
                    f"почта ({pattern['sender']}) и календарь",
                ))
    return items


def _finance_items(user_id: str) -> list[dict[str, Any]]:
    items = []
    cash = analytics.monthly_cashflow(user_id)
    complete = cash.get("complete_months_used_for_average") or []
    if len(complete) >= 3:
        last, previous = complete[-1], complete[:-1]
        report = analytics.finance_report(user_id, date_from=f"{previous[0]}-01", date_to=f"{last}-31")
        by_month = report["by_month_and_category"]
        categories = {c for m in by_month.values() for c in m}
        spikes = []
        for category in sorted(categories):
            present = [m for m in previous if by_month.get(m, {}).get(category, 0) > 0]
            base = sum(by_month.get(m, {}).get(category, 0) for m in previous) / len(previous)
            current = by_month.get(last, {}).get(category, 0)
            # A regular category (spent in most previous months) that jumped —
            # not a one-off purchase in a category seen once before.
            if len(present) >= max(2, len(previous) - 1) and base > 0 and current >= 1.8 * base and current - base >= 2000:
                spikes.append((current - base, category, base, current))
        for _, category, base, current in sorted(spikes, reverse=True):
                items.append(_item(
                    "medium", "finance", f"Траты на «{category}» выросли в {current / base:.1f} раза",
                    f"{last}: {current:,.0f} ₽ против среднего {base:,.0f} ₽ за {previous[0]}–{previous[-1]}.".replace(",", " "),
                    "Поставить месячный лимит на категорию и напоминание в календаре.",
                    f"Почему выросли мои траты на «{category}» и как их ограничить?", "банковские уведомления",
                ))
        latest = cash["months"][-1] if cash["months"] else None
        avg_net = cash["average_per_complete_month"]["net"]
        if latest and latest["complete_month"] and avg_net and latest["net"] < 0.7 * avg_net:
            items.append(_item(
                "medium", "finance", f"В {latest['month']} удалось отложить меньше обычного",
                f"Остаток {latest['net']:,.0f} ₽ при среднем {avg_net:,.0f} ₽.".replace(",", " "),
                "Разобрать, на что ушло больше, и скорректировать план накоплений.",
                "Почему в прошлом месяце я отложила меньше обычного?", "банковские уведомления",
            ))
    for source in cash.get("one_off_or_new_income", []):
        payment = source["payments"][-1]
        items.append(_item(
            "low", "finance", f"Новый доход: {source['source']} (+{payment['amount']:,.0f} ₽)".replace(",", " "),
            f"Поступление {payment['date']}, раньше такого источника не было.",
            "Сразу отложить часть в накопления — могу поставить ежемесячное напоминание.",
            "У меня появился новый доход — сколько из него разумно отложить?", "банковские уведомления",
        ))
    return items


def scan(user_id: str, limit: int = 6) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for detector in (_email_items, _calendar_items, _finance_items):
        try:
            items.extend(detector(user_id))
        except Exception:  # noqa: BLE001 — one detector failing must not hide the others
            logger.exception("proactive detector %s failed for user_id=%s", detector.__name__, user_id)
    items.sort(key=lambda i: _PRIORITY[i["priority"]])
    # Diverse first: the best item of every kind (email, security, calendar,
    # travel, routine, finance) before a second one of any kind — otherwise
    # four unanswered emails hide the calendar clash and the budget spike.
    picked, seen_kinds = [], set()
    for item in items:
        if item["kind"] not in seen_kinds:
            picked.append(item)
            seen_kinds.add(item["kind"])
    picked += [i for i in items if i not in picked]
    picked = sorted(picked[:limit], key=lambda i: _PRIORITY[i["priority"]])
    return {"now": now_local(user_id).isoformat(), "count": len(items), "suggestions": picked}
