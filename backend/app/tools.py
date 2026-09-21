from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

import requests
from langchain_core.tools import BaseTool, tool

from . import db, google_calendar, memory_bridge, user_settings

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


def create_calendar_event_impl(
    refresh_token: str, title: str, start_time: str, duration_minutes: int = 30, notes: str | None = None
) -> dict[str, Any]:
    """Create a real event (with a popup reminder) on the user's own Google
    Calendar — unlike create_task_impl above, which only writes to this
    app's internal tasks table. See google_calendar.py for the OAuth/API
    details; ToolNode(handle_tool_errors=True) in agent_graph.py turns any
    exception here (revoked access, a malformed start_time, ...) into a
    ToolMessage the model can relay to the user instead of crashing the turn."""
    return google_calendar.create_event(
        refresh_token=refresh_token,
        summary=title,
        start_iso=start_time,
        duration_minutes=duration_minutes,
        description=notes,
    )


def list_calendar_events_impl(refresh_token: str, date: str, days: int = 1) -> dict[str, Any]:
    """Read events from the user's own Google Calendar — the read-side
    counterpart to create_calendar_event_impl above."""
    return {"events": google_calendar.list_events(refresh_token=refresh_token, date=date, days=days)}


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
# TOPIC 3b — WORKOUTS (INTERVALS.ICU)
# ============================================================
#
# log_meal/log_weight/get_health_summary above are our own SQLite tables
# for calories and body weight. This is a different concept — an actual
# training session (a run, a ride, a gym session) — logged to a free
# external service (intervals.icu) instead of reimplementing training-load
# tracking ourselves. Auth is a personal API key (HTTP Basic, username
# "API_KEY"), connected per-user via Settings (see user_settings.py) and
# stored in the users table — not a public token like Mapbox's, so it's
# never a source default and each user only ever sees/uses their own.

INTERVALS_ICU_BASE_URL = "https://intervals.icu/api/v1"


def _intervals_icu_auth(api_key: str) -> tuple[str, str]:
    return ("API_KEY", api_key)


def intervals_icu_key_is_valid(api_key: str) -> bool:
    """Used by the Settings endpoint to confirm a key actually works before
    saving it, rather than letting a typo surface later as a mid-chat tool
    failure. `athlete/0` (current-athlete shorthand) is the cheapest
    authenticated call the API offers."""
    resp = requests.get(f"{INTERVALS_ICU_BASE_URL}/athlete/0", auth=_intervals_icu_auth(api_key), timeout=10)
    if resp.status_code == 401:
        return False
    resp.raise_for_status()
    return True


def log_workout_impl(
    api_key: str,
    activity_type: str,
    name: str,
    date: str,
    duration_min: float,
    distance_km: float | None = None,
    calories: float | None = None,
    avg_heart_rate: float | None = None,
    perceived_exertion: float | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": activity_type,
        "name": name,
        "start_date_local": f"{date}T12:00:00",
        "moving_time": round(duration_min * 60),
    }
    if distance_km is not None:
        payload["distance"] = distance_km * 1000
    if calories is not None:
        payload["calories"] = calories
    if avg_heart_rate is not None:
        payload["average_heartrate"] = avg_heart_rate
    if perceived_exertion is not None:
        payload["perceived_exertion"] = perceived_exertion
    if notes:
        payload["description"] = notes

    resp = requests.post(
        f"{INTERVALS_ICU_BASE_URL}/athlete/0/activities/manual",
        json=payload,
        auth=_intervals_icu_auth(api_key),
        timeout=10,
    )
    resp.raise_for_status()
    created = resp.json()

    return {
        "logged": True,
        "id": created.get("id"),
        "type": created.get("type", activity_type),
        "name": created.get("name", name),
        "date": date,
        "duration_min": duration_min,
        "distance_km": distance_km,
    }


def get_workouts_impl(api_key: str, days: int = 14) -> dict[str, Any]:
    oldest = (date.today() - timedelta(days=days)).isoformat()

    resp = requests.get(
        f"{INTERVALS_ICU_BASE_URL}/athlete/0/activities",
        params={"oldest": oldest},
        auth=_intervals_icu_auth(api_key),
        timeout=10,
    )
    resp.raise_for_status()
    activities = resp.json()

    return {
        "days": days,
        "count": len(activities),
        "workouts": [
            {
                "id": a.get("id"),
                "type": a.get("type"),
                "name": a.get("name"),
                "date": a.get("start_date_local"),
                "duration_min": round((a.get("moving_time") or 0) / 60),
                "distance_km": round(a["distance"] / 1000, 1) if a.get("distance") else None,
                "calories": a.get("calories"),
                "avg_heart_rate": a.get("average_heartrate"),
                "training_load": a.get("icu_training_load"),
            }
            for a in activities
        ],
    }


