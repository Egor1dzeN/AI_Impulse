from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from typing import Any

import requests
from langchain_core.tools import BaseTool, tool

from . import db, memory_bridge

MEMORY_TOOL_NAME = "search_memory"

# ============================================================
# TOPIC 1 — CALENDAR & TASKS
# ============================================================


def create_task_impl(user_id: str, title: str, due_date: str | None = None, notes: str | None = None) -> dict[str, Any]:
    row_id = db.new_id()
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO tasks (id, user_id, title, due_date, notes, done, created_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?)",
            (row_id, user_id, title, due_date, notes, db.now_iso()),
        )
    return {"id": row_id, "title": title, "due_date": due_date, "notes": notes}


def list_tasks_impl(user_id: str) -> dict[str, Any]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, due_date, notes, done, created_at FROM tasks "
            "WHERE user_id = ? ORDER BY created_at DESC LIMIT 20",
            (user_id,),
        ).fetchall()
    return {"tasks": [dict(row) for row in rows]}


# ============================================================
# TOPIC 2 — FINANCE & EXPENSES
# ============================================================


def add_expense_impl(user_id: str, amount: float, category: str, date: str, note: str | None = None) -> dict[str, Any]:
    row_id = db.new_id()
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO expenses (id, user_id, amount, category, note, date, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (row_id, user_id, amount, category, note, date, db.now_iso()),
        )
    return {"id": row_id, "amount": amount, "category": category, "date": date}


def get_expense_summary_impl(user_id: str) -> dict[str, Any]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT amount, category, date FROM expenses WHERE user_id = ?",
            (user_id,),
        ).fetchall()

    by_month: dict[str, float] = defaultdict(float)
    by_category: dict[str, float] = defaultdict(float)

    for row in rows:
        month = str(row["date"])[:7]
        by_month[month] += float(row["amount"])
        by_category[row["category"]] += float(row["amount"])

    return {
        "total": sum(by_month.values()),
        "by_month": dict(sorted(by_month.items())),
        "by_category": dict(sorted(by_category.items(), key=lambda kv: -kv[1])),
    }


# ============================================================
# TOPIC 3 — HEALTH & FITNESS
# ============================================================


def log_meal_impl(
    user_id: str,
    name: str,
    kcal: float,
    date: str,
    protein: float | None = None,
    fat: float | None = None,
    carbs: float | None = None,
) -> dict[str, Any]:
    row_id = db.new_id()
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO meals (id, user_id, name, kcal, protein, fat, carbs, date, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row_id, user_id, name, kcal, protein, fat, carbs, date, db.now_iso()),
        )
    return {"id": row_id, "name": name, "kcal": kcal, "date": date}


def log_weight_impl(user_id: str, value_kg: float, date: str) -> dict[str, Any]:
    row_id = db.new_id()
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO weights (id, user_id, value_kg, date, created_at) VALUES (?, ?, ?, ?, ?)",
            (row_id, user_id, value_kg, date, db.now_iso()),
        )
    return {"id": row_id, "value_kg": value_kg, "date": date}


DAILY_KCAL_GOAL = 2200


def get_health_summary_impl(user_id: str) -> dict[str, Any]:
    with db.get_conn() as conn:
        meal_rows = conn.execute(
            "SELECT name, kcal, protein, fat, carbs, date FROM meals WHERE user_id = ? ORDER BY date DESC",
            (user_id,),
        ).fetchall()
        weight_rows = conn.execute(
            "SELECT value_kg, date FROM weights WHERE user_id = ? ORDER BY date ASC",
            (user_id,),
        ).fetchall()

    by_day: dict[str, float] = defaultdict(float)
    for row in meal_rows:
        by_day[row["date"]] += float(row["kcal"])

    today = max(by_day.keys()) if by_day else None
    today_kcal = by_day.get(today, 0.0) if today else 0.0

    return {
        "daily_kcal_goal": DAILY_KCAL_GOAL,
        "today": today,
        "today_kcal": today_kcal,
        "kcal_left_today": max(0.0, DAILY_KCAL_GOAL - today_kcal),
        "kcal_by_day": dict(sorted(by_day.items())),
        "weight_history": [dict(row) for row in weight_rows],
    }


# ============================================================
# TOPIC 4 — MAPS & LOCATION
# ============================================================

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# Nominatim's usage policy requires a real identifying User-Agent, not the
# default python-requests one, or it may reject/throttle requests.
NOMINATIM_USER_AGENT = "ImpulseAssistant/1.0 (personal project; contact via GitHub)"


def geocode_location_impl(place: str) -> dict[str, Any]:
    resp = requests.get(
        NOMINATIM_URL,
        params={"q": place, "format": "json", "limit": 1},
        headers={"User-Agent": NOMINATIM_USER_AGENT},
        timeout=8,
    )
    resp.raise_for_status()
    results = resp.json()

    if not results:
        return {"found": False, "query": place}

    hit = results[0]
    south, north, west, east = (float(x) for x in hit["boundingbox"])

    return {
        "found": True,
        "query": place,
        "display_name": hit["display_name"],
        "lat": float(hit["lat"]),
        "lon": float(hit["lon"]),
        "bbox": f"{west},{south},{east},{north}",
    }


