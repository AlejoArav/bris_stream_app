from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class Listing:
    source: str
    title: str
    url: str
    external_id: str | None = None
    price_pcm: float | None = None
    bills_included: bool | None = None
    internet_included: bool | None = None
    bedrooms: float | None = None
    bathrooms: float | None = None
    property_type: str | None = None
    address_text: str | None = None
    postcode: str | None = None
    lat: float | None = None
    lon: float | None = None
    available_from: str | None = None
    description: str | None = None
    area: str | None = None
    walking_minutes: float | None = None
    safety_band: str | None = None
    all_in_estimate_pcm: float | None = None
    score: float | None = None
    status: str = "active"  # active / maybe / rejected / contacted / viewing / applied / archived
    rejection_reason: str | None = None
    notes: str | None = None
    raw_json: str | None = None
    first_seen: str | None = None
    last_seen: str | None = None

    def asdict(self) -> dict[str, Any]:
        return asdict(self)
