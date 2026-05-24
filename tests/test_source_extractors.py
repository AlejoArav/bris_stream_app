from __future__ import annotations

from pathlib import Path

import pytest

from housing_dashboard.scrapers.static_css import StaticCssScraper


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.mark.parametrize(
    ("source_name", "fixture_name"),
    [
        ("Bristol SU Lettings - Properties To Let", "bristol_su.html"),
        ("OpenRent - Bristol", "openrent.html"),
        ("Rightmove - Bristol 1 Bedroom Flats", "rightmove.html"),
        ("Zoopla - Bristol 1 Bedroom Flats", "zoopla.html"),
    ],
)
def test_phase1_source_extractors_have_price_and_location(source_name: str, fixture_name: str) -> None:
    html = (FIXTURE_DIR / fixture_name).read_text(encoding="utf-8")
    scraper = StaticCssScraper(
        source_name=source_name,
        url="https://example.com/listings",
        selectors={
            "card": ".totally-wrong-card-selector",
            "title": ".totally-wrong-title-selector",
            "url": ".totally-wrong-url-selector",
            "price": ".totally-wrong-price-selector",
            "address": ".totally-wrong-address-selector",
        },
        fallback_to_similar_selectors=True,
        enable_whole_page_block_discovery=True,
        render_mode="http",
        min_quality_price=0.8,
        min_quality_location=0.8,
    )

    result = scraper.parse_html(html, render_mode_used="http")
    assert result.metrics is not None
    assert result.metrics["cards_seen"] >= 1
    assert result.metrics["listings_emitted"] >= 1
    assert result.metrics["price_coverage"] >= 0.8
    assert result.metrics["location_coverage"] >= 0.8
    assert result.quality_status == "healthy"
