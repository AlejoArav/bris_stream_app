from __future__ import annotations

import json
import re
import urllib.robotparser
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ..models import Listing
from ..scoring import enrich_and_score_listing
from ..text_utils import (
    clean_text,
    extract_postcode,
    infer_property_type,
    parse_bedrooms,
    parse_bool,
    parse_price_pcm,
    stable_id,
)
from .base import BaseScraper, ScrapeResult, deduplicate_listings


@dataclass
class StaticCssScraper(BaseScraper):
    """Generic scraper for simple public static listing pages.

    The scraper is configured with CSS selectors. It is intentionally basic and
    conservative. It should not be used for pages that prohibit scraping or rely
    on anti-bot mechanisms.
    """

    source_name: str
    url: str
    selectors: dict[str, str]
    user_agent: str = "bristol-housing-dashboard/0.1 personal-use"
    timeout: int = 20
    respect_robots_txt: bool = True

    def _allowed_by_robots(self) -> bool:
        if not self.respect_robots_txt:
            return True
        parsed = urlparse(self.url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        rp = urllib.robotparser.RobotFileParser()
        try:
            rp.set_url(robots_url)
            rp.read()
            return rp.can_fetch(self.user_agent, self.url)
        except Exception:
            # If robots cannot be fetched, fail closed for safety.
            return False

    def _get_text(self, node: Any, selector_name: str) -> str | None:
        selector = self.selectors.get(selector_name)
        if not selector:
            return None
        found = node.select_one(selector)
        if not found:
            return None
        return clean_text(found.get_text(" ", strip=True))

    def _get_href(self, node: Any, selector_name: str = "url") -> str | None:
        selector = self.selectors.get(selector_name)
        if not selector:
            return None
        found = node.select_one(selector)
        if not found:
            return None
        href = found.get("href")
        if not href:
            return None
        return urljoin(self.url, href)

    def scrape(self) -> ScrapeResult:
        if not self._allowed_by_robots():
            return ScrapeResult(
                source=self.source_name,
                listings=[],
                message="Skipped: robots.txt did not allow this user agent, or robots.txt was unavailable.",
            )

        headers = {"User-Agent": self.user_agent}
        response = requests.get(self.url, headers=headers, timeout=self.timeout)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        card_selector = self.selectors["card"]
        cards = soup.select(card_selector)

        listings: list[Listing] = []
        for idx, card in enumerate(cards):
            title = self._get_text(card, "title") or f"Untitled listing {idx + 1}"
            listing_url = self._get_href(card, "url") or self.url
            price_text = self._get_text(card, "price")
            address_text = self._get_text(card, "address")
            description = self._get_text(card, "description")
            available_from = self._get_text(card, "available_from")
            bedrooms_text = self._get_text(card, "bedrooms") or title
            bills_text = self._get_text(card, "bills_included")
            internet_text = self._get_text(card, "internet_included")

            combined = " ".join(filter(None, [title, price_text, address_text, description]))
            price_pcm = parse_price_pcm(price_text or combined)
            bedrooms = parse_bedrooms(bedrooms_text or combined)
            postcode = extract_postcode(address_text or combined)
            property_type = infer_property_type(title, description)

            raw = {
                "source_url": self.url,
                "price_text": price_text,
                "address_text": address_text,
                "selectors": self.selectors,
            }

            listing = Listing(
                source=self.source_name,
                external_id=stable_id(self.source_name, listing_url, title),
                title=title,
                url=listing_url,
                price_pcm=price_pcm,
                bills_included=parse_bool(bills_text),
                internet_included=parse_bool(internet_text),
                bedrooms=bedrooms,
                property_type=property_type,
                address_text=address_text,
                postcode=postcode,
                available_from=available_from,
                description=description,
                raw_json=json.dumps(raw, ensure_ascii=False),
            )
            listings.append(enrich_and_score_listing(listing))

        listings = deduplicate_listings(listings)
        return ScrapeResult(
            source=self.source_name,
            listings=listings,
            message=f"Fetched {len(listings)} listings from {len(cards)} cards.",
        )


def build_static_scrapers_from_yaml(path: str) -> list[StaticCssScraper]:
    import yaml

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    scrapers: list[StaticCssScraper] = []
    for source in data.get("sources", []):
        if not source.get("enabled", False):
            continue
        if source.get("type") != "static_css":
            continue
        scrapers.append(
            StaticCssScraper(
                source_name=source["name"],
                url=source["url"],
                selectors=source["selectors"],
                user_agent=source.get("user_agent", "bristol-housing-dashboard/0.1 personal-use"),
                respect_robots_txt=source.get("respect_robots_txt", True),
            )
        )
    return scrapers
