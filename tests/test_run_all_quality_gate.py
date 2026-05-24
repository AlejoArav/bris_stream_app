from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

from housing_dashboard.models import Listing
from housing_dashboard.scrapers.base import ScrapeResult
from housing_dashboard.scrapers.run_all import run_all_scrapers
import housing_dashboard.scrapers.run_all as run_all_module


class HealthyScraper:
    source_name = "Healthy Source"

    def scrape(self) -> ScrapeResult:
        listing = Listing(
            source=self.source_name,
            title="Healthy Listing",
            url="https://example.com/healthy/1",
            external_id="healthy-1",
            price_pcm=1200.0,
            address_text="Clifton, Bristol BS8 1AB",
            postcode="BS8 1AB",
        )
        return ScrapeResult(
            source=self.source_name,
            listings=[listing],
            quality_status="healthy",
            metrics={
                "cards_seen": 1,
                "listings_emitted": 1,
                "price_coverage": 1.0,
                "location_coverage": 1.0,
                "duplicates_dropped": 0,
                "render_mode_used": "http",
            },
            message="healthy source ok",
        )


class DegradedScraper:
    source_name = "Degraded Source"

    def scrape(self) -> ScrapeResult:
        listing = Listing(
            source=self.source_name,
            title="Bad Listing",
            url="https://example.com/degraded/1",
            external_id="degraded-1",
            price_pcm=None,
            address_text=None,
            postcode=None,
        )
        return ScrapeResult(
            source=self.source_name,
            listings=[listing],
            quality_status="degraded",
            quality_gate_reason="coverage below threshold",
            metrics={
                "cards_seen": 1,
                "listings_emitted": 1,
                "price_coverage": 0.0,
                "location_coverage": 0.0,
                "duplicates_dropped": 0,
                "render_mode_used": "browser",
            },
            message="degraded source blocked",
        )


def test_run_all_quarantines_degraded_sources(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "housing.sqlite"
    backup_dir = tmp_path / "backups"
    sources_file = tmp_path / "sources.yaml"
    sources_file.write_text("sources: []\n", encoding="utf-8")

    test_config = SimpleNamespace(
        database_path=db_path,
        scrape_backups_dir=backup_dir,
        nominatim_user_agent="test-agent",
        enable_online_enrichment=False,
    )
    monkeypatch.setattr(run_all_module, "CONFIG", test_config)
    monkeypatch.setattr(
        run_all_module,
        "build_static_scrapers_from_yaml",
        lambda _path: [HealthyScraper(), DegradedScraper()],
    )
    monkeypatch.setattr(run_all_module, "enrich_coordinates", lambda *_args, **_kwargs: (None, None, None))

    total = run_all_scrapers(sources_path=sources_file, trigger="test")
    assert total == 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        listing_rows = conn.execute("SELECT source FROM listings ORDER BY id").fetchall()
        assert [row["source"] for row in listing_rows] == ["Healthy Source"]

        degraded_log = conn.execute(
            "SELECT status, quality_status, inserted_or_updated, metrics_json FROM run_log WHERE source = ?",
            ("Degraded Source",),
        ).fetchone()
        assert degraded_log is not None
        assert degraded_log["status"] == "degraded"
        assert degraded_log["quality_status"] == "degraded"
        assert degraded_log["inserted_or_updated"] == 0
        assert '"price_coverage": 0.0' in (degraded_log["metrics_json"] or "")
    finally:
        conn.close()
