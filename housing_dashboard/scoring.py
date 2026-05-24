from __future__ import annotations

from .config import AppConfig, CONFIG
from .models import Listing
from .text_utils import find_area, looks_self_contained
from .enrichment.walking_time import estimate_walking_minutes, haversine_km


LOW_RISK_AREA_HINTS = {
    "Cotham",
    "Kingsdown",
    "Redland",
    "Clifton",
    "Clifton Down",
    "Bishopston",
    "St Andrews",
}

CAUTION_AREA_HINTS = {
    "Central",
    "City Centre",
    "Hotwells",
    "Harbourside",
}


def estimate_all_in_pcm(listing: Listing, config: AppConfig = CONFIG) -> float | None:
    if listing.price_pcm is None:
        return None
    total = float(listing.price_pcm)
    if listing.bills_included is not True:
        total += config.expected_bills_pcm
    if listing.internet_included is not True:
        total += config.expected_internet_pcm
    return round(total, 2)


def price_score(all_in_pcm: float | None, config: AppConfig = CONFIG) -> float:
    if all_in_pcm is None:
        return 0.35
    if all_in_pcm <= config.max_all_in_pcm - 200:
        return 1.0
    if all_in_pcm <= config.max_all_in_pcm - 100:
        return 0.85
    if all_in_pcm <= config.max_all_in_pcm:
        return 0.70
    if all_in_pcm <= config.soft_max_all_in_pcm:
        return 0.35
    return 0.0


def walking_score(walking_minutes: float | None, config: AppConfig = CONFIG) -> float:
    if walking_minutes is None:
        return 0.45
    if walking_minutes <= config.ideal_walking_minutes:
        return 1.0
    if walking_minutes <= config.max_walking_minutes:
        return 0.65
    if walking_minutes <= config.max_walking_minutes + 15:
        return 0.25
    return 0.0


def campus_distance_score(campus_distance_km: float | None) -> float:
    if campus_distance_km is None:
        return 0.45
    if campus_distance_km <= 0.8:
        return 1.0
    if campus_distance_km <= 1.5:
        return 0.9
    if campus_distance_km <= 2.5:
        return 0.75
    if campus_distance_km <= 3.5:
        return 0.55
    if campus_distance_km <= 5.0:
        return 0.3
    if campus_distance_km <= 7.0:
        return 0.1
    return 0.0


def safety_band_for_area(area: str | None, config: AppConfig = CONFIG) -> str | None:
    if not area:
        return None
    area_low = area.lower()
    if any(a.lower() in area_low for a in config.preferred_areas) or area in LOW_RISK_AREA_HINTS:
        return "preferred"
    if any(a.lower() in area_low for a in config.caution_areas) or area in CAUTION_AREA_HINTS:
        return "caution"
    return "unknown"


def safety_score(safety_band: str | None) -> float:
    if safety_band == "preferred":
        return 1.0
    if safety_band == "unknown" or safety_band is None:
        return 0.55
    if safety_band == "caution":
        return 0.25
    return 0.55


def couple_fit_score(listing: Listing) -> float:
    title = listing.title or ""
    desc = listing.description or ""
    ptype = listing.property_type or ""
    text = f"{title} {desc} {ptype}".lower()

    if any(bad in text for bad in ["single occupancy", "room only", "house share", "shared house", "flat share"]):
        return 0.0

    if looks_self_contained(title, desc, ptype):
        if listing.bedrooms in (0, 1, 1.0, 0.0, None):
            return 1.0
        return 0.7

    return 0.45


def availability_score(available_from: str | None) -> float:
    if not available_from:
        return 0.45
    text = available_from.lower()
    if "sep" in text or "2026-09" in text or "september" in text:
        return 1.0
    if "aug" in text or "2026-08" in text or "oct" in text or "2026-10" in text:
        return 0.75
    return 0.4


def enrich_and_score_listing(listing: Listing, config: AppConfig = CONFIG) -> Listing:
    text_for_area = " ".join(filter(None, [listing.address_text, listing.title, listing.description]))
    if not listing.area:
        listing.area = find_area(text_for_area, config.preferred_areas + config.caution_areas)

    if listing.campus_distance_km is None and listing.lat is not None and listing.lon is not None:
        listing.campus_distance_km = round(
            haversine_km(listing.lat, listing.lon, config.target_lat, config.target_lon),
            2,
        )

    if listing.walking_minutes is None and listing.lat is not None and listing.lon is not None:
        listing.walking_minutes = estimate_walking_minutes(
            listing.lat,
            listing.lon,
            config.target_lat,
            config.target_lon,
        )

    if not listing.safety_band:
        listing.safety_band = safety_band_for_area(listing.area, config=config)

    listing.all_in_estimate_pcm = estimate_all_in_pcm(listing, config=config)

    # Explicitly score proximity to campus: closer listings get higher scores.
    total = (
        0.30 * price_score(listing.all_in_estimate_pcm, config=config)
        + 0.20 * walking_score(listing.walking_minutes, config=config)
        + 0.15 * campus_distance_score(listing.campus_distance_km)
        + 0.18 * safety_score(listing.safety_band)
        + 0.12 * couple_fit_score(listing)
        + 0.05 * availability_score(listing.available_from)
    )
    listing.score = round(total * 100, 1)
    return listing