# ============================================================
# TOPIC 5 — WEATHER
# ============================================================

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather codes, as used by Open-Meteo. https://open-meteo.com/en/docs
_WEATHER_CODE_DESCRIPTIONS = {
    0: "ясно",
    1: "преимущественно ясно",
    2: "переменная облачность",
    3: "пасмурно",
    45: "туман",
    48: "изморозь",
    51: "лёгкая морось",
    53: "морось",
    55: "сильная морось",
    56: "лёгкая ледяная морось",
    57: "сильная ледяная морось",
    61: "небольшой дождь",
    63: "дождь",
    65: "сильный дождь",
    66: "лёгкий ледяной дождь",
    67: "сильный ледяной дождь",
    71: "небольшой снег",
    73: "снег",
    75: "сильный снег",
    77: "снежная крупа",
    80: "небольшой ливень",
    81: "ливень",
    82: "сильный ливень",
    85: "небольшой снегопад",
    86: "сильный снегопад",
    95: "гроза",
    96: "гроза с небольшим градом",
    99: "гроза с сильным градом",
}


def get_weather_impl(place: str) -> dict[str, Any]:
    # Reuses the same geocoder as show_map — one place name -> coords path.
    location = geocode_location_impl(place)
    if not location["found"]:
        return {"found": False, "query": place}

    resp = requests.get(
        OPEN_METEO_URL,
        params={
            "latitude": location["lat"],
            "longitude": location["lon"],
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
            "timezone": "auto",
        },
        timeout=8,
    )
    resp.raise_for_status()
    current = resp.json().get("current", {})

    return {
        "found": True,
        "place": location["display_name"],
        "temperature_c": current.get("temperature_2m"),
        "feels_like_c": current.get("apparent_temperature"),
        "humidity_percent": current.get("relative_humidity_2m"),
        "wind_speed_kmh": current.get("wind_speed_10m"),
        "description": _WEATHER_CODE_DESCRIPTIONS.get(current.get("weather_code"), "неизвестно"),
        "time": current.get("time"),
    }


# ============================================================
# TOPIC 6 — FINANCE & CURRENCY
# ============================================================

CBR_DAILY_URL = "https://www.cbr.ru/scripts/XML_daily.asp"
CBR_USER_AGENT = "ImpulseAssistant/1.0 (personal project; contact via GitHub)"


def get_exchange_rate_impl(currency: str) -> dict[str, Any]:
    """Today's official CBR (Central Bank of Russia) rate for `currency` vs
    RUB. No date param exposed to the LLM on purpose — always the latest
    published rate, one less argument for a small model to get wrong."""
    currency = currency.strip().upper()

    resp = requests.get(
        CBR_DAILY_URL,
        headers={"User-Agent": CBR_USER_AGENT},
        timeout=8,
    )
    resp.raise_for_status()

    # The feed is windows-1251, not UTF-8 — parsing raw bytes lets
    # ElementTree read the encoding from the XML prolog itself instead of
    # requests guessing (and getting it wrong).
    root = ET.fromstring(resp.content)
    rate_date = root.get("Date")

    for valute in root.findall("Valute"):
        char_code = valute.findtext("CharCode")
        if char_code and char_code.upper() == currency:
            return {
                "found": True,
                "currency": char_code,
                "name": valute.findtext("Name"),
                "nominal": int(valute.findtext("Nominal", "1")),
                "value_rub": float(valute.findtext("Value", "0").replace(",", ".")),
                "date": rate_date,
            }

    return {"found": False, "currency": currency, "date": rate_date}


# ============================================================
# LANGCHAIN TOOLS — bound to one user_id, handed to the LangGraph agent
# ============================================================
#
# Each request builds its own tool instances (via build_action_tools /
# build_memory_tool) closed over that request's user_id, since the raw
# functions above are keyed by user but the LLM must never see or fill in
# user_id itself. MCP-provided tools are plain LangChain BaseTool instances
# too, so agent_graph.py can merge them into the same flat list this module
# produces without any special-casing.