def get_workout_route_impl(api_key: str, workout_id: str) -> dict[str, Any]:
    auth = _intervals_icu_auth(api_key)

    activity_resp = requests.get(f"{INTERVALS_ICU_BASE_URL}/activity/{workout_id}", auth=auth, timeout=10)
    if activity_resp.status_code == 404:
        # The model has no way to validate a workout_id before calling this
        # (it just copies whatever get_workouts returned, or occasionally
        # hallucinates one despite the docstring) — a stale/bad id is an
        # expected, recoverable case, not an exceptional one, so it gets
        # the same {"found": False, ...} shape the rest of tools.py uses
        # for "didn't find it" instead of raising.
        return {"found": False, "workout_id": workout_id}
    activity_resp.raise_for_status()
    activity = activity_resp.json()

    map_resp = requests.get(f"{INTERVALS_ICU_BASE_URL}/activity/{workout_id}/map", auth=auth, timeout=10)
    map_resp.raise_for_status()
    points = map_resp.json().get("latlngs") or []

    summary = {
        "found": True,
        "workout_id": workout_id,
        "name": activity.get("name"),
        "type": activity.get("type"),
        "date": activity.get("start_date_local"),
        "distance_km": round(activity["distance"] / 1000, 1) if activity.get("distance") else None,
        "duration_min": round((activity.get("moving_time") or 0) / 60),
        "has_route": len(points) >= 2,
    }
    if len(points) < 2:
        return summary

    return {**summary, "points": points}


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
# TOPIC 4b — ROUTES (MAPBOX)
# ============================================================
#
# show_map/get_weather stay on Nominatim+OSM (free, no key, fine for "where
# is X"/a plain pin). Mapbox only comes in here, for the one thing OSM's
# free tier doesn't give us: actual turn-by-turn routing (distance/duration/
# path). Geocoding for the two endpoints still goes through the same
# Nominatim geocoder as everything else above — no reason to spend Mapbox
# quota on that part too.

MAPBOX_TOKEN = os.environ.get(
    "MAPBOX_TOKEN",
    # Public token (pk.*), scoped to Mapbox's client-side APIs (Directions,
    # GL JS map rendering) — meant to be embedded in source/client code, not
    # a secret. The frontend uses this same value (frontend/src/lib/mapbox.js)
    # to render the interactive map itself. Override via env if it's ever
    # rotated (update both places).
    "pk.eyJ1IjoiZWdvcjFkemVuIiwiYSI6ImNtdThnZ2FoajAybW0yeXIxMWN0eWZ1aXUifQ.2fOYRI5EI-4w6aZRl3mqag",
)

MAPBOX_DIRECTIONS_URL = "https://api.mapbox.com/directions/v5/mapbox/{profile}/{coords}"

_ROUTE_PROFILES = {"driving", "walking", "cycling"}


