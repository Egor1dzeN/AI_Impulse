"""City -> IANA time zone, offline.

Used when the imported data says where the user lives (bank notifications,
"Город: …" in a letter) but the calendar carries no time zone. A static
table instead of a geo/time-zone API: no key, no network at evaluation
time, and every Russian zone is covered by its main cities.
"""
from __future__ import annotations

import re

_ZONES: dict[str, tuple[str, ...]] = {
    "Europe/Kaliningrad": ("калининград",),
    "Europe/Moscow": (
        "москва", "санкт-петербург", "петербург", "спб", "казань", "нижний новгород", "воронеж", "ростов-на-дону",
        "краснодар", "сочи", "ярославль", "тула", "рязань", "владимир", "иваново", "тверь", "калуга", "брянск",
        "смоленск", "курск", "белгород", "липецк", "тамбов", "орёл", "орел", "пенза", "чебоксары", "йошкар-ола",
        "киров", "вологда", "архангельск", "мурманск", "петрозаводск", "великий новгород", "псков", "сыктывкар",
        "ставрополь", "махачкала", "грозный", "нальчик", "владикавказ", "майкоп", "волгоград", "симферополь",
        "севастополь", "новороссийск", "набережные челны",
    ),
    "Europe/Samara": ("самара", "тольятти", "ижевск", "ульяновск"),
    "Europe/Saratov": ("саратов",),
    "Europe/Astrakhan": ("астрахань",),
    "Asia/Yekaterinburg": (
        "екатеринбург", "тюмень", "челябинск", "пермь", "уфа", "оренбург", "курган", "магнитогорск",
        "сургут", "нижневартовск", "ханты-мансийск", "салехард", "новый уренгой", "нижний тагил",
    ),
    "Asia/Omsk": ("омск",),
    "Asia/Novosibirsk": ("новосибирск",),
    "Asia/Tomsk": ("томск",),
    "Asia/Barnaul": ("барнаул", "горно-алтайск"),
    "Asia/Novokuznetsk": ("новокузнецк", "кемерово"),
    "Asia/Krasnoyarsk": ("красноярск", "абакан", "кызыл", "норильск"),
    "Asia/Irkutsk": ("иркутск", "улан-удэ", "братск"),
    "Asia/Chita": ("чита",),
    "Asia/Yakutsk": ("якутск", "благовещенск"),
    "Asia/Vladivostok": ("владивосток", "хабаровск", "комсомольск-на-амуре", "находка"),
    "Asia/Magadan": ("магадан",),
    "Asia/Sakhalin": ("южно-сахалинск",),
    "Asia/Kamchatka": ("петропавловск-камчатский", "анадырь"),
    # Nearby capitals that show up in Russian-language data.
    "Europe/Minsk": ("минск",),
    "Asia/Almaty": ("алматы", "астана"),
    "Asia/Tashkent": ("ташкент",),
    "Asia/Bishkek": ("бишкек",),
    "Asia/Tbilisi": ("тбилиси",),
    "Asia/Yerevan": ("ереван",),
    "Asia/Baku": ("баку",),
}


def _norm(city: str) -> str:
    city = re.sub(r"\s+", " ", (city or "").strip().lower().replace("ё", "е"))
    return re.sub(r"^(г\.|город)\s*", "", city)


_BY_CITY = {_norm(city): zone for zone, cities in _ZONES.items() for city in cities}


def timezone_for_city(city: str | None) -> str | None:
    """IANA zone for a city name as it appears in the data, or None."""
    if not city:
        return None
    return _BY_CITY.get(_norm(city))
