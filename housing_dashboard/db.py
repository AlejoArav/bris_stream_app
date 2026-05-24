from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

import pandas as pd

from .models import Listing, now_utc_iso
from .text_utils import stable_id

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    price_pcm REAL,
    bills_included INTEGER,
    internet_included INTEGER,
    bedrooms REAL,
    bathrooms REAL,
    property_type TEXT,
    address_text TEXT,
    postcode TEXT,
    lat REAL,
    lon REAL,
    available_from TEXT,
    description TEXT,
    area TEXT,
    walking_minutes REAL,
    campus_distance_km REAL,
    safety_band TEXT,
    all_in_estimate_pcm REAL,
    score REAL,
    status TEXT DEFAULT 'active',
    rejection_reason TEXT,
    notes TEXT,
    raw_json TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    UNIQUE(source, external_id)
);

CREATE INDEX IF NOT EXISTS idx_listings_status ON listings(status);
CREATE INDEX IF NOT EXISTS idx_listings_score ON listings(score);
CREATE INDEX IF NOT EXISTS idx_listings_price ON listings(price_pcm);
CREATE INDEX IF NOT EXISTS idx_listings_last_seen ON listings(last_seen);

CREATE TABLE IF NOT EXISTS run_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    source TEXT,
    status TEXT NOT NULL,
    quality_status TEXT,
    metrics_json TEXT,
    message TEXT,
    inserted_or_updated INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS scrape_backups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scraped_at TEXT NOT NULL,
    trigger TEXT NOT NULL,
    file_path TEXT NOT NULL,
    listing_count INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_scrape_backups_scraped_at ON scrape_backups(scraped_at DESC);
"""


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db(db_path: str | Path) -> None:
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA)
        _ensure_column(conn, "listings", "campus_distance_km", "REAL")
        _ensure_column(conn, "run_log", "quality_status", "TEXT")
        _ensure_column(conn, "run_log", "metrics_json", "TEXT")
        conn.commit()


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, column_type: str) -> None:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    existing = {row["name"] if isinstance(row, sqlite3.Row) else row[1] for row in rows}
    if column_name in existing:
        return
    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")


def _to_db_bool(value: bool | None) -> int | None:
    if value is None:
        return None
    return 1 if value else 0


def upsert_listing(conn: sqlite3.Connection, listing: Listing) -> None:
    now = now_utc_iso()
    external_id = listing.external_id or stable_id(listing.source, listing.url or listing.title)

    existing = conn.execute(
        "SELECT id, first_seen, status, notes FROM listings WHERE source = ? AND external_id = ?",
        (listing.source, external_id),
    ).fetchone()

    first_seen = existing["first_seen"] if existing else (listing.first_seen or now)
    status = listing.status or (existing["status"] if existing else "active")
    notes = listing.notes if listing.notes is not None else (existing["notes"] if existing else None)

    conn.execute(
        """
        INSERT INTO listings (
            source, external_id, title, url, price_pcm, bills_included, internet_included,
            bedrooms, bathrooms, property_type, address_text, postcode, lat, lon,
            available_from, description, area, walking_minutes, campus_distance_km, safety_band,
            all_in_estimate_pcm, score, status, rejection_reason, notes, raw_json,
            first_seen, last_seen
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, external_id) DO UPDATE SET
            title = excluded.title,
            url = excluded.url,
            price_pcm = COALESCE(excluded.price_pcm, listings.price_pcm),
            bills_included = COALESCE(excluded.bills_included, listings.bills_included),
            internet_included = COALESCE(excluded.internet_included, listings.internet_included),
            bedrooms = COALESCE(excluded.bedrooms, listings.bedrooms),
            bathrooms = COALESCE(excluded.bathrooms, listings.bathrooms),
            property_type = COALESCE(excluded.property_type, listings.property_type),
            address_text = COALESCE(excluded.address_text, listings.address_text),
            postcode = COALESCE(excluded.postcode, listings.postcode),
            lat = COALESCE(excluded.lat, listings.lat),
            lon = COALESCE(excluded.lon, listings.lon),
            available_from = COALESCE(excluded.available_from, listings.available_from),
            description = COALESCE(excluded.description, listings.description),
            area = COALESCE(excluded.area, listings.area),
            walking_minutes = COALESCE(excluded.walking_minutes, listings.walking_minutes),
            campus_distance_km = COALESCE(excluded.campus_distance_km, listings.campus_distance_km),
            safety_band = COALESCE(excluded.safety_band, listings.safety_band),
            all_in_estimate_pcm = COALESCE(excluded.all_in_estimate_pcm, listings.all_in_estimate_pcm),
            score = COALESCE(excluded.score, listings.score),
            status = listings.status,
            rejection_reason = COALESCE(excluded.rejection_reason, listings.rejection_reason),
            notes = COALESCE(?, listings.notes),
            raw_json = COALESCE(excluded.raw_json, listings.raw_json),
            last_seen = excluded.last_seen
        """,
        (
            listing.source,
            external_id,
            listing.title,
            listing.url,
            listing.price_pcm,
            _to_db_bool(listing.bills_included),
            _to_db_bool(listing.internet_included),
            listing.bedrooms,
            listing.bathrooms,
            listing.property_type,
            listing.address_text,
            listing.postcode,
            listing.lat,
            listing.lon,
            listing.available_from,
            listing.description,
            listing.area,
            listing.walking_minutes,
            listing.campus_distance_km,
            listing.safety_band,
            listing.all_in_estimate_pcm,
            listing.score,
            status,
            listing.rejection_reason,
            notes,
            listing.raw_json,
            first_seen,
            now,
            listing.notes,
        ),
    )


def upsert_many(db_path: str | Path, listings: Iterable[Listing]) -> int:
    init_db(db_path)
    count = 0
    with get_connection(db_path) as conn:
        for listing in listings:
            upsert_listing(conn, listing)
            count += 1
        conn.commit()
    return count


def load_listings_df(db_path: str | Path, include_archived: bool = False) -> pd.DataFrame:
    init_db(db_path)
    query = "SELECT * FROM listings"
    params: tuple = ()
    if not include_archived:
        query += " WHERE status != ?"
        params = ("archived",)
    query += " ORDER BY COALESCE(score, 0) DESC, last_seen DESC"
    with get_connection(db_path) as conn:
        df = pd.read_sql_query(query, conn, params=params)
    for col in ["bills_included", "internet_included"]:
        if col in df.columns:
            df[col] = df[col].map({1: True, 0: False}).astype("object")
    return df


def update_listing_status(
    db_path: str | Path,
    listing_id: int,
    status: str,
    rejection_reason: str | None = None,
    notes: str | None = None,
) -> None:
    init_db(db_path)
    with get_connection(db_path) as conn:
        conn.execute(
            """
            UPDATE listings
            SET status = ?, rejection_reason = COALESCE(?, rejection_reason), notes = COALESCE(?, notes)
            WHERE id = ?
            """,
            (status, rejection_reason, notes, listing_id),
        )
        conn.commit()


def insert_run_log(
    conn: sqlite3.Connection,
    started_at: str,
    source: str | None,
    status: str,
    message: str | None,
    inserted_or_updated: int = 0,
    finished_at: str | None = None,
    quality_status: str | None = None,
    metrics: dict | None = None,
) -> None:
    metrics_json = json.dumps(metrics, ensure_ascii=False) if metrics is not None else None
    conn.execute(
        """
        INSERT INTO run_log (
            started_at, finished_at, source, status, quality_status, metrics_json, message, inserted_or_updated
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            started_at,
            finished_at,
            source,
            status,
            quality_status,
            metrics_json,
            message,
            inserted_or_updated,
        ),
    )


