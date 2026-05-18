from __future__ import annotations

import argparse

from apscheduler.schedulers.blocking import BlockingScheduler

from ..config import CONFIG
from .run_all import run_all_scrapers


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", default="sources.yaml", help="Path to sources.yaml")
    args = parser.parse_args()

    interval = CONFIG.scraper_interval_hours
    scheduler = BlockingScheduler(timezone="UTC")

    scheduler.add_job(
        run_all_scrapers,
        "interval",
        hours=interval,
        args=[args.sources],
        id="housing_scraper",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    print(f"Scheduled scraping every {interval} hours.")
    if CONFIG.run_on_startup:
        print("RUN_ON_STARTUP=true; running scraper once now.")
        run_all_scrapers(args.sources)

    scheduler.start()


if __name__ == "__main__":
    main()
