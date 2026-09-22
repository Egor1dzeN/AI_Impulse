"""MCP-сервер: тематика "Погода" через Open-Meteo (без ключа).

Самодостаточный: сам геокодирует город через геокодер Open-Meteo, не
зависит от Nominatim/Mapbox, которые уже использует основной проект —
это отдельный, независимый источник данных, а не обёртка над
get_weather_impl из tools.py.

О дублировании с существующим локальным get_weather (tools.py): если
хотите, чтобы агент однозначно пользовался именно этим MCP-инструментом
(показать живую MCP-интеграцию на защите), уберите get_weather из
списка, который возвращает build_action_tools() в tools.py — иначе у
модели будет выбор из двух похожих тулов, и предсказать, какой она
вызовет, сложнее.
"""

from __future__ import annotations

import requests
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("weather")

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

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
    61: "небольшой дождь",
    63: "дождь",
    65: "сильный дождь",
    71: "небольшой снег",
    73: "снег",
    75: "сильный снег",
    80: "небольшой ливень",
    81: "ливень",
    82: "сильный ливень",
    95: "гроза",
}


@mcp.tool()
def get_weather_forecast(city: str) -> dict:
    """Текущая погода в указанном городе (источник: Open-Meteo).

    Args:
        city: название города, например "Нижний Новгород".
    """
    geo = requests.get(
        GEOCODING_URL,
        params={"name": city, "count": 1, "language": "ru"},
        timeout=10,
    ).json()
    results = geo.get("results")
    if not results:
        return {"found": False, "query": city}

    lat, lon = results[0]["latitude"], results[0]["longitude"]
    found_name = results[0]["name"]

    weather = requests.get(
        FORECAST_URL,
        params={
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
            "timezone": "auto",
        },
        timeout=10,
    ).json()
    current = weather.get("current", {})

    return {
        "found": True,
        "city": found_name,
        "temperature_c": current.get("temperature_2m"),
        "feels_like_c": current.get("apparent_temperature"),
        "humidity_percent": current.get("relative_humidity_2m"),
        "wind_speed_kmh": current.get("wind_speed_10m"),
        "description": _WEATHER_CODE_DESCRIPTIONS.get(current.get("weather_code"), "неизвестно"),
        "observed_at": current.get("time"),
        "source": "Open-Meteo (api.open-meteo.com)",
    }


if __name__ == "__main__":
    mcp.run()
