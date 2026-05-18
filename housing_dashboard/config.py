from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv

load_dotenv()


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return float(value)


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


def _csv_env(name: str, default: Iterable[str]) -> list[str]:
    value = os.getenv(name)
    if not value:
        return list(default)
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class AppConfig:
    database_path: Path = Path(os.getenv("DATABASE_PATH", "data/housing.sqlite"))

    target_label: str = os.getenv("TARGET_LABEL", "University of Bristol Clifton Campus / Beacon House")
    target_lat: float = _float_env("TARGET_LAT", 51.4563)
    target_lon: float = _float_env("TARGET_LON", -2.6040)

    max_all_in_pcm: float = _float_env("MAX_ALL_IN_PCM", 1350.0)
    soft_max_all_in_pcm: float = _float_env("SOFT_MAX_ALL_IN_PCM", 1500.0)
    max_walking_minutes: float = _float_env("MAX_WALKING_MINUTES", 45.0)
    ideal_walking_minutes: float = _float_env("IDEAL_WALKING_MINUTES", 30.0)
    min_bedrooms: int = _int_env("MIN_BEDROOMS", 0)

    expected_bills_pcm: float = _float_env("EXPECTED_BILLS_PCM", 180.0)
    expected_internet_pcm: float = _float_env("EXPECTED_INTERNET_PCM", 30.0)

    scraper_interval_hours: int = _int_env("SCRAPER_INTERVAL_HOURS", 12)
    run_on_startup: bool = _bool_env("RUN_ON_STARTUP", True)

    enable_online_enrichment: bool = _bool_env("ENABLE_ONLINE_ENRICHMENT", False)
    nominatim_user_agent: str = os.getenv(
        "NOMINATIM_USER_AGENT",
        "bristol-housing-dashboard/0.1 personal-use",
    )

    preferred_areas: list[str] = field(default_factory=lambda: _csv_env(
        "PREFERRED_AREAS",
        ["Cotham", "Kingsdown", "Redland", "Clifton", "Clifton Down", "Bishopston", "St Andrews", "Montpelier"],
    ))
    caution_areas: list[str] = field(default_factory=lambda: _csv_env(
        "CAUTION_AREAS",
        ["Central", "Hotwells", "Harbourside", "City Centre"],
    ))


CONFIG = AppConfig()
