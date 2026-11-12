"""Computes the numeric parts of the data2 reference (Nikita Orlov) directly from the files.

Run: python competition_task/data2/compute_reference.py
Reference moment: Thursday 17.09.2026 12:30 MSK (same as --now in eval_reference.py).
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

HERE = Path(__file__).parent
NOW = datetime(2026, 9, 17, 12, 30)
USER = "n.orlov@northstarlabs.ru"
WORK_START, WORK_END = time(9, 0), time(18, 0)


def load(name):
    return [json.loads(line) for line in open(HERE / name, encoding="utf-8") if line.strip()]


emails = load("emails_mock_nikita.jsonl")
calendar = load("google_calendar_mock_nikita.jsonl")
telegram = load("telegram_mock_nikita.jsonl")


def dt(value):
    return datetime.fromisoformat(value).replace(tzinfo=None)


def rub(n):
    return f"{n:,}".replace(",", " ") + " ₽"


# ---------------------------------------------------------------- finances
bank = []
for e in emails:
    if "alpha-bank" not in e["from"][0]["email"]:
        continue
    body = e["body"]
    m = re.search(r"(Зачисление|Списание): ([\d ]+)", body)
    amount = int(m.group(2).replace(" ", ""))
    lines = body.split("\n")
    category = re.search(r"Категория: (.+)", body)
    bank.append({
        "date": e["date"][:10], "kind": m.group(1), "amount": amount,
        "merchant": lines[2].strip(), "category": category.group(1).strip() if category else "Доход",
    })

print("=== Финансы по месяцам")
by_month = defaultdict(lambda: {"income": 0, "expense": 0, "cats": Counter()})
for t in bank:
    month = t["date"][:7]
    if t["kind"] == "Зачисление":
        by_month[month]["income"] += t["amount"]
    else:
        by_month[month]["expense"] += t["amount"]
        by_month[month]["cats"][t["category"]] += t["amount"]
for month in sorted(by_month):
    b = by_month[month]
    print(month, "доход", rub(b["income"]), "расход", rub(b["expense"]), "остаток", rub(b["income"] - b["expense"]))
    cafe = b["cats"]["Кофейни"] + b["cats"]["Кафе и рестораны"]
    print("   кафе+кофейни", rub(cafe), "| спорт", rub(b["cats"]["Спорт"]), "| образование", rub(b["cats"]["Образование"]),
          "| категории", dict(b["cats"]))
salary_only = [by_month[m]["income"] - by_month[m]["expense"] - (35000 if m == "2026-08" else 0) for m in sorted(by_month)]
print("средний остаток без подработки:", rub(round(sum(salary_only) / len(salary_only))))
print("средний остаток с подработкой:", rub(round(sum(by_month[m]["income"] - by_month[m]["expense"] for m in by_month) / len(by_month))))
print("последняя банковская операция:", max(t["date"] for t in bank))

# ------------------------------------------------------------- calendar
events = []
for e in calendar:
    s, en = e["start"], e["end"]
    if "dateTime" not in s:
        events.append({"summary": e["summary"], "all_day": True, "day": s["date"], "type": e.get("eventType")})
        continue
    events.append({
        "summary": e["summary"], "all_day": False, "start": dt(s["dateTime"]), "end": dt(en["dateTime"]),
        "transparent": e.get("transparency") == "transparent", "type": e.get("eventType"),
        "attendees": [a["email"] for a in e.get("attendees") or [] if not a.get("self")],
        "importance": (e.get("extendedProperties") or {}).get("private", {}).get("importance"),
        "location": e.get("location"),
    })
timed = [e for e in events if not e["all_day"] and not e["transparent"]]


def day_events(d):
    return sorted((e for e in timed if e["start"].date() == d), key=lambda e: e["start"])


def free_windows(d, min_minutes, not_before=WORK_START):
    cursor = datetime.combine(d, not_before)
    end_of_day = datetime.combine(d, WORK_END)
    out = []
    for e in day_events(d):
        if e["end"] <= cursor:
            continue
        if e["start"] > cursor and (e["start"] - cursor).seconds / 60 >= min_minutes:
            out.append((cursor, e["start"]))
        cursor = max(cursor, e["end"])
    if end_of_day > cursor and (end_of_day - cursor).seconds / 60 >= min_minutes:
        out.append((cursor, end_of_day))
    return out


next_week = [date(2026, 9, 21) + timedelta(days=i) for i in range(5)]
print("\n=== Окна ≥40 мин после 11:00, следующая неделя")
for d in next_week:
    print(d, ", ".join(f"{a:%H:%M}–{b:%H:%M}" for a, b in free_windows(d, 40, time(11, 0))) or "—")

print("\n=== Загрузка дней следующей недели (минуты встреч в 9–18, с обедом / без обеда)")
for d in next_week:
    evs = day_events(d)
    total = sum((e["end"] - e["start"]).seconds // 60 for e in evs)
    no_lunch = sum((e["end"] - e["start"]).seconds // 60 for e in evs if e["summary"] != "Обед")
    no_focus = sum((e["end"] - e["start"]).seconds // 60 for e in evs if e["summary"] != "Обед" and e["type"] != "focusTime")
    print(d, "всего", total, "без обеда", no_lunch, "без обеда и фокус-времени", no_focus)

print("\n=== Окна ≥90 мин в 9–18 на следующей неделе")
for d in next_week:
    print(d, ", ".join(f"{a:%H:%M}–{b:%H:%M}" for a, b in free_windows(d, 90)) or "—")

print("\n=== Завтра 18.09")
for e in [x for x in events if x["all_day"] and x["day"] == "2026-09-18"]:
    print("весь день:", e["summary"])
for e in day_events(date(2026, 9, 18)):
    print(f"{e['start']:%H:%M}–{e['end']:%H:%M}", e["summary"], e["attendees"], e["location"] or "", e["importance"])

print("\n=== Дейли по датам (есть ли на следующей неделе)")
print(sorted({e["start"].date().isoformat() for e in timed if e["summary"].startswith("Дейли")}))

# --------------------------------------------------------- work contacts
print("\n=== Рабочие контакты: встречи + входящие письма + сообщения в рабочих чатах")
names = {}
for e in emails:
    names[e["from"][0]["email"]] = e["from"][0]["name"]
score = Counter()
meetings = Counter()
for e in timed:
    for a in e["attendees"]:
        meetings[a] += 1
incoming = Counter(e["from"][0]["email"] for e in emails
                   if e["from"][0]["email"] != USER and "northstarlabs.ru" in e["from"][0]["email"]
                   or e["from"][0]["email"].endswith("finmarket.ru"))
work_chats = {"chat_boss", "chat_data_team", "chat_hr"}
tg_by_name = Counter(m["sender"] for m in telegram if m["chat"] in work_chats and not m["out"])
email_by_name = {
    "Евгений Ларин": "e.larin@northstarlabs.ru", "София Зимина": "s.zimina@northstarlabs.ru",
    "Максим Чернов": "m.chernov@northstarlabs.ru", "Руслан Громов": "r.gromov@northstarlabs.ru",
    "Дарья Белова": "d.belova@northstarlabs.ru", "Павел Осипов": "p.osipov@northstarlabs.ru",
    "Ирина Соколова": "i.sokolova@northstarlabs.ru", "Марина Кравцова": "m.kravtsova@finmarket.ru",
    "Елена Громова": "e.gromova@northstarlabs.ru", "Ольга Журавлёва": "o.zh@northstarlabs.ru",
}
rows = []
for name, addr in email_by_name.items():
    rows.append((meetings[addr] + incoming[addr] + tg_by_name[name], name, meetings[addr], incoming[addr], tg_by_name[name]))
for total, name, m, i, t in sorted(rows, reverse=True):
    print(f"{name}: {total} (встречи {m}, письма {i}, telegram {t})")

# ---------------------------------------------------------------- inbox
print("\n=== Входящие 14–17.09 (до 12:30 17.09)")
# Dataset trap: Nikita's replies sit in INBOX, are addressed to himself and
# have their OWN thread_id, so a reply can be linked to its email only by the "Re: …" subject.
replied_subjects = {e["subject"][4:].strip() for e in emails
                    if e["from"][0]["email"] == USER and e["subject"].startswith("Re:")}


def is_replied(e):
    return e["subject"].strip() in replied_subjects
for e in sorted(emails, key=lambda e: e["date"]):
    d = dt(e["date"])
    if not (datetime(2026, 9, 14) <= d <= NOW) or e["from"][0]["email"] == USER or "alpha-bank" in e["from"][0]["email"]:
        continue
    print(f"{d:%d.%m %H:%M}", e["from"][0]["name"], "|", e["subject"], "| отвечено" if is_replied(e) else "| НЕ отвечено")

print("\n=== Все письма без ответа с просьбой/дедлайном (любая дата до NOW)")
for e in sorted(emails, key=lambda e: e["date"]):
    if e["from"][0]["email"] == USER or is_replied(e):
        continue
    if re.search(r"до |нужн|пришли|подтверд|можешь", e["body"], re.IGNORECASE) and "alpha-bank" not in e["from"][0]["email"]:
        print(e["date"][:16], e["from"][0]["name"], "|", e["subject"])