def load_run_log_df(db_path: str | Path, limit: int = 50) -> pd.DataFrame:
    init_db(db_path)
    with get_connection(db_path) as conn:
        return pd.read_sql_query(
            "SELECT * FROM run_log ORDER BY started_at DESC LIMIT ?",
            conn,
            params=(limit,),
        )


def create_scrape_backup(
    db_path: str | Path,
    backups_dir: str | Path,
    trigger: str,
) -> Path:
    init_db(db_path)
    scraped_at = now_utc_iso()
    safe_timestamp = scraped_at.replace(":", "-").replace("+00:00", "Z")

    with get_connection(db_path) as conn:
        listings_df = pd.read_sql_query(
            "SELECT * FROM listings ORDER BY last_seen DESC",
            conn,
        )

    records = json.loads(listings_df.to_json(orient="records", date_format="iso"))
    payload = {
        "scraped_at": scraped_at,
        "trigger": trigger,
        "listing_count": len(records),
        "listings": records,
    }

    backup_dir_path = Path(backups_dir)
    backup_dir_path.mkdir(parents=True, exist_ok=True)
    snapshot_path = backup_dir_path / f"scrape_backup_{safe_timestamp}.json"
    latest_path = backup_dir_path / "latest_scrape_backup.json"

    with snapshot_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    with latest_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO scrape_backups (scraped_at, trigger, file_path, listing_count)
            VALUES (?, ?, ?, ?)
            """,
            (scraped_at, trigger, str(snapshot_path), len(records)),
        )
        conn.commit()

    return snapshot_path


def load_scrape_backups_df(db_path: str | Path, limit: int = 20) -> pd.DataFrame:
    init_db(db_path)
    with get_connection(db_path) as conn:
        return pd.read_sql_query(
            "SELECT * FROM scrape_backups ORDER BY scraped_at DESC LIMIT ?",
            conn,
            params=(limit,),
        )
