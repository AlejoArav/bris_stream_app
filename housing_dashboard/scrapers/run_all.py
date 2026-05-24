from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..config import CONFIG
from ..db import create_scrape_backup, get_connection, init_db, insert_run_log, upsert_listing
from ..enrichment.geocode import enrich_coordinates
from ..models import now_utc_iso
from ..scoring import enrich_and_score_listing
from .static_css import build_static_scrapers_from_yaml


def _write_backup(trigger: str) -> None:
    try:
        backup_path = create_scrape_backup(CONFIG.database_path, CONFIG.scrape_backups_dir, trigger)
        print(f"[backup] Saved scrape backup at {backup_path}")
    except Exception as exc:
        print(f"[backup] ERROR: {exc}", file=sys.stderr)


def _log_runner_event(status: str, message: str, source: str = "scraper_runner") -> None:
    started = now_utc_iso()
    with get_connection(CONFIG.database_path) as conn:
        insert_run_log(
            conn,
            started_at=started,
            finished_at=now_utc_iso(),
            source=source,
            status=status,
            quality_status=status if status in {"healthy", "degraded"} else None,
            message=message,
            inserted_or_updated=0,
        )
        conn.commit()


def run_all_scrapers(sources_path: str | Path = "sources.yaml", trigger: str = "manual") -> int:
    init_db(CONFIG.database_path)
    sources_path = Path(sources_path)
    total = 0
    try:
        if not sources_path.exists():
            message = f"No sources file found at {sources_path}. Nothing to scrape."
            print(message)
            _log_runner_event(status="error", message=message)
            return 0

        scrapers = build_static_scrapers_from_yaml(str(sources_path))
        if not scrapers:
            message = (
                f"No enabled scrapers found in {sources_path}. "
                "Set at least one source with enabled: true."
            )
            print(message)
            _log_runner_event(status="skipped", message=message)
            return 0

        for scraper in scrapers:
            started = now_utc_iso()
            try:
                result = scraper.scrape()
                count = 0
                quality_status = result.quality_status if result.quality_status in {"healthy", "degraded"} else "healthy"
                log_status = "degraded" if quality_status == "degraded" else "success"
                log_message = result.message
                if quality_status == "degraded" and result.quality_gate_reason:
                    log_message = f"{result.message} Gate: {result.quality_gate_reason}"
                with get_connection(CONFIG.database_path) as conn:
                    if quality_status != "degraded":
                        for listing in result.listings:
                            if listing.lat is None or listing.lon is None:
                                lat, lon, postcode = enrich_coordinates(
                                    listing.address_text,
                                    listing.postcode,
                                    CONFIG.nominatim_user_agent,
                                    CONFIG.enable_online_enrichment,
                                )
                                listing.lat = lat or listing.lat
                                listing.lon = lon or listing.lon
                                listing.postcode = postcode or listing.postcode
                            listing = enrich_and_score_listing(listing)
                            upsert_listing(conn, listing)
                            count += 1
                    insert_run_log(
                        conn,
                        started_at=started,
                        finished_at=now_utc_iso(),
                        source=result.source,
                        status=log_status,
                        quality_status=quality_status,
                        metrics=result.metrics,
                        message=log_message,
                        inserted_or_updated=count,
                    )
                    conn.commit()
                print(f"[{result.source}] {log_message}")
                total += count
            except Exception as exc:
                with get_connection(CONFIG.database_path) as conn:
                    insert_run_log(
                        conn,
                        started_at=started,
                        finished_at=now_utc_iso(),
                        source=getattr(scraper, "source_name", "unknown"),
                        status="error",
                        quality_status="error",
                        message=repr(exc),
                        inserted_or_updated=0,
                    )
                    conn.commit()
                print(f"[{getattr(scraper, 'source_name', 'unknown')}] ERROR: {exc}", file=sys.stderr)
        return total
    finally:
        _write_backup(trigger)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", default="sources.yaml", help="Path to sources.yaml")
    parser.add_argument("--trigger", default="manual_cli", help="Trigger label for backup metadata.")
    args = parser.parse_args()
    total = run_all_scrapers(args.sources, trigger=args.trigger)
    print(f"Finished. Inserted/updated {total} listings.")


if __name__ == "__main__":
    main()
