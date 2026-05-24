from __future__ import annotations

from housing_dashboard.models import Listing
from housing_dashboard.scoring import campus_distance_score, enrich_and_score_listing


def test_campus_distance_score_monotonic() -> None:
    assert campus_distance_score(0.5) > campus_distance_score(2.0)
    assert campus_distance_score(2.0) > campus_distance_score(5.5)


def test_enrich_and_score_prefers_closer_listing_when_other_factors_equal() -> None:
    closer = Listing(
        source="test",
        title="Close flat",
        url="https://example.com/close",
        external_id="close-1",
        price_pcm=1200.0,
        bills_included=False,
        internet_included=False,
        bedrooms=1.0,
        property_type="1 bedroom flat",
        address_text="Clifton, Bristol BS8 1AB",
        lat=51.4571,
        lon=-2.6032,
        available_from="September 2026",
    )
    farther = Listing(
        source="test",
        title="Far flat",
        url="https://example.com/far",
        external_id="far-1",
        price_pcm=1200.0,
        bills_included=False,
        internet_included=False,
        bedrooms=1.0,
        property_type="1 bedroom flat",
        address_text="Bristol BS5 6XX",
        lat=51.4685,
        lon=-2.5635,
        available_from="September 2026",
    )

    closer_scored = enrich_and_score_listing(closer)
    farther_scored = enrich_and_score_listing(farther)

    assert closer_scored.campus_distance_km is not None
    assert farther_scored.campus_distance_km is not None
    assert closer_scored.campus_distance_km < farther_scored.campus_distance_km
    assert (closer_scored.score or 0.0) > (farther_scored.score or 0.0)
