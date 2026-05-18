from __future__ import annotations

import time
from typing import Any

import requests

from ..text_utils import extract_postcode


def geocode_uk_postcode(postcode: str, timeout: int = 10) -> tuple[float, float] | None:
    """Geocode a UK postcode via postcodes.io.

    Returns (lat, lon) or None. Use only for missing coordinates and keep calls
    low-frequency. Results are approximate to postcode level, which is usually
    sufficient for initial housing triage.
    """
    postcode = postcode.strip().replace(" ", "")
    url = f"https://api.postcodes.io/postcodes/{postcode}"
    try:
        r = requests.get(url, timeout=timeout)
        if r.status_code != 200:
            return None
        data: dict[str, Any] = r.json()
        result = data.get("result") or {}
        lat = result.get("latitude")
        lon = result.get("longitude")
        if lat is None or lon is None:
            return None
        return float(lat), float(lon)
    except Exception:
        return None


def geocode_with_nominatim(query: str, user_agent: str, timeout: int = 15) -> tuple[float, float] | None:
    """Very small Nominatim helper.

    Respect Nominatim usage policy: avoid bulk geocoding and cache results. This
    app only calls it when online enrichment is enabled and listing coordinates
    are missing.
    """
    try:
        headers = {"User-Agent": user_agent}
        params = {"q": query, "format": "json", "limit": 1}
        r = requests.get("https://nominatim.openstreetmap.org/search", params=params, headers=headers, timeout=timeout)
        if r.status_code != 200:
            return None
        data = r.json()
        if not data:
            return None
        time.sleep(1.0)
        return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception:
        return None


def enrich_coordinates(
    address_text: str | None,
    postcode: str | None,
    user_agent: str,
    enable_online: bool,
) -> tuple[float | None, float | None, str | None]:
    if not enable_online:
        return None, None, postcode or extract_postcode(address_text)

    inferred_postcode = postcode or extract_postcode(address_text)
    if inferred_postcode:
        coords = geocode_uk_postcode(inferred_postcode)
        if coords:
            return coords[0], coords[1], inferred_postcode

    if address_text:
        query = address_text
        if "bristol" not in query.lower():
            query += ", Bristol, UK"
        coords = geocode_with_nominatim(query, user_agent=user_agent)
        if coords:
            return coords[0], coords[1], inferred_postcode

    return None, None, inferred_postcode
