"""MCP-сервер: тематика "Карты" через Nominatim (геокодер OpenStreetMap,
без ключа).

Не дублирует ваш существующий show_map/build_route (Nominatim+Mapbox,
уже рисует карту прямо в UI фронтенда) — этот сервер даёт ДРУГОЕ:
подробную адресную информацию из OSM (страна/город/индекс/тип объекта)
и прямую ссылку для просмотра на openstreetmap.org, когда пользователю
нужны детали адреса, а не картинка карты в чате. Формулировка в
описании тула ниже специально разводит эти два случая, чтобы модель не
путала, какой инструмент вызвать.
"""

from __future__ import annotations

import requests
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("openstreetmap")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "ImpulseAssistant/1.0 (personal project; contact via GitHub)"


@mcp.tool()
def get_place_info(query: str) -> dict:
    """Подробная адресная информация о месте из OpenStreetMap (страна,
    город, район, почтовый индекс, тип объекта) + прямая ссылка для
    просмотра на openstreetmap.org. Используй ТОЛЬКО когда пользователю
    нужны детали адреса/справка о месте («что за адрес», «в какой это
    стране/районе», «дай ссылку на карту») — а не когда нужно просто
    показать место или маршрут в чате (для этого есть show_map/build_route).

    Args:
        query: название места или адрес, например "Кремль, Казань".
    """
    resp = requests.get(
        NOMINATIM_URL,
        params={"q": query, "format": "json", "limit": 1, "addressdetails": 1},
        headers={"User-Agent": USER_AGENT},
        timeout=10,
    )
    resp.raise_for_status()
    results = resp.json()

    if not results:
        return {"found": False, "query": query}

    hit = results[0]
    lat, lon = float(hit["lat"]), float(hit["lon"])
    address = hit.get("address", {})

    return {
        "found": True,
        "query": query,
        "display_name": hit.get("display_name"),
        "place_type": hit.get("type"),
        "category": hit.get("class"),
        "country": address.get("country"),
        "city": address.get("city") or address.get("town") or address.get("village"),
        "district": address.get("suburb") or address.get("state_district"),
        "postcode": address.get("postcode"),
        "lat": lat,
        "lon": lon,
        "osm_view_url": f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=17/{lat}/{lon}",
        "source": "OpenStreetMap / Nominatim",
    }


if __name__ == "__main__":
    mcp.run()
