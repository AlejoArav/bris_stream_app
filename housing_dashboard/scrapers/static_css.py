from __future__ import annotations

import json
import re
import urllib.robotparser
from collections import defaultdict
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

FIELD_KEYWORDS: dict[str, list[str]] = {
    "title": ["title", "name", "headline", "property"],
    "url": ["link", "url", "href", "details"],
    "price": ["price", "rent", "pcm", "per-month", "permonth", "pw", "weekly"],
    "address": ["address", "location", "area", "postcode"],
    "description": ["description", "summary", "details", "excerpt"],
    "available_from": ["available", "move", "date"],
    "bedrooms": ["bed", "bedroom", "beds", "room"],
    "bills_included": ["bills", "bill", "utilities", "included"],
    "internet_included": ["internet", "wifi", "broadband", "included"],
}

FIELD_FALLBACK_SELECTORS: dict[str, list[str]] = {
    "title": [
        "[class*='title']",
        "[data-testid*='title']",
        "h1, h2, h3",
        "a[title]",
    ],
    "url": [
        "a[href]",
    ],
    "price": [
        "[class*='price']",
        "[data-testid*='price']",
        "[aria-label*='price']",
    ],
    "address": [
        "[class*='address']",
        "[class*='location']",
        "[data-testid*='address']",
    ],
    "description": [
        "[class*='description']",
        "[data-testid*='description']",
        "p",
    ],
    "available_from": [
        "[class*='available']",
        "[class*='date']",
        "[data-testid*='available']",
    ],
    "bedrooms": [
        "[class*='bed']",
        "[data-testid*='bed']",
        "[aria-label*='bed']",
    ],
    "bills_included": [
        "[class*='bills']",
        "[data-testid*='bills']",
    ],
    "internet_included": [
        "[class*='internet']",
        "[class*='wifi']",
        "[data-testid*='internet']",
    ],
}

CARD_FALLBACK_SELECTORS: list[str] = [
    "[class*='property-card']",
    "[class*='listing-card']",
    "[class*='property-item']",
    "[class*='listing-item']",
    "[data-testid*='property-card']",
    "[data-testid*='listing-card']",
    "article[class*='property']",
    "article[class*='listing']",
    "li[class*='property']",
    "li[class*='listing']",
    "article",
]

PROPERTY_BLOCK_HINTS_RE = re.compile(
    r"\b(studio|flat|apartment|bed|bedroom|to let|for rent|pcm|per month|pw)\b",
    re.I,
)


