"""Parse bank/payment notification emails into structured transactions.

Deliberately format-agnostic: the evaluation dataset can come from a
different bank with different wording, so detection relies on generic
signals (an operation keyword next to a money amount) rather than one
bank's template, and every field has a fallback.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

_NUM = (r"\d{1,3}(?:[   ]\d{3})+(?:[.,]\d{1,2})?"  # 190 000 / 190 000,50
        r"|\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?"        # 190,000 / 1,250.50 (comma = thousands)
        r"|\d+(?:[.,]\d{1,2})?")
_CUR = r"₽|руб\.?(?:лей|ля|ль)?|р\.|rub\b|RUB\b|\$|USD\b|€|EUR\b"
AMOUNT_RE = re.compile(rf"(?P<sign>[+\-−–])?\s?(?P<num>{_NUM})\s?(?P<cur>{_CUR})", re.IGNORECASE)
AMOUNT_PREFIX_RE = re.compile(rf"(?P<cur>{_CUR})\s?(?P<num>{_NUM})", re.IGNORECASE)

EXPENSE_WORDS = (
    "списание", "списано", "покупка", "оплата", "оплачено", "платёж", "платеж", "снятие",
    "перевод на", "перевод получателю", "исходящий перевод", "автоплатёж", "автоплатеж",
    "debit", "purchase", "payment", "withdrawal", "charged",
)
INCOME_WORDS = (
    "зачисление", "зачислено", "поступление", "пополнение", "входящий перевод", "перевод от",
    "возврат", "кэшбэк", "cashback", "refund", "credit", "deposit", "received",
)
_NOT_A_TRANSACTION = (
    "скидк", "промокод", "акци", "распродаж", "дарим", "выиграй", "предложени", "кредитн лимит",
    "одобрен", "unsubscribe", "отписаться",
)

_BALANCE_SPLIT_RE = re.compile(r"(?i)\b(?:баланс|остаток|доступно|balance|available|лимит)")
_BALANCE_RE = re.compile(rf"(баланс|остаток|доступно|balance|available)\s*[:\-]?\s*(?:{_CUR})?\s?({_NUM})", re.IGNORECASE)
_CATEGORY_RE = re.compile(r"(категория|category|mcc[- ]?категория)\s*[:\-]\s*(.+)", re.IGNORECASE)
_MERCHANT_RE = re.compile(
    r"(место|магазин|получатель|отправитель|merchant|описание|назначение|торговая точка|организация)\s*[:\-]\s*(.+)",
    re.IGNORECASE,
)
_DATETIME_RES = [
    (re.compile(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}(?::\d{2})?)"), "iso"),
    (re.compile(r"(\d{2}\.\d{2}\.\d{4})[ ,в]+(\d{2}:\d{2}(?::\d{2})?)"), "ru"),
    (re.compile(r"(\d{2}\.\d{2}\.\d{2})[ ,в]+(\d{2}:\d{2})"), "ru_short"),
]
_CARD_TAIL_RE = re.compile(r"(?:карт\w*|card|счёт|счет)\D{0,6}(?:•+|\*+|x+|х+)?\s?(\d{4})\b", re.IGNORECASE)

MERCHANT_CATEGORY_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("Кофейни", ("кофе", "coffee", "kofe", "кофейн", "даблби", "cofix", "starbucks", "шоколадниц", "surf", "дринкит", "one price")),
    ("Кафе и рестораны", ("ресторан", "кафе", "бар ", "паб", "пицц", "суши", "бургер", "burger", "kfc", "вкусно — и точка",
                          "вкусно и точка", "теремок", "додо", "grill", "гриль", "бистро", "столов", "delivery club", "яндекс еда")),
    ("Продукты", ("пятёрочка", "пятерочка", "pyaterochka", "perekrestok", "vkusvill", "magnit", "auchan", "lenta",
                  "перекрёсток", "перекресток", "вкусвилл", "магнит", "ашан", "лента", "дикси",
                  "азбука вкуса", "самокат", "глобус", "метро кэш", "продукт", "супермаркет", "гипермаркет")),
    ("Транспорт", ("такси", "taxi", "метро", "мцд", "транспорт", "тройка", "яндекс go", "uber", "ситимобил", "каршеринг",
                   "делимобиль", "белкакар", "азс", "лукойл", "газпромнефть", "роснефть", "аэроэкспресс", "ржд")),
    ("Связь", ("мтс", "билайн", "мегафон", "tele2", "теле2", "ростелеком", "интернет", "мобильная связь", "связь")),
    ("Подписки", ("подписк", "яндекс плюс", "netflix", "spotify", "icloud", "apple.com", "кинопоиск", "okko", "ivi", "youtube")),
    ("ЖКХ", ("жкх", "коммунал", "мосэнерго", "электроэнерг", "водоканал", "управляющая компания", "квартплат")),
    ("Жильё", ("аренда", "квартир", "ипотек")),
    ("Покупки", ("ozon", "озон", "wildberries", "вайлдберриз", "яндекс маркет", "aliexpress", "lamoda", "мвидео", "днс", "dns")),
    ("Спорт", ("фитнес", "fitness", "спорт", "зал", "бассейн", "energy loft", "world class", "x-fit", "ddx")),
    ("Здоровье", ("аптек", "клиник", "стоматолог", "медицин", "анализ", "инвитро", "гемотест")),
    ("Кредит", ("кредит", "займ", "погашение")),
    ("Развлечения", ("кино", "театр", "концерт", "билет", "музей")),
    ("Путешествия", ("авиа", "aeroflot", "аэрофлот", "s7", "победа", "отель", "hotel", "booking", "островок")),
]
INCOME_CATEGORY_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("Зарплата", ("заработная плата", "зарплата", "зп ", "аванс", "salary", "payroll")),
    ("Возврат", ("возврат", "refund", "кэшбэк", "cashback")),
    ("Подработка/проекты", ("договор", "гпх", "проект", "фриланс", "услуг", "вознагражден")),
]


def _to_float(num: str) -> float:
    num = re.sub(r"[\s  ]", "", num)
    # "190,000" / "1,250.50": comma followed by exactly three digits groups thousands.
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?", num):
        return float(num.replace(",", ""))
    return float(num.replace(",", "."))


def _classify_direction(text_lower: str) -> str | None:
    exp = min((text_lower.find(w) for w in EXPENSE_WORDS if w in text_lower), default=-1)
    inc = min((text_lower.find(w) for w in INCOME_WORDS if w in text_lower), default=-1)
    if exp < 0 and inc < 0:
        return None
    if exp < 0:
        return "income"
    if inc < 0:
        return "expense"
    return "expense" if exp <= inc else "income"


def _operation_amount(lines: list[str], subject: str) -> tuple[float, str] | None:
    candidates = [subject, *lines]
    keyword_lines = [
        line for line in candidates
        if any(w in line.lower() for w in (*EXPENSE_WORDS, *INCOME_WORDS, "сумма", "amount"))
    ]
    for line in (*keyword_lines, *candidates):
        line = _BALANCE_SPLIT_RE.split(line)[0]
        match = AMOUNT_RE.search(line) or AMOUNT_PREFIX_RE.search(line)
        if match:
            try:
                return _to_float(match.group("num")), match.group("cur")
            except ValueError:
                continue
    return None


def _guess_category(text: str, hints: list[tuple[str, tuple[str, ...]]], default: str) -> str:
    low = f" {text.lower()} "
    for category, words in hints:
        if any(w in low for w in words):
            return category
    return default


def _clean_merchant(value: str) -> str:
    value = re.split(r"\s\d{1,2}[./]\d{1,2}[./]\d{2,4}|\s\d{4}-\d{2}-\d{2}|\.\s|;|\s(?:баланс|остаток|доступно)", value, flags=re.I)[0]
    value = value.strip(" .,")
    if value.count("«") != value.count("»"):
        value = value.strip("«»")
    return value.strip("\"'")


def _is_meta_segment(segment: str) -> bool:
    low = segment.lower()
    return bool(
        not segment.strip()
        or re.search(r"баланс|остаток|доступно|категор|карт|card|\d{2}:\d{2}|\d{2}\.\d{2}\.\d{2}", low)
        or AMOUNT_RE.search(segment)
        or any(w in low for w in (*EXPENSE_WORDS, *INCOME_WORDS))
    )


def _merchant(lines: list[str]) -> str | None:
    for line in lines:
        match = _MERCHANT_RE.search(line)
        if match:
            return match.group(2).strip()
    for i, line in enumerate(lines):
        if AMOUNT_RE.search(line) and any(w in line.lower() for w in (*EXPENSE_WORDS, *INCOME_WORDS)):
            for nxt in lines[i + 1:i + 3]:
                if not re.search(r"(?i)баланс|остаток|доступно|категор|^карт|^card|\d{1,2}:\d{2}", nxt) and not AMOUNT_RE.search(nxt):
                    return nxt.strip()
    for line in lines:
        if not AMOUNT_RE.search(line):
            continue
        match = re.search(r"\b(?:в|у|от|from|at)\s+([«\"'A-ZА-ЯЁ][^,\n]{2,60})", line)
        if match:
            return _clean_merchant(match.group(1))
        # One-line formats: "Покупка, карта *5521. 890 RUB. PYATEROCHKA."
        segments = [s.strip() for s in re.split(r"[.;|]\s+|,\s+", line)]
        for segment in segments:
            if not _is_meta_segment(segment) and len(segment) >= 3:
                return _clean_merchant(segment)
    return None


def _datetime(text: str, fallback: str | None) -> str | None:
    for regex, kind in _DATETIME_RES:
        match = regex.search(text)
        if not match:
            continue
        day, clock = match.group(1), match.group(2)
        try:
            if kind == "iso":
                return datetime.fromisoformat(f"{day}T{clock}").isoformat()
            fmt = "%d.%m.%Y" if kind == "ru" else "%d.%m.%y"
            parsed_day = datetime.strptime(day, fmt).date()
            return datetime.fromisoformat(f"{parsed_day.isoformat()}T{clock}").isoformat()
        except ValueError:
            continue
    return fallback


def parse_bank_notification(subject: str, body: str, email_date: str | None) -> dict[str, Any] | None:
    """Return {kind, amount, currency, merchant, category, datetime, balance,
    card_tail} for a transaction notification, or None if it isn't one."""
    subject = subject or ""
    body = body or ""
    text = f"{subject}\n{body}"
    low = text.lower()

    if any(w in low for w in _NOT_A_TRANSACTION) and not re.search(r"(списание|зачисление|покупка|оплата)\s*[:\-]", low):
        return None
    if not (AMOUNT_RE.search(text) or AMOUNT_PREFIX_RE.search(text)):
        return None

    head = f"{subject}\n" + "\n".join(body.splitlines()[:4])
    kind = _classify_direction(head.lower()) or _classify_direction(low)
    if kind is None:
        return None
    # A transaction notification is short and structured; long prose that
    # merely mentions a payment ("оплата по договору до пятницы") is not one.
    if len(body) > 1500:
        return None
    operation_line = re.search(
        r"(списан|зачисл|покупк|оплат|платёж|платеж|снят|пополн|поступ|перевод|debit|credit|purchase|payment)\w*"
        rf"[^\n]{{0,60}}?[+\-−–]?\s?(?:{_CUR})?\s?(?:{_NUM})\s?(?:{_CUR})?",
        low,
        re.IGNORECASE,
    )
    if not operation_line:
        return None
    structural = (
        _CARD_TAIL_RE.search(text) is not None
        or _BALANCE_RE.search(body) is not None
        or _CATEGORY_RE.search(body) is not None
        or re.search(r"(списан|зачисл|покупк|оплат|поступ|пополн|перевод)\w*[^\n]{0,20}\d", subject.lower()) is not None
    )
    if not structural:
        return None

    lines = [line.strip() for line in body.splitlines() if line.strip()]
    amount = _operation_amount(lines, subject)
    if amount is None or amount[0] <= 0:
        return None
    value, currency = amount

    merchant = _merchant(lines)
    category_match = _CATEGORY_RE.search(body)
    if category_match:
        category = category_match.group(2).strip()
    elif kind == "income":
        category = _guess_category(f"{merchant or ''} {body}", INCOME_CATEGORY_HINTS, "Прочие поступления")
    else:
        category = _guess_category(f"{merchant or ''} {body}", MERCHANT_CATEGORY_HINTS, "Прочее")

    balance_match = _BALANCE_RE.search(body)
    card_match = _CARD_TAIL_RE.search(text)
    cur = currency.lower()
    currency_code = "USD" if "$" in cur or "usd" in cur else "EUR" if "€" in cur or "eur" in cur else "RUB"

    return {
        "kind": kind,
        "amount": value,
        "currency": currency_code,
        "merchant": merchant,
        "category": category,
        "datetime": _datetime(body, email_date),
        "balance": _to_float(balance_match.group(2)) if balance_match else None,
        "card_tail": card_match.group(1) if card_match else None,
    }
