from __future__ import annotations

import hashlib
import re
from typing import Any

POSTCODE_RE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\b", re.I)
PRICE_RE = re.compile(r"£\s*([0-9][0-9,]*(?:\.\d{1,2})?)\s*(pcm|pm|per month|pw|pppw|per week|week)?", re.I)
BEDROOM_RE = re.compile(r"\b(studio|[0-9]+\s*(?:bed|beds|bedroom|bedrooms))\b", re.I)

REJECT_KEYWORDS = [
    "house share",
    "shared house",
    "room in",
    "single room",
    "single occupancy",
    "hmo room",
    "rooms available",
    "flat share",
    "student room",
]

SELF_CONTAINED_KEYWORDS = [
    "studio",
    "1 bedroom",
    "one bedroom",
    "1 bed",
    "self contained",
    "self-contained",
    "apartment",
    "flat",
]


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def stable_id(*parts: str | None) -> str:
    material = "|".join(p or "" for p in parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def parse_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y", "included", "incl", "incl.", "bill included", "bills included"}:
        return True
    if text in {"false", "0", "no", "n", "not included", "excluded", "excl", "excl."}:
        return False
    return None


def parse_price_pcm(text: str | None) -> float | None:
    if not text:
        return None
    match = PRICE_RE.search(text)
    if not match:
        return None
    amount = float(match.group(1).replace(",", ""))
    period = (match.group(2) or "pcm").lower()
    if period in {"pw", "pppw", "per week", "week"}:
        return round(amount * 52 / 12, 2)
    return amount


def parse_bedrooms(text: str | None) -> float | None:
    if not text:
        return None
    if re.search(r"\bstudio\b", text, re.I):
        return 0.0
    match = re.search(r"\b([0-9]+)\s*(?:bed|beds|bedroom|bedrooms)\b", text, re.I)
    if match:
        return float(match.group(1))
    return None


def extract_postcode(text: str | None) -> str | None:
    if not text:
        return None
    match = POSTCODE_RE.search(text.upper())
    if not match:
        return None
    postcode = match.group(1).upper().replace(" ", "")
    return postcode[:-3] + " " + postcode[-3:]


def infer_property_type(title: str | None, description: str | None = None) -> str | None:
    text = " ".join(filter(None, [title, description])).lower()
    if "studio" in text:
        return "studio"
    if "1 bed" in text or "1 bedroom" in text or "one bedroom" in text:
        return "1 bedroom flat"
    if "apartment" in text:
        return "apartment"
    if "flat" in text:
        return "flat"
    if "room" in text:
        return "room"
    return None


def looks_self_contained(title: str | None, description: str | None = None, property_type: str | None = None) -> bool:
    text = " ".join(filter(None, [title, description, property_type])).lower()
    if any(keyword in text for keyword in REJECT_KEYWORDS):
        return False
    return any(keyword in text for keyword in SELF_CONTAINED_KEYWORDS)


def find_area(text: str | None, known_areas: list[str]) -> str | None:
    if not text:
        return None
    low = text.lower()
    for area in known_areas:
        if area.lower() in low:
            return area
    return None
