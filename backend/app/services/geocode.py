"""Регион по точке на карте: обратное геокодирование через Nominatim (OpenStreetMap).

Правила Nominatim: не больше запроса в секунду и осмысленный User-Agent. Нам это подходит:
запрос один на онбординг. Если сервис недоступен, регион остаётся координатами.
"""

from __future__ import annotations

import httpx

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "max-applicability-bot/1.0 (hackathon MAX 2026, t419)"


async def region_by_point(lat: float, lon: float) -> str | None:
    params = {"format": "jsonv2", "lat": lat, "lon": lon, "zoom": 5, "accept-language": "ru"}
    try:
        async with httpx.AsyncClient(timeout=6, headers={"User-Agent": USER_AGENT}) as c:
            r = await c.get(NOMINATIM_URL, params=params)
            r.raise_for_status()
            address = r.json().get("address") or {}
    except (httpx.HTTPError, ValueError):
        return None
    return address.get("state") or address.get("region") or address.get("city")
