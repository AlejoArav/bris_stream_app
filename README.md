# Bristol Housing Dashboard

A local Streamlit dashboard for tracking self-contained Bristol accommodation for a couple moving near the University of Bristol.

The default configuration is tuned for:

- **Budget:** £1,350/month all-in, including bills and internet.
- **Property type:** studio or 1-bedroom apartment/flat; no house sharing.
- **Location target:** University of Bristol Clifton campus / Beacon House area.
- **Walking time:** ideally <=30 minutes, hard threshold <=45 minutes.
- **Scraping cadence:** every 12 hours by default.

The project is intentionally modular:

1. The dashboard reads from a local SQLite database.
2. Scrapers run separately and upsert listings into the database.
3. Enrichment and scoring can work offline using approximate distance estimates, or online using optional APIs.
4. You can start with manual/CSV imports and add source-specific scrapers later.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
streamlit run app.py
```

Then open the displayed local URL, normally:

```text
http://localhost:8501
```

## Docker quick start

```bash
docker compose up --build dashboard
```

Open:

```text
http://localhost:8501
```

To run the scheduled scraper/enrichment worker as well:

```bash
docker compose up --build
```

The scheduler defaults to running every **12 hours**. Change it in `.env`:

```env
SCRAPER_INTERVAL_HOURS=12
```

## Repository tree

```text
bristol-housing-dashboard/
├── app.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── data/
│   └── .gitkeep
├── examples/
│   ├── manual_import_template.csv
│   └── sources.example.yaml
└── housing_dashboard/
    ├── config.py
    ├── db.py
    ├── models.py
    ├── scoring.py
    ├── text_utils.py
    ├── enrichment/
    │   ├── __init__.py
    │   ├── geocode.py
    │   └── walking_time.py
    └── scrapers/
        ├── __init__.py
        ├── base.py
        ├── run_all.py
        ├── scheduler.py
        └── static_css.py
```

## Important scraping note

This tool is designed for **low-frequency personal use**. Before enabling a source:

- Check the site's terms of use and robots.txt.
- Do not bypass logins, CAPTCHAs, paywalls, or anti-bot systems.
- Keep the interval conservative. The default is 12 hours.
- Prefer official alerts, RSS feeds, manual imports, or exported saved searches where possible.

The included CSS scraper is generic and only works for simple public/static listing pages. Many commercial portals use dynamic JavaScript and may prohibit automated scraping. For those, use saved-search emails or manual import.

## Configuration

Copy `.env.example` to `.env` and edit values.

Key values:

```env
MAX_ALL_IN_PCM=1350
MAX_WALKING_MINUTES=45
IDEAL_WALKING_MINUTES=30
EXPECTED_BILLS_PCM=180
EXPECTED_INTERNET_PCM=30
SCRAPER_INTERVAL_HOURS=12
RUN_ON_STARTUP=true
SCRAPE_BACKUPS_DIR=data/scrape_backups
ENABLE_ONLINE_ENRICHMENT=false
```

The default target coordinates are approximate for the University of Bristol Clifton campus / Beacon House area. You can adjust them in `.env`.

## CSV import

Use `examples/manual_import_template.csv`. Minimum useful columns:

```text
source,title,url,price_pcm,bills_included,internet_included,bedrooms,property_type,address_text,postcode,available_from,description
```

Accepted boolean values include `true`, `false`, `yes`, `no`, `1`, `0`.

## Source configuration

Copy `examples/sources.example.yaml` to `sources.yaml`, then edit CSS selectors for each source you are legally and technically allowed to scrape.

Notes:

- Set `enabled: true` for at least one source, otherwise the scraper exits immediately with no updates.
- You can provide selector alternatives with `||`, for example: `card: ".property-card || .listing-card || article"`.
- Keep `fallback_to_similar_selectors: true` (default) to let the scraper try common property/listing selectors when configured selectors do not match.
- Keep `enable_whole_page_block_discovery: true` (default) to scan full-page HTML for property-like blocks when card selectors fail.
- `allow_if_robots_unavailable: true` (default) allows scraping when robots.txt cannot be fetched.
- `ignore_robots_restrictions: true` (default) allows scraping even if robots disallows the path.

Run once manually:

```bash
python -m housing_dashboard.scrapers.run_all --sources sources.yaml
```

Run scheduler:

```bash
python -m housing_dashboard.scrapers.scheduler --sources sources.yaml
```

## App-triggered scraping and backups

- The Streamlit app can run one scrape automatically at startup when `RUN_ON_STARTUP=true`.
- In the **Run log** tab, use **Run web scrape now** for on-demand scraping.
- Every scrape writes a timestamped JSON backup and updates `latest_scrape_backup.json` in `SCRAPE_BACKUPS_DIR`.

## How scoring works

Listings are estimated as an all-in monthly cost:

```text
all_in_estimate = rent + expected bills if bills not included + expected internet if internet not included
```

The score prioritizes:

- Price/all-in affordability: 35%
- Walking time: 25%
- Area/safety band: 20%
- Couple/self-contained fit: 15%
- September availability: 5%

You can adjust this in `housing_dashboard/scoring.py`.

## Next recommended improvements

- Add a postcode-to-ward lookup table for better safety scoring.
- Add source-specific parsers for Bristol SU Lettings, OpenRent saved searches, or agency pages that allow scraping.
- Add email/Telegram notifications for new high-score listings.
- Add a listing notes/history page to track viewings, emails sent, and landlord responses.
