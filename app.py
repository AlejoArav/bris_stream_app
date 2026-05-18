from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import streamlit as st

from housing_dashboard.config import CONFIG
from housing_dashboard.db import (
    get_connection,
    init_db,
    load_listings_df,
    load_run_log_df,
    update_listing_status,
    upsert_listing,
)
from housing_dashboard.enrichment.geocode import enrich_coordinates
from housing_dashboard.models import Listing
from housing_dashboard.scoring import enrich_and_score_listing
from housing_dashboard.text_utils import (
    extract_postcode,
    infer_property_type,
    parse_bool,
    parse_bedrooms,
    parse_price_pcm,
    stable_id,
)

st.set_page_config(page_title="Bristol Housing Dashboard", page_icon="🏠", layout="wide")
init_db(CONFIG.database_path)


def bool_display(value):
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "unknown"


def normalise_import_row(row: pd.Series, source_default: str = "manual") -> Listing:
    title = str(row.get("title") or row.get("Title") or "Untitled listing")
    url = str(row.get("url") or row.get("URL") or "")
    source = str(row.get("source") or source_default)
    description = str(row.get("description") or row.get("Description") or "") or None
    address = str(row.get("address_text") or row.get("address") or row.get("Address") or "") or None
    price_raw = row.get("price_pcm") if "price_pcm" in row else row.get("price")
    price_pcm = None
    if pd.notna(price_raw):
        try:
            price_pcm = float(price_raw)
        except Exception:
            price_pcm = parse_price_pcm(str(price_raw))

    bedrooms_raw = row.get("bedrooms")
    bedrooms = None
    if pd.notna(bedrooms_raw):
        try:
            bedrooms = float(bedrooms_raw)
        except Exception:
            bedrooms = parse_bedrooms(str(bedrooms_raw))

    postcode = row.get("postcode") if "postcode" in row else extract_postcode(address)
    postcode = str(postcode) if pd.notna(postcode) else None

    bills = parse_bool(row.get("bills_included")) if "bills_included" in row else None
    internet = parse_bool(row.get("internet_included")) if "internet_included" in row else None
    ptype = row.get("property_type") if "property_type" in row else infer_property_type(title, description)
    ptype = str(ptype) if pd.notna(ptype) else None

    lat = row.get("lat") if "lat" in row else None
    lon = row.get("lon") if "lon" in row else None
    try:
        lat = float(lat) if pd.notna(lat) else None
        lon = float(lon) if pd.notna(lon) else None
    except Exception:
        lat, lon = None, None

    listing = Listing(
        source=source,
        external_id=str(row.get("external_id") or stable_id(source, url, title)),
        title=title,
        url=url,
        price_pcm=price_pcm,
        bills_included=bills,
        internet_included=internet,
        bedrooms=bedrooms,
        property_type=ptype,
        address_text=address,
        postcode=postcode,
        lat=lat,
        lon=lon,
        available_from=str(row.get("available_from")) if "available_from" in row and pd.notna(row.get("available_from")) else None,
        description=description,
        notes=str(row.get("notes")) if "notes" in row and pd.notna(row.get("notes")) else None,
    )
    return enrich_and_score_listing(listing)


def save_listing(listing: Listing) -> None:
    with get_connection(CONFIG.database_path) as conn:
        upsert_listing(conn, listing)
        conn.commit()


def filter_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    with st.sidebar:
        st.header("Filters")
        max_price = st.slider("Max all-in estimate (£/month)", 800, 2000, int(CONFIG.max_all_in_pcm), 25)
        max_walk = st.slider("Max walking minutes", 5, 90, int(CONFIG.max_walking_minutes), 5)
        statuses = st.multiselect(
            "Status",
            sorted(df["status"].dropna().unique().tolist()),
            default=[s for s in ["active", "maybe", "contacted", "viewing", "applied"] if s in df["status"].unique()],
        )
        self_contained_only = st.checkbox("Self-contained only", value=True)
        show_unknown_walk = st.checkbox("Keep listings with unknown walking time", value=True)
        show_unknown_price = st.checkbox("Keep listings with unknown price", value=False)

    out = df.copy()
    if statuses:
        out = out[out["status"].isin(statuses)]

    if show_unknown_price:
        out = out[(out["all_in_estimate_pcm"].isna()) | (out["all_in_estimate_pcm"] <= max_price)]
    else:
        out = out[out["all_in_estimate_pcm"].notna() & (out["all_in_estimate_pcm"] <= max_price)]

    if show_unknown_walk:
        out = out[(out["walking_minutes"].isna()) | (out["walking_minutes"] <= max_walk)]
    else:
        out = out[out["walking_minutes"].notna() & (out["walking_minutes"] <= max_walk)]

    if self_contained_only:
        text = (
            out["title"].fillna("") + " " + out["property_type"].fillna("") + " " + out["description"].fillna("")
        ).str.lower()
        reject = text.str.contains("house share|shared house|room in|single occupancy|flat share|hmo room", regex=True)
        positive = text.str.contains("studio|1 bed|1 bedroom|one bedroom|flat|apartment|self-contained|self contained", regex=True)
        out = out[positive & ~reject]

    return out.sort_values(["score", "last_seen"], ascending=[False, False])