def build_route_impl(origin: str, destination: str, profile: str = "driving") -> dict[str, Any]:
    profile = profile if profile in _ROUTE_PROFILES else "driving"

    origin_loc = geocode_location_impl(origin)
    if not origin_loc["found"]:
        return {"found": False, "missing": "origin", "query": origin}

    dest_loc = geocode_location_impl(destination)
    if not dest_loc["found"]:
        return {"found": False, "missing": "destination", "query": destination}

    coords = f"{origin_loc['lon']},{origin_loc['lat']};{dest_loc['lon']},{dest_loc['lat']}"
    resp = requests.get(
        MAPBOX_DIRECTIONS_URL.format(profile=profile, coords=coords),
        params={
            # geojson, not the encoded "polyline" string from the earlier
            # static-image version: the frontend now draws the route itself
            # with Mapbox GL JS, which wants plain [lon, lat] pairs for a
            # GeoJSON LineString source — no client-side polyline decoder
            # needed. "full" is safe again now that this never has to fit
            # inside a URL (that's what forced "simplified" for the old
            # Static Images approach).
            "geometries": "geojson",
            "overview": "full",
            "access_token": MAPBOX_TOKEN,
        },
        timeout=8,
    )
    resp.raise_for_status()
    routes = resp.json().get("routes") or []

    if not routes:
        return {"found": False, "missing": "route", "query": f"{origin} -> {destination}"}

    route = routes[0]

    return {
        "found": True,
        "origin": origin_loc["display_name"],
        "destination": dest_loc["display_name"],
        "profile": profile,
        "distance_km": round(route["distance"] / 1000, 1),
        "duration_min": round(route["duration"] / 60),
        "origin_coords": [origin_loc["lon"], origin_loc["lat"]],
        "destination_coords": [dest_loc["lon"], dest_loc["lat"]],
        "route_geometry": route["geometry"]["coordinates"],
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
        """Создать задачу во внутреннем списке задач приложения (НЕ синхронизируется с Google Calendar и не присылает уведомлений в конкретное время). Используй для простых дел без точного времени ("добавь задачу купить молоко", "нужно доделать отчёт до пятницы"). Если пользователь просит именно напоминание/событие на конкретные дату И время ("напомни завтра в 9 утра") — используй create_calendar_event, если она доступна в списке функций, а не эту. due_date — YYYY-MM-DD, опционально."""
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

    intervals_tools: list[BaseTool] = []
    intervals_icu_key = user_settings.get_intervals_icu_key(user_id)
    if intervals_icu_key:

        @tool
        def log_workout(
            activity_type: str,
            name: str,
            date: str,
            duration_min: float,
            distance_km: float | None = None,
            calories: float | None = None,
            avg_heart_rate: float | None = None,
            perceived_exertion: float | None = None,
            notes: str | None = None,
        ) -> dict:
            """Записать тренировку (пробежка, велосипед, зал, плавание, йога и т.п.) во внешний сервис Intervals.icu. Действие на запись. НЕ путай с log_meal/log_weight — те про калории и вес, эта функция про саму тренировку как событие. Вызывай ТОЛЬКО когда пользователь явно просит записать/сохранить тренировку (например "запиши тренировку: бег 5км за 30 минут" или "сегодня час позанимался в зале"). activity_type — тип на английском в стиле Strava: Run, Ride, WeightTraining, Swim, Walk, Hike, Workout, Yoga и т.п. — подбери ближайший по смыслу. name — короткое название тренировки. date — YYYY-MM-DD, используй сегодняшнюю дату, если пользователь не указал. duration_min — длительность в минутах. distance_km, calories, avg_heart_rate, perceived_exertion (шкала 1-10) — заполняй, только если пользователь их называл."""
            return log_workout_impl(
                api_key=intervals_icu_key,
                activity_type=activity_type,
                name=name,
                date=date,
                duration_min=duration_min,
                distance_km=distance_km,
                calories=calories,
                avg_heart_rate=avg_heart_rate,
                perceived_exertion=perceived_exertion,
                notes=notes,
            )

        @tool
        def get_workouts(days: int = 14) -> dict:
            """Получить список тренировок пользователя из Intervals.icu за последние N дней (по умолчанию 14), включая их id. Вызывай, когда пользователь спрашивает про свои тренировки/активность за последнее время, историю пробежек/заездов и т.п. Если после этого пользователь попросит показать маршрут/карту одной из тренировок — возьми её id отсюда и вызови get_workout_route."""
            return get_workouts_impl(api_key=intervals_icu_key, days=days)

        @tool(response_format="content_and_artifact")
        def get_workout_route(workout_id: str) -> tuple[dict, dict]:
            """Получить маршрут (GPS-трек) конкретной тренировки из Intervals.icu — если трек есть, пользователю отдельно покажется интерактивная карта, БЕЗ дополнительных вызовов с твоей стороны. Вызывай, когда пользователь просит показать маршрут/карту/куда он бежал/ехал на конкретной тренировке. workout_id — бери из результата get_workouts (вызови её сначала, если ещё не знаешь id нужной тренировки); никогда не придумывай и не угадывай id сам, и никогда не бери его из названия/описания тренировки в сообщении пользователя. Если found=false — такой тренировки не существует (id устарел или был придуман), сообщи об этом пользователю и предложи сначала посмотреть список через get_workouts. Если found=true и has_route=true — карта уже показана, НЕ вызывай show_map или что-либо ещё (show_map с названием тренировки вместо места ничего не найдёт), просто опиши тренировку словами по этим данным. Если found=true, но has_route=false (например тренировка добавлена вручную) — карта не покажется, поэтому в ответе просто опиши тренировку словами (тип, дата, длительность, дистанция), не упоминая карту и не выдумывая маршрут."""
            result = get_workout_route_impl(api_key=intervals_icu_key, workout_id=workout_id)
            summary = {k: v for k, v in result.items() if k != "points"}
            return summary, result

        intervals_tools = [log_workout, get_workouts, get_workout_route]

    calendar_tools: list[BaseTool] = []
    calendar_refresh_token = user_settings.get_google_calendar_refresh_token(user_id)
    if calendar_refresh_token:

        @tool
        def create_calendar_event(
            title: str, start_time: str, duration_minutes: int = 30, notes: str | None = None
        ) -> dict:
            """Поставить напоминание/событие в РЕАЛЬНЫЙ Google-календарь пользователя (не путать с create_task — та пишет только во внутренний список задач приложения, без синхронизации с Google). Вызывай ТОЛЬКО когда пользователь явно просит поставить напоминание/событие на конкретное время или дату ("напомни завтра в 9 утра", "поставь встречу в календарь на пятницу в 15:00"). title — короткое название. start_time — ОБЯЗАТЕЛЬНО в формате YYYY-MM-DDTHH:MM:SS по московскому времени (Europe/Moscow), без указания смещения часового пояса — сам разреши относительные даты/время ("завтра", "через час", "в 9 утра") в этот формат, используя сегодняшнюю дату из инструкций. duration_minutes — длительность события в минутах (по умолчанию 30), если пользователь не назвал — не спрашивай, просто оставь по умолчанию. В момент start_time пользователю придёт всплывающее уведомление в Google Calendar."""
            return create_calendar_event_impl(
                refresh_token=calendar_refresh_token,
                title=title,
                start_time=start_time,
                duration_minutes=duration_minutes,
                notes=notes,
            )

        @tool
        def get_calendar_events(date: str, days: int = 1) -> dict:
            """Посмотреть, что запланировано в РЕАЛЬНОМ Google-календаре пользователя. Вызывай, когда пользователь спрашивает про свои планы/расписание/встречи на конкретный день или период ("что у меня завтра", "какие планы на этой неделе", "что запланировано на пятницу"). date — YYYY-MM-DD, начало периода (переведи "сегодня"/"завтра"/день недели в конкретную дату сам, используя сегодняшнюю дату из инструкций). days — сколько дней подряд просмотреть начиная с date (по умолчанию 1 — только этот день; для "на этой неделе" используй 7). Если events пуст — значит на этот период ничего не запланировано, так и скажи."""
            return list_calendar_events_impl(refresh_token=calendar_refresh_token, date=date, days=days)

        calendar_tools = [create_calendar_event, get_calendar_events]

    @tool
    def show_map(place: str) -> dict:
        """Показать место на карте прямо в чате. Вызывай ТОЛЬКО когда пользователь явно просит показать карту или спрашивает "где находится X" — простое упоминание места в разговоре (например "я живу в X") не повод её вызывать. Если пользователь просит именно маршрут/путь/сколько ехать из одного места в другое — используй build_route, а не эту функцию. place — только реальное название места. Если пользователь не назвал место явно (например "покажи на карте где я живу") — сначала вызови search_memory, чтобы узнать место из памяти, и передай сюда именно то, что она вернула, слово в слово. Никогда не придумывай и не дописывай место от себя — если в памяти его нет, не вызывай эту функцию, а спроси пользователя. После вызова не добавляй в ответ данные из других функций (погоду, курс и т.п.), если их не просили."""
        return geocode_location_impl(place)

    @tool(response_format="content_and_artifact")
    def build_route(origin: str, destination: str, profile: str = "driving") -> tuple[dict, dict]:
        """Построить маршрут между двумя точками: расстояние, время в пути и карта с проложенным путём. Вызывай ТОЛЬКО когда пользователь явно просит маршрут/путь/"как добраться из X в Y"/сколько ехать или идти — для простого "покажи X на карте" используй show_map, а не эту функцию. profile — один из driving (по умолчанию, на машине), walking (пешком), cycling (на велосипеде), выбирай по смыслу запроса. origin и destination — реальные названия мест; если одно из них не названо явно (например "маршрут до работы"), сначала вызови search_memory, чтобы узнать место из памяти, и передай сюда именно то, что она вернула. Карта уже показывается пользователю отдельно — в ответе просто назови расстояние и время в пути своими словами."""
        result = build_route_impl(origin=origin, destination=destination, profile=profile)

        # response_format="content_and_artifact": `content` (returned first)
        # is what actually lands in the model's prompt on the next turn,
        # `artifact` is carried on the ToolMessage for the app to read but
        # is never serialized into any prompt. route_geometry can be
        # thousands of [lon, lat] pairs (a few hundred KB as JSON) — feeding
        # that back into the model blew past its context window and crashed
        # generation with a CUDA OOM. The frontend map only needs the full
        # `result` (with route_geometry); the model only needs the summary.
        if not result["found"]:
            return result, result

        summary = {k: v for k, v in result.items() if k != "route_geometry"}
        return summary, result

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
        *intervals_tools,
        *calendar_tools,
        show_map,
        build_route,
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
