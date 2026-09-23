"""Считает числовые части эталона для test_data (Вера Лисицына) напрямую по файлам.

Запуск: python competition_task/test_data/compute_reference.py
Точка отсчёта — четверг 17.09.2026 12:30 МСК.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
MSK = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 9, 17, 12, 30, tzinfo=MSK)


def load(name):
    return [json.loads(line) for line in open(HERE / name, encoding="utf-8") if line.strip()]


emails = load("emails_mock_vera.jsonl")
calendar = load("google_calendar_mock_vera.jsonl")
telegram = load("telegram_mock_vera.jsonl")


def rub(n):
    return f"{round(n):,}".replace(",", " ") + " ₽"


# ---------------------------------------------------------------- финансы
bank = []
for e in emails:
    if e["from"][0]["email"] != "notify@nevabank.ru":
        continue
    b = e["body"]
    kind, amount = re.match(r"(Зачисление|Списание|Покупка): ([\d ]+) ₽", b).groups()
    cat = re.search(r"Категория: (.+)", b)
    merchant = re.search(r"(?:Место|Отправитель): (.+)", b).group(1)
    bank.append({"date": e["date"][:10], "in": kind == "Зачисление", "amount": int(amount.replace(" ", "")),
                 "merchant": merchant, "cat": cat.group(1) if cat else "Доход"})

print("=== Финансы по месяцам (банковских уведомлений:", len(bank), ")")
by_month = defaultdict(lambda: {"in": 0, "out": 0, "cats": Counter(), "salary": 0})
for t in bank:
    m = by_month[t["date"][:7]]
    if t["in"]:
        m["in"] += t["amount"]
        if "Квант" in t["merchant"]:
            m["salary"] += t["amount"]
    else:
        m["out"] += t["amount"]
        m["cats"][t["cat"]] += t["amount"]
for month in sorted(by_month):
    b = by_month[month]
    print(f"{month}: доход {rub(b['in'])} (зарплата {rub(b['salary'])}), расход {rub(b['out'])}, остаток {rub(b['in'] - b['out'])}")
    print("    ", {k: v for k, v in sorted(b["cats"].items(), key=lambda kv: -kv[1])})
full = ["2026-05", "2026-06", "2026-07", "2026-08"]
nets = [by_month[m]["salary"] - by_month[m]["out"] for m in full]
print("средний остаток за май–август (только зарплата):", rub(sum(nets) / 4), nets)
for cat in ("Транспорт", "Кофейни", "Кафе и рестораны", "Спорт", "Хобби", "Подписки", "Продукты"):
    print(f"  {cat}:", {m: by_month[m]['cats'][cat] for m in sorted(by_month)})
taxi = Counter()
for t in bank:
    if "Яндекс Go" in t["merchant"]:
        taxi[t["date"][:7]] += t["amount"]
print("  такси:", dict(taxi))
print("последний баланс:", emails and [e for e in emails if e["from"][0]["email"] == "notify@nevabank.ru"][-1]["body"].split("Доступно: ")[1],
      "на", [t["date"] for t in bank][-1])
print("сентябрь 1–16: расход", rub(by_month["2026-09"]["out"]), "доход", rub(by_month["2026-09"]["in"]))

# ---------------------------------------------------------------- календарь
occ = []
for e in calendar:
    if e["status"] == "cancelled":
        continue
    s = e["start"]
    if "date" in s:
        occ.append({"summary": e["summary"], "all_day": True, "day": s["date"]})
        continue
    st = datetime.fromisoformat(s["dateTime"]).astimezone(MSK)
    en = datetime.fromisoformat(e["end"]["dateTime"]).astimezone(MSK)
    occ.append({"summary": e["summary"], "all_day": False, "start": st, "end": en, "loc": e.get("location"),
                "imp": e.get("extendedProperties", {}).get("private", {}).get("importance"),
                "att": [a["email"] for a in e.get("attendees", []) if not a.get("self")], "desc": e.get("description", "")})
timed = sorted([o for o in occ if not o["all_day"]], key=lambda o: o["start"])


def day_events(d):
    return [o for o in timed if o["start"].date() == d]


print("\n=== Завтра 18.09")
for o in day_events(date(2026, 9, 18)):
    print(f"  {o['start']:%H:%M}–{o['end']:%H:%M} {o['summary']} | {o['loc']} | {o['imp']} | {o['att']}")


def free_slots(d, start_h, end_h, min_minutes, after=None):
    lo = datetime.combine(d, time(start_h), MSK)
    if after:
        lo = max(lo, datetime.combine(d, after, MSK))
    hi = datetime.combine(d, time(end_h), MSK)
    busy = sorted((max(o["start"], lo), min(o["end"], hi)) for o in day_events(d) if o["end"] > lo and o["start"] < hi)
    slots, cur = [], lo
    for a, b in busy:
        if a > cur and (a - cur) >= timedelta(minutes=min_minutes):
            slots.append((cur, a))
        cur = max(cur, b)
    if hi > cur and (hi - cur) >= timedelta(minutes=min_minutes):
        slots.append((cur, hi))
    return slots


week = [date(2026, 9, 21) + timedelta(days=i) for i in range(5)]
print("\n=== Следующая неделя")
for d in week:
    evs = day_events(d)
    load_10_19 = sum((min(o["end"], datetime.combine(d, time(19), MSK)) - max(o["start"], datetime.combine(d, time(10), MSK))).seconds // 60
                     for o in evs if o["end"] > datetime.combine(d, time(10), MSK) and o["start"] < datetime.combine(d, time(19), MSK))
    print(f"{d:%a %d.%m}: занято {load_10_19} мин из 540 (10–19)")
    for o in evs:
        print(f"    {o['start']:%H:%M}–{o['end']:%H:%M} {o['summary']} [{o['imp']}]")
    fmt = lambda sl: ", ".join(f"{a:%H:%M}–{b:%H:%M}" for a, b in sl) or "—"
    print("    окна ≥45 после 15:00 (до 19):", fmt(free_slots(d, 10, 19, 45, after=time(15))), "| (до 18):", fmt(free_slots(d, 9, 18, 45, after=time(15))))
    print("    окна ≥90 (10–19):", fmt(free_slots(d, 10, 19, 90)), "| (9–18):", fmt(free_slots(d, 9, 18, 90)))

print("\n=== Регулярные (RRULE)")
rr = Counter((e["summary"], tuple(e["recurrence"])) for e in calendar if e.get("recurrence"))
for (s, r), n in rr.items():
    print(" ", s, r, n)

# ---------------------------------------------------------------- почта за 3 дня
print("\n=== Входящие 14.09 00:00 – 17.09 12:30")
threads_answered = {e["thread_id"] for e in emails if e["folder"] == "SENT"}
for e in emails:
    if e["folder"] != "INBOX" or not ("2026-09-14" <= e["date"][:10] <= "2026-09-17") or e["from"][0]["email"] == "notify@nevabank.ru":
        continue
    print(f"  {e['date'][:16]} {e['from'][0]['email']:34} {e['subject'][:60]:60} ответ: {'да' if e['thread_id'] in threads_answered else 'нет'}")

# ---------------------------------------------------------------- рабочие контакты
print("\n=== Рабочие контакты (встречи + входящие письма + сообщения в рабочих чатах)")
score = Counter()
names = {}
for e in calendar:
    if e["status"] == "cancelled":
        continue
    for a in e.get("attendees", []):
        if not a.get("self"):
            score[a["email"]] += 1
            names[a["email"]] = a["displayName"]
for e in emails:
    f = e["from"][0]
    if f["email"].endswith(("kvantmobility.ru", "gorodpark.ru")) and e["folder"] == "INBOX" and not f["email"].startswith("hr@"):
        score[f["email"]] += 1
        names[f["email"]] = f["name"]
rev = {v: k for k, v in names.items()}
for m in telegram:
    if m["chat"] in ("chat_oleg", "chat_design_team") and not m["out"] and m["sender"] in rev:
        score[rev[m["sender"]]] += 1
for em, n in score.most_common(9):
    print(f"  {names[em]:22} {em:34} {n}")

print("\n=== Telegram: чаты", Counter(m["chat"] for m in telegram))
