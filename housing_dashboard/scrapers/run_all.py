from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..config import CONFIG
from ..db import get_connection, init_db, insert_run_log, upsert_listing
from ..enrichment.geocode import enrich_coordinates
from ..models import now_utc_iso
from ..scoring import enrich_and_score_listing
from .static_css import build_static_scrapers_from_yaml


def run_all_scrapers(sources_path: str | Path = "sources.yaml") -> int:
    init_db(CONFIG.database_path)
    sources_path = Path(sources_path)
    if not sources_path.exists():
        print(f"No sources file found at {sources_path}. Nothing to scrape.")
        return 0

    scrapers = build_static_scrapers_from_yaml(str(sources_path))
    if not scrapers:
        print("No enabled scrapers found in sources file.")
        return 0

    total = 0
    for scraper in scrapers:
        started = now_utc_iso()
        try:
            result = scraper.scrape()
            count = 0
            with get_connection(CONFIG.database_path) as conn:
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
                    status="success",
                    message=result.message,
                    inserted_or_updated=count,
                )
                conn.commit()
            print(f"[{result.source}] {result.message}")
            total += count
        except Exception as exc:
            with get_connection(CONFIG.database_path) as conn:
                insert_run_log(
                    conn,
                    started_at=started,
                    finished_at=now_utc_iso(),
                    source=getattr(scraper, "source_name", "unknown"),
                    status="error",
                    message=repr(exc),
                    inserted_or_updated=0,
                )
                conn.commit()
            print(f"[{getattr(scraper, 'source_name', 'unknown')}] ERROR: {exc}", file=sys.stderr)
    return total


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", default="sources.yaml", help="Path to sources.yaml")
    args = parser.parse_args()
    total = run_all_scrapers(args.sources)
    print(f"Finished. Inserted/updated {total} listings.")


if __name__ == "__main__":
    main()