@dataclass
class StaticCssScraper(BaseScraper):
    """Generic scraper for simple public static listing pages.

    The scraper is configured with CSS selectors. It is intentionally basic and
    conservative. It should not be used for pages that prohibit scraping or rely
    on anti-bot mechanisms.
    """

    source_name: str
    url: str
    selectors: dict[str, str | list[str]]
    user_agent: str = "bristol-housing-dashboard/0.1 personal-use"
    timeout: int = 20
    respect_robots_txt: bool = True
    allow_if_robots_unavailable: bool = True
    ignore_robots_restrictions: bool = True
    fallback_to_similar_selectors: bool = True
    enable_whole_page_block_discovery: bool = True

    def _allowed_by_robots(self) -> tuple[bool, str | None]:
        if not self.respect_robots_txt:
            return True, "Robots checks disabled by config."
        parsed = urlparse(self.url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        rp = urllib.robotparser.RobotFileParser()
        try:
            rp.set_url(robots_url)
            rp.read()
            if rp.can_fetch(self.user_agent, self.url):
                return True, None
            if self.ignore_robots_restrictions:
                return True, "Robots disallowed this path; continuing due ignore_robots_restrictions=true."
            return False, "Skipped: robots.txt disallowed this user agent/path."
        except Exception:
            if self.allow_if_robots_unavailable:
                return True, "robots.txt unavailable; continuing due allow_if_robots_unavailable=true."
            return False, "Skipped: robots.txt unavailable and allow_if_robots_unavailable=false."

    @staticmethod
    def _extract_near_keyword(text: str, keywords: list[str], span: int = 48) -> str | None:
        low = text.lower()
        for keyword in keywords:
            idx = low.find(keyword)
            if idx >= 0:
                start = max(0, idx - span)
                end = min(len(text), idx + len(keyword) + span)
                return text[start:end].strip()
        return None

    def _configured_selector_candidates(self, selector_name: str) -> list[str]:
        raw = self.selectors.get(selector_name)
        if raw is None:
            return []
        if isinstance(raw, str):
            return [part.strip() for part in raw.split("||") if part.strip()]
        if isinstance(raw, list):
            out: list[str] = []
            for part in raw:
                if part is None:
                    continue
                value = str(part).strip()
                if value:
                    out.append(value)
            return out
        value = str(raw).strip()
        return [value] if value else []

    def _selector_candidates(self, selector_name: str, include_fallback: bool) -> list[str]:
        candidates: list[str] = []
        for selector in self._configured_selector_candidates(selector_name):
            if selector not in candidates:
                candidates.append(selector)
        if include_fallback:
            for selector in FIELD_FALLBACK_SELECTORS.get(selector_name, []):
                if selector not in candidates:
                    candidates.append(selector)
            for keyword in FIELD_KEYWORDS.get(selector_name, []):
                for selector in [
                    f"[class*='{keyword}']",
                    f"[id*='{keyword}']",
                    f"[data-testid*='{keyword}']",
                    f"[aria-label*='{keyword}']",
                    f"[itemprop*='{keyword}']",
                ]:
                    if selector not in candidates:
                        candidates.append(selector)
        return candidates

    def _select_first(self, node: Any, selectors: list[str]) -> tuple[Any | None, str | None]:
        for selector in selectors:
            try:
                found = node.select_one(selector)
            except Exception:
                continue
            if found is not None:
                return found, selector
        return None, None

    def _get_text(self, node: Any, selector_name: str, include_fallback: bool) -> tuple[str | None, str | None]:
        found, used_selector = self._select_first(node, self._selector_candidates(selector_name, include_fallback))
        if not found:
            return None, None
        return clean_text(found.get_text(" ", strip=True)), used_selector

    def _get_href(
        self,
        node: Any,
        selector_name: str = "url",
        include_fallback: bool = True,
    ) -> tuple[str | None, str | None]:
        found, used_selector = self._select_first(node, self._selector_candidates(selector_name, include_fallback))
        if not found:
            return None, None
        href = found.get("href")
        if not href:
            return None, used_selector
        return urljoin(self.url, href), used_selector

    @staticmethod
    def _score_cards(cards: list[Any]) -> float:
        if not cards:
            return 0.0
        sample = cards[: min(len(cards), 30)]
        score = 0.0
        for card in sample:
            text = (clean_text(card.get_text(" ", strip=True)) or "").lower()
            if re.search(r"£\s?\d{2,6}", text):
                score += 3.0
            if "bed" in text:
                score += 1.0
            if card.select_one("a[href]"):
                score += 1.0
        return score / len(sample)

    def _select_cards(self, soup: BeautifulSoup) -> tuple[list[Any], str | None, list[str], bool]:
        attempted: list[str] = []
        for selector in self._configured_selector_candidates("card"):
            attempted.append(selector)
            try:
                cards = soup.select(selector)
            except Exception:
                continue
            if cards:
                return cards, selector, attempted, False

        if not self.fallback_to_similar_selectors:
            return [], None, attempted, False

        best_selector: str | None = None
        best_cards: list[Any] = []
        best_score = 0.0
        for selector in CARD_FALLBACK_SELECTORS:
            if selector in attempted:
                continue
            attempted.append(selector)
            try:
                cards = soup.select(selector)
            except Exception:
                continue
            if len(cards) < 2 or len(cards) > 500:
                continue
            score = self._score_cards(cards)
            if score > best_score:
                best_score = score
                best_selector = selector
                best_cards = cards

        if best_selector and best_cards:
            return best_cards, best_selector, attempted, True
        if self.enable_whole_page_block_discovery:
            discovered_cards = self._discover_cards_by_content(soup)
            if discovered_cards:
                attempted.append("__whole_page_block_discovery__")
                return discovered_cards, "__whole_page_block_discovery__", attempted, True
        return [], None, attempted, True

    def _discover_cards_by_content(self, soup: BeautifulSoup) -> list[Any]:
        candidates: list[Any] = []
        for node in soup.find_all(["article", "li", "div", "section"]):
            text = clean_text(node.get_text(" ", strip=True)) or ""
            if len(text) < 30 or len(text) > 1800:
                continue
            if not re.search(r"£\s?\d{2,6}", text):
                continue
            if not PROPERTY_BLOCK_HINTS_RE.search(text):
                continue
            if not node.find("a", href=True):
                continue
            candidates.append(node)
            if len(candidates) >= 450:
                break

        if not candidates:
            return []

        candidate_ids = {id(node) for node in candidates}
        deduped: list[Any] = []
        for node in candidates:
            parent_candidate = node.find_parent(lambda parent: id(parent) in candidate_ids)
            if parent_candidate is None:
                deduped.append(node)

        if len(deduped) < 2:
            return []
        return deduped[:200]

    def scrape(self) -> ScrapeResult:
        allowed, robots_note = self._allowed_by_robots()
        if not allowed:
            return ScrapeResult(
                source=self.source_name,
                listings=[],
                message=robots_note or "Skipped due robots policy.",
            )

        headers = {"User-Agent": self.user_agent}
        response = requests.get(self.url, headers=headers, timeout=self.timeout)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        cards, card_selector_used, attempted_card_selectors, used_card_fallback = self._select_cards(soup)
        selector_usage: dict[str, set[str]] = defaultdict(set)

        listings: list[Listing] = []
        for idx, card in enumerate(cards):
            card_text = clean_text(card.get_text(" ", strip=True)) or ""
            title, title_selector = self._get_text(card, "title", include_fallback=self.fallback_to_similar_selectors)
            if title_selector:
                selector_usage["title"].add(title_selector)
            title = title or f"Untitled listing {idx + 1}"

            listing_url, url_selector = self._get_href(card, "url", include_fallback=self.fallback_to_similar_selectors)
            if url_selector:
                selector_usage["url"].add(url_selector)
            listing_url = listing_url or self.url

            price_text, price_selector = self._get_text(card, "price", include_fallback=self.fallback_to_similar_selectors)
            if price_selector:
                selector_usage["price"].add(price_selector)
            if not price_text:
                price_text = self._extract_near_keyword(card_text, FIELD_KEYWORDS["price"])

            address_text, address_selector = self._get_text(
                card,
                "address",
                include_fallback=self.fallback_to_similar_selectors,
            )
            if address_selector:
                selector_usage["address"].add(address_selector)
            if not address_text:
                address_text = self._extract_near_keyword(card_text, FIELD_KEYWORDS["address"])

            description, description_selector = self._get_text(
                card,
                "description",
                include_fallback=self.fallback_to_similar_selectors,
            )
            if description_selector:
                selector_usage["description"].add(description_selector)
            if not description and card_text:
                description = card_text[:380]

            available_from, available_selector = self._get_text(
                card,
                "available_from",
                include_fallback=self.fallback_to_similar_selectors,
            )
            if available_selector:
                selector_usage["available_from"].add(available_selector)
            if not available_from:
                available_from = self._extract_near_keyword(card_text, FIELD_KEYWORDS["available_from"])

            bedrooms_text, bedrooms_selector = self._get_text(
                card,
                "bedrooms",
                include_fallback=self.fallback_to_similar_selectors,
            )
            if bedrooms_selector:
                selector_usage["bedrooms"].add(bedrooms_selector)
            bedrooms_text = bedrooms_text or self._extract_near_keyword(card_text, FIELD_KEYWORDS["bedrooms"]) or title

            bills_text, bills_selector = self._get_text(
                card,
                "bills_included",
                include_fallback=self.fallback_to_similar_selectors,
            )
            if bills_selector:
                selector_usage["bills_included"].add(bills_selector)
            if not bills_text:
                bills_text = self._extract_near_keyword(card_text, FIELD_KEYWORDS["bills_included"])

            internet_text, internet_selector = self._get_text(
                card,
                "internet_included",
                include_fallback=self.fallback_to_similar_selectors,
            )
            if internet_selector:
                selector_usage["internet_included"].add(internet_selector)
            if not internet_text:
                internet_text = self._extract_near_keyword(card_text, FIELD_KEYWORDS["internet_included"])

            combined = " ".join(filter(None, [title, price_text, address_text, description, card_text]))
            price_pcm = parse_price_pcm(price_text or combined)
            bedrooms = parse_bedrooms(bedrooms_text or combined)
            postcode = extract_postcode(address_text or combined)
            property_type = infer_property_type(title, description)

            raw = {
                "source_url": self.url,
                "price_text": price_text,
                "address_text": address_text,
                "selectors": self.selectors,
                "card_selector_used": card_selector_used,
                "selector_usage": {k: sorted(v) for k, v in selector_usage.items()},
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
        if not cards:
            return ScrapeResult(
                source=self.source_name,
                listings=listings,
                message=(
                    "Fetched 0 listings: no cards found. "
                    f"Attempted selectors: {attempted_card_selectors[:8]}"
                ),
            )

        fallback_note = ""
        if used_card_fallback:
            fallback_note = f" Used fallback card selector '{card_selector_used}'."
        elif card_selector_used:
            fallback_note = f" Used configured card selector '{card_selector_used}'."
        robots_note_text = f" {robots_note}" if robots_note else ""
        return ScrapeResult(
            source=self.source_name,
            listings=listings,
            message=f"Fetched {len(listings)} listings from {len(cards)} cards.{fallback_note}{robots_note_text}",
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
                allow_if_robots_unavailable=source.get("allow_if_robots_unavailable", True),
                ignore_robots_restrictions=source.get("ignore_robots_restrictions", True),
                fallback_to_similar_selectors=source.get("fallback_to_similar_selectors", True),
                enable_whole_page_block_discovery=source.get("enable_whole_page_block_discovery", True),
            )
        )
    return scrapers
