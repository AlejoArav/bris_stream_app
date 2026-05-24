from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

POSTCODE_RE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\b", re.I)
PRICE_RE = re.compile(
    r"(?P<prefix>£|gbp)?\s*(?P<amount>[0-9][0-9,]*(?:\.\d{1,2})?)\s*(?P<period>pcm|pm|per month|monthly|pw|pppw|per week|week|weekly)?",
    re.I,
)
BEDROOM_RE = re.compile(r"\b(studio|[0-9]+\s*(?:bed|beds|bedroom|bedrooms))\b", re.I)
FALSE_PRICE_CONTEXT_RE = re.compile(
    r"\b(deposit|holding fee|holding deposit|bond|admin fee|reservation fee|tenancy fee|council tax)\b",
    re.I,
)
PER_PERSON_RE = re.compile(r"\b(pp|per person|each)\b", re.I)

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


@dataclass(frozen=True)
class PriceCandidate:
    monthly_amount: float
    original_amount: float
    period: str
    confidence: int
    context: str


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


def _amount_to_pcm(amount: float, period: str) -> float:
    normalized_period = period.lower()
    if normalized_period in {"pw", "pppw", "per week", "week", "weekly"}:
        return round(amount * 52 / 12, 2)
    return round(amount, 2)


def extract_price_candidates(text: str | None) -> list[PriceCandidate]:
    if not text:
        return []
    out: list[PriceCandidate] = []
    for match in PRICE_RE.finditer(text):
        amount_raw = match.group("amount")
        if not amount_raw:
            continue
        period = (match.group("period") or "pcm").lower()
        full_match = match.group(0) or ""
        has_currency = bool(match.group("prefix")) or "£" in full_match

        context_start = max(0, match.start() - 40)
        context_end = min(len(text), match.end() + 40)
        context = text[context_start:context_end]
        local_context_start = max(0, match.start() - 16)
        local_context_end = min(len(text), match.end() + 16)
        local_context = text[local_context_start:local_context_end]
        if FALSE_PRICE_CONTEXT_RE.search(local_context):
            continue

        if not has_currency and not match.group("period"):
            continue

        try:
            original_amount = float(amount_raw.replace(",", ""))
        except ValueError:
            continue
        monthly_amount = _amount_to_pcm(original_amount, period)
        confidence = 0
        if has_currency:
            confidence += 3
        if period in {"pcm", "pm", "per month", "monthly"}:
            confidence += 2
        if period in {"pw", "pppw", "per week", "week", "weekly"}:
            confidence += 1
        if 300 <= monthly_amount <= 4500:
            confidence += 2
        elif monthly_amount < 100 or monthly_amount > 10000:
            confidence -= 3
        if PER_PERSON_RE.search(context):
            confidence -= 1

        out.append(
            PriceCandidate(
                monthly_amount=monthly_amount,
                original_amount=original_amount,
                period=period,
                confidence=confidence,
                context=context.strip(),
            )
        )

    out.sort(key=lambda item: (item.confidence, item.monthly_amount), reverse=True)
    return out


def parse_price_pcm(text: str | None) -> float | None:
    candidates = extract_price_candidates(text)
    if not candidates:
        return None
    return candidates[0].monthly_amount


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


def resolve_location(
    selector_address: str | None,
    jsonld_address: str | None,
    combined_text: str | None,
    known_areas: list[str] | None = None,
) -> tuple[str | None, str | None]:
    address = clean_text(selector_address) or clean_text(jsonld_address)
    postcode = extract_postcode(address) or extract_postcode(combined_text)

    if not address and postcode:
        address = postcode

    if not address and known_areas:
        area = find_area(combined_text, known_areas)
        if area:
            address = area

    return address, postcode