def build_action_tools(user_id: str) -> list[BaseTool]:
    """Task/expense/health tools exposed to the agent for one user."""

    @tool
    def create_task(title: str, due_date: str | None = None, notes: str | None = None) -> dict:
        """Создать задачу/напоминание с дедлайном. Действие на запись. due_date — YYYY-MM-DD, опционально."""
        return create_task_impl(user_id=user_id, title=title, due_date=due_date, notes=notes)

    @tool
    def list_tasks() -> dict:
        """Показать последние задачи пользователя."""
        return list_tasks_impl(user_id=user_id)

    @tool
    def add_expense(amount: float, category: str, date: str, note: str | None = None) -> dict:
        """Записать трату. Действие на запись. date — YYYY-MM-DD, используй сегодняшнюю дату, если пользователь не указал."""
        return add_expense_impl(user_id=user_id, amount=amount, category=category, date=date, note=note)

    @tool
    def get_expense_summary() -> dict:
        """Получить сводку трат по категориям и месяцам."""
        return get_expense_summary_impl(user_id=user_id)

    @tool
    def log_meal(
        name: str,
        kcal: float,
        date: str,
        protein: float | None = None,
        fat: float | None = None,
        carbs: float | None = None,
    ) -> dict:
        """Записать приём пищи с КБЖУ. Действие на запись. date — YYYY-MM-DD."""
        return log_meal_impl(
            user_id=user_id, name=name, kcal=kcal, date=date, protein=protein, fat=fat, carbs=carbs
        )

    @tool
    def log_weight(value_kg: float, date: str) -> dict:
        """Записать вес пользователя. Действие на запись. date — YYYY-MM-DD."""
        return log_weight_impl(user_id=user_id, value_kg=value_kg, date=date)

    @tool
    def get_health_summary() -> dict:
        """Получить сводку по калориям за сегодня и динамике веса."""
        return get_health_summary_impl(user_id=user_id)

    @tool
    def show_map(place: str) -> dict:
        """Показать место на карте прямо в чате. Вызывай ТОЛЬКО когда пользователь явно просит показать карту или спрашивает "где находится X" — простое упоминание места в разговоре (например "я живу в X") не повод её вызывать. place — только реальное название места. Если пользователь не назвал место явно (например "покажи на карте где я живу") — сначала вызови search_memory, чтобы узнать место из памяти, и передай сюда именно то, что она вернула, слово в слово. Никогда не придумывай и не дописывай место от себя — если в памяти его нет, не вызывай эту функцию, а спроси пользователя. После вызова не добавляй в ответ данные из других функций (погоду, курс и т.п.), если их не просили."""
        return geocode_location_impl(place)

    @tool
    def get_weather(place: str) -> dict:
        """Узнать текущую погоду в указанном месте. Вызывай ТОЛЬКО когда пользователь явно спрашивает о погоде ("какая погода", "холодно ли", "нужен зонт" и т.п.) — простое упоминание места в разговоре (например "я живу в X", "еду в X") не повод её вызывать. place — только реальное название места. Если пользователь спрашивает о погоде, но не назвал место явно (например "какая у меня погода") — сначала вызови search_memory, чтобы узнать место из памяти, и передай сюда именно то, что она вернула. Никогда не придумывай место от себя."""
        return get_weather_impl(place)

    @tool
    def get_exchange_rate(currency: str) -> dict:
        """Узнать официальный курс ЦБ РФ валюты к рублю на сегодня. Вызывай ТОЛЬКО когда пользователь явно спрашивает про курс/сколько стоит валюта — простое упоминание валюты в разговоре (например "зарплата в долларах") не повод её вызывать. currency — трёхбуквенный код валюты (например USD, EUR, CNY)."""
        return get_exchange_rate_impl(currency)

    return [
        create_task,
        list_tasks,
        add_expense,
        get_expense_summary,
        log_meal,
        log_weight,
        get_health_summary,
        show_map,
        get_weather,
        get_exchange_rate,
    ]


def build_memory_tool(user_id: str) -> BaseTool:
    """The memory-retriever tool: the agent decides on its own, per query,
    whether to call this (and how many times) before answering."""

    @tool(MEMORY_TOOL_NAME)
    def search_memory(query: str) -> str:
        """Найти релевантные факты о пользователе в долгосрочной памяти. Передавай короткий целевой поисковый запрос (например «любимая еда» или «дедлайн проекта»), а не весь вопрос пользователя целиком. Можно вызывать несколько раз подряд с разными формулировками, если первого результата недостаточно."""
        context = memory_bridge.retrieve_memory_context(query, user_id, top_k=5)
        print("ПОИСК ПО ПАМЯТИ ДАЛ РЕЗУЛЬТАТ:\n", flush=True)
        print(context, flush=True)
        return context if context else "По этому запросу в памяти ничего не найдено."

    return search_memory


def get_sandbox_snapshot(user_id: str) -> dict[str, Any]:
    with db.get_conn() as conn:
        tasks = conn.execute(
            "SELECT id, title, due_date, notes, done, created_at FROM tasks "
            "WHERE user_id = ? ORDER BY created_at DESC LIMIT 20",
            (user_id,),
        ).fetchall()
        expenses = conn.execute(
            "SELECT id, amount, category, note, date, created_at FROM expenses "
            "WHERE user_id = ? ORDER BY created_at DESC LIMIT 20",
            (user_id,),
        ).fetchall()
        meals = conn.execute(
            "SELECT id, name, kcal, protein, fat, carbs, date, created_at FROM meals "
            "WHERE user_id = ? ORDER BY created_at DESC LIMIT 20",
            (user_id,),
        ).fetchall()
        weights = conn.execute(
            "SELECT id, value_kg, date, created_at FROM weights "
            "WHERE user_id = ? ORDER BY created_at DESC LIMIT 20",
            (user_id,),
        ).fetchall()

    return {
        "tasks": [dict(r) for r in tasks],
        "expenses": [dict(r) for r in expenses],
        "meals": [dict(r) for r in meals],
        "weights": [dict(r) for r in weights],
    }