st.title("🏠 Bristol Housing Dashboard")
st.caption(
    f"Target: £{CONFIG.max_all_in_pcm:,.0f}/month all-in, self-contained studio/1-bed, "
    f"≤{CONFIG.max_walking_minutes:.0f} min walk to {CONFIG.target_label}."
)

listings_df = load_listings_df(CONFIG.database_path)
filtered_df = filter_df(listings_df)

tab_best, tab_map, tab_add, tab_import, tab_runs, tab_settings = st.tabs(
    ["Best matches", "Map", "Add listing", "CSV import", "Run log", "Settings"]
)

with tab_best:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Listings", len(filtered_df))
    c2.metric("Active DB total", len(listings_df))
    if not filtered_df.empty:
        c3.metric("Best score", f"{filtered_df['score'].max():.1f}")
        median_price = filtered_df["all_in_estimate_pcm"].dropna().median()
        c4.metric("Median all-in", "unknown" if pd.isna(median_price) else f"£{median_price:,.0f}")
    else:
        c3.metric("Best score", "—")
        c4.metric("Median all-in", "—")

    if filtered_df.empty:
        st.info("No listings match the current filters yet. Add listings manually or import a CSV to start.")
    else:
        display_cols = [
            "id",
            "score",
            "title",
            "source",
            "all_in_estimate_pcm",
            "price_pcm",
            "bills_included",
            "internet_included",
            "bedrooms",
            "property_type",
            "area",
            "walking_minutes",
            "safety_band",
            "available_from",
            "status",
            "url",
            "last_seen",
        ]
        st.dataframe(
            filtered_df[[c for c in display_cols if c in filtered_df.columns]],
            hide_index=True,
            column_config={
                "url": st.column_config.LinkColumn("URL"),
                "all_in_estimate_pcm": st.column_config.NumberColumn("All-in estimate", format="£%.0f"),
                "price_pcm": st.column_config.NumberColumn("Rent", format="£%.0f"),
                "score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.1f"),
            },
            use_container_width=True,
        )

        st.subheader("Update a listing")
        selected_id = st.number_input("Listing ID", min_value=1, value=int(filtered_df.iloc[0]["id"]))
        new_status = st.selectbox("New status", ["active", "maybe", "contacted", "viewing", "applied", "rejected", "archived"])
        reason = st.text_input("Rejection reason / note label", placeholder="too expensive, too far, not self-contained...")
        notes = st.text_area("Notes", placeholder="Landlord response, viewing date, details to check...")
        if st.button("Save status update"):
            update_listing_status(CONFIG.database_path, int(selected_id), new_status, reason or None, notes or None)
            st.success("Listing updated. Refresh the app to see changes.")

with tab_map:
    map_df = filtered_df.dropna(subset=["lat", "lon"]).copy() if not filtered_df.empty else pd.DataFrame()
    if map_df.empty:
        st.info("No listings have coordinates yet. Add lat/lon manually or enable online enrichment for postcode/address geocoding.")
    else:
        st.map(map_df.rename(columns={"lat": "latitude", "lon": "longitude"}), latitude="latitude", longitude="longitude")
        st.dataframe(
            map_df[["title", "all_in_estimate_pcm", "walking_minutes", "area", "url"]],
            hide_index=True,
            column_config={"url": st.column_config.LinkColumn("URL")},
            use_container_width=True,
        )

with tab_add:
    st.subheader("Add one listing manually")
    with st.form("manual_listing"):
        source = st.text_input("Source", value="manual")
        title = st.text_input("Title")
        url = st.text_input("URL")
        price_pcm = st.number_input("Rent pcm (£)", min_value=0.0, value=0.0, step=25.0)
        bills_included = st.selectbox("Bills included?", ["unknown", "yes", "no"])
        internet_included = st.selectbox("Internet included?", ["unknown", "yes", "no"])
        bedrooms = st.selectbox("Bedrooms", ["unknown", "studio", "1", "2+"])
        property_type = st.text_input("Property type", placeholder="studio, 1 bedroom flat, apartment...")
        address_text = st.text_input("Address / area text")
        postcode = st.text_input("Postcode", placeholder="Optional")
        available_from = st.text_input("Available from", placeholder="e.g. September 2026")
        lat = st.text_input("Latitude", placeholder="Optional")
        lon = st.text_input("Longitude", placeholder="Optional")
        description = st.text_area("Description")
        submitted = st.form_submit_button("Add listing")

    if submitted:
        if not title or not url:
            st.error("Title and URL are required.")
        else:
            bmap = {"yes": True, "no": False, "unknown": None}
            bedroom_value = None
            if bedrooms == "studio":
                bedroom_value = 0.0
            elif bedrooms == "1":
                bedroom_value = 1.0
            elif bedrooms == "2+":
                bedroom_value = 2.0

            listing = Listing(
                source=source,
                external_id=stable_id(source, url, title),
                title=title,
                url=url,
                price_pcm=price_pcm or None,
                bills_included=bmap[bills_included],
                internet_included=bmap[internet_included],
                bedrooms=bedroom_value,
                property_type=property_type or infer_property_type(title, description),
                address_text=address_text or None,
                postcode=postcode or extract_postcode(address_text),
                lat=float(lat) if lat else None,
                lon=float(lon) if lon else None,
                available_from=available_from or None,
                description=description or None,
            )
            if (listing.lat is None or listing.lon is None) and CONFIG.enable_online_enrichment:
                listing.lat, listing.lon, listing.postcode = enrich_coordinates(
                    listing.address_text,
                    listing.postcode,
                    CONFIG.nominatim_user_agent,
                    CONFIG.enable_online_enrichment,
                )
            listing = enrich_and_score_listing(listing)
            save_listing(listing)
            st.success("Listing added. Refresh the app to see it in the table.")

with tab_import:
    st.subheader("Import listings from CSV")
    st.write("Use `examples/manual_import_template.csv` as a starting point.")
    uploaded = st.file_uploader("Upload CSV", type=["csv"])
    if uploaded is not None:
        df_import = pd.read_csv(uploaded)
        st.dataframe(df_import.head(20), use_container_width=True)
        if st.button("Import rows"):
            count = 0
            with get_connection(CONFIG.database_path) as conn:
                for _, row in df_import.iterrows():
                    listing = normalise_import_row(row)
                    if (listing.lat is None or listing.lon is None) and CONFIG.enable_online_enrichment:
                        listing.lat, listing.lon, listing.postcode = enrich_coordinates(
                            listing.address_text,
                            listing.postcode,
                            CONFIG.nominatim_user_agent,
                            CONFIG.enable_online_enrichment,
                        )
                        listing = enrich_and_score_listing(listing)
                    upsert_listing(conn, listing)
                    count += 1
                conn.commit()
            st.success(f"Imported {count} rows.")

    template = pd.DataFrame([
        {
            "source": "manual",
            "title": "Example 1 bed flat near Cotham",
            "url": "https://example.com/listing",
            "price_pcm": 1200,
            "bills_included": "no",
            "internet_included": "no",
            "bedrooms": 1,
            "property_type": "1 bedroom flat",
            "address_text": "Cotham, Bristol",
            "postcode": "",
            "available_from": "September 2026",
            "description": "Self-contained flat suitable for a couple.",
            "notes": "Example only",
        }
    ])
    csv_bytes = template.to_csv(index=False).encode("utf-8")
    st.download_button("Download CSV template", data=csv_bytes, file_name="manual_import_template.csv", mime="text/csv")

with tab_runs:
    st.subheader("Scraper run log")
    log_df = load_run_log_df(CONFIG.database_path)
    if log_df.empty:
        st.info("No scraper runs logged yet.")
    else:
        st.dataframe(log_df, hide_index=True, use_container_width=True)

with tab_settings:
    st.subheader("Current configuration")
    st.json({
        "database_path": str(CONFIG.database_path),
        "max_all_in_pcm": CONFIG.max_all_in_pcm,
        "soft_max_all_in_pcm": CONFIG.soft_max_all_in_pcm,
        "max_walking_minutes": CONFIG.max_walking_minutes,
        "ideal_walking_minutes": CONFIG.ideal_walking_minutes,
        "expected_bills_pcm": CONFIG.expected_bills_pcm,
        "expected_internet_pcm": CONFIG.expected_internet_pcm,
        "scraper_interval_hours": CONFIG.scraper_interval_hours,
        "enable_online_enrichment": CONFIG.enable_online_enrichment,
        "target_label": CONFIG.target_label,
        "target_lat": CONFIG.target_lat,
        "target_lon": CONFIG.target_lon,
        "preferred_areas": CONFIG.preferred_areas,
        "caution_areas": CONFIG.caution_areas,
    })
    st.markdown(
        "Edit `.env` to change these settings. The scraping cadence is controlled by "
        "`SCRAPER_INTERVAL_HOURS`, currently set to "
        f"**{CONFIG.scraper_interval_hours} hours**."
    )
