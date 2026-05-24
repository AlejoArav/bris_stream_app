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

from ..config import CONFIG
from ..models import Listing
from ..scoring import enrich_and_score_listing
from ..text_utils import (
    clean_text,
    extract_postcode,
    infer_property_type,
    parse_bedrooms,
    parse_bool,
    parse_price_pcm,
    resolve_location,
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
        "[data-cy*='title']",
        "h1, h2, h3",
        "a[title]",
    ],
    "url": [
        "a[href]",
    ],
    "price": [
        "[class*='price']",
        "[data-testid*='price']",
        "[data-cy*='price']",
        "[aria-label*='price']",
    ],
    "address": [
        "[class*='address']",
        "[class*='location']",
        "[data-testid*='address']",
        "[data-cy*='location']",
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

SOURCE_EXTRACTOR_PROFILES: dict[str, dict[str, list[str]]] = {
    "bristol su lettings - properties to let": {
        "card": [
            ".property-card",
            ".property-item",
            ".property",
            "article",
            "li[class*='property']",
        ],
        "title": [".property-title", "h2", "h3", "a[title]"],
        "url": ["a.property-link", "a[href*='property']", "a[href]"],
        "price": [".property-price", ".price", "[class*='price']"],
        "address": [".property-address", ".address", "[class*='address']"],
        "description": [".property-description", "p"],
        "available_from": [".available-date", "[class*='available']"],
        "bedrooms": [".bedrooms", "[class*='bed']"],
    },
    "openrent - bristol": {
        "card": [
            "div[data-cy='listing']",
            ".listing-result",
            "article",
            "li[class*='listing']",
        ],
        "title": ["[data-cy='listing-title']", "h2", "h3"],
        "url": [
            "a[data-cy='listing-link']",
            "a[href*='/property-to-rent/']",
            "a[href*='/properties-to-rent/property/']",
            "a[href]",
        ],
        "price": [
            "[data-cy='listing-price']",
            ".listing-price",
            "[class*='price']",
        ],
        "address": [
            "[data-cy='listing-location']",
            ".listing-location",
            "[class*='location']",
        ],
        "description": ["[data-cy='listing-description']", ".listing-description", "p"],
        "bedrooms": ["[data-cy='listing-bedrooms']", "[class*='bed']"],
    },
    "rightmove - bristol 1 bedroom flats": {
        "card": [
            "div.propertyCard",
            "div[data-test='propertyCard']",
            "article",
        ],
        "title": ["h2.propertyCard-title", "[data-test='property-title']", "h2", "h3"],
        "url": [
            "a.propertyCard-link",
            "a[data-test='property-details-link']",
            "a[href*='/properties/']",
            "a[href]",
        ],
        "price": [
            ".propertyCard-priceValue",
            "[data-test='property-price']",
            "[class*='price']",
        ],
        "address": [
            ".propertyCard-address",
            "[data-test='property-address']",
            "[class*='address']",
            "[class*='location']",
        ],
        "description": [".propertyCard-description", "[data-test='property-description']", "p"],
        "bedrooms": ["[data-test='property-bedrooms']", "[class*='bedroom']", "[class*='bed']"],
    },
    "zoopla - bristol 1 bedroom flats": {
        "card": [
            "div[data-testid='regular-listing']",
            "[data-testid*='listing']",
            "article",
        ],
        "title": ["[data-testid='listing-title']", "h2", "h3"],
        "url": [
            "a[data-testid='listing-details-link']",
            "a[href*='/to-rent/details/']",
            "a[href*='/to-rent/']",
            "a[href]",
        ],
        "price": [
            "[data-testid='listing-price']",
            "[data-testid*='price']",
            "[class*='price']",
        ],
        "address": [
            "[data-testid='listing-address']",
            "[data-testid='listing-description']",
            "[data-testid*='address']",
            "[class*='address']",
            "[class*='location']",
        ],
        "description": ["[data-testid='listing-description']", "p"],
        "bedrooms": ["[data-testid*='bed']", "[class*='bedroom']", "[class*='bed']"],
    },
}


@dataclass
class StaticCssScraper(BaseScraper):
    """Generic scraper for simple public listing pages with source-aware profiles."""

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
    render_mode: str = "http"  # http | browser
    wait_for_selector: str | None = None
    scroll_steps: int = 0
    fallback_render_mode: str | None = None
    min_quality_price: float = 0.8
    min_quality_location: float = 0.8

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

    def _profile(self) -> dict[str, list[str]]:
        return SOURCE_EXTRACTOR_PROFILES.get(self.source_name.strip().lower(), {})

    @staticmethod
    def _parse_selector_values(raw: str | list[str] | None) -> list[str]:
        if raw is None:
            return []
        if isinstance(raw, str):
            return [part.strip() for part in raw.split("||") if part.strip()]
        if isinstance(raw, list):
            out: list[str] = []
            for part in raw:
                if part is None:
                    continue
                text = str(part).strip()
                if text:
                    out.append(text)
            return out
        text = str(raw).strip()
        return [text] if text else []

    def _configured_selector_candidates(self, selector_name: str) -> list[str]:
        return self._parse_selector_values(self.selectors.get(selector_name))

    def _profile_selector_candidates(self, selector_name: str) -> list[str]:
        return self._parse_selector_values(self._profile().get(selector_name))

    def _selector_candidates(self, selector_name: str, include_fallback: bool) -> list[str]:
        candidates: list[str] = []
        for selector in self._configured_selector_candidates(selector_name):
            if selector not in candidates:
                candidates.append(selector)
        for selector in self._profile_selector_candidates(selector_name):
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

    def _card_selector_candidates(self) -> list[str]:
        candidates: list[str] = []
        for selector in self._configured_selector_candidates("card"):
            if selector not in candidates:
                candidates.append(selector)
        for selector in self._profile_selector_candidates("card"):
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
        for selector in self._card_selector_candidates():
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

    @staticmethod
    def _walk_json_values(value: Any) -> list[dict[str, Any]]:
        stack = [value]
        out: list[dict[str, Any]] = []
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                out.append(current)
                stack.extend(current.values())
            elif isinstance(current, list):
                stack.extend(current)
        return out

    @staticmethod
    def _normalize_url(url: str | None) -> str | None:
        if not url:
            return None
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        if not path:
            return parsed.netloc.lower() or None
        return f"{parsed.netloc.lower()}{path}"

    @staticmethod
    def _jsonld_address_text(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return clean_text(value)
        if isinstance(value, dict):
            parts: list[str] = []
            for key in ["streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry"]:
                field_val = clean_text(value.get(key))
                if field_val:
                    parts.append(field_val)
            if parts:
                return clean_text(", ".join(parts))
        if isinstance(value, list):
            for item in value:
                text = StaticCssScraper._jsonld_address_text(item)
                if text:
                    return text
        return None

    @staticmethod
    def _jsonld_price_text(offers: Any) -> str | None:
        if offers is None:
            return None
        offer_values: list[dict[str, Any]] = []
        if isinstance(offers, dict):
            offer_values.append(offers)
        elif isinstance(offers, list):
            offer_values.extend(item for item in offers if isinstance(item, dict))

        for offer in offer_values:
            price = offer.get("price") or offer.get("lowPrice") or offer.get("highPrice")
            if price is None:
                continue
            raw_period = ""
            spec = offer.get("priceSpecification")
            if isinstance(spec, dict):
                raw_period = str(spec.get("unitText") or spec.get("billingDuration") or "")
            raw_period = raw_period.lower()
            if "week" in raw_period or "/wk" in raw_period or "pw" in raw_period:
                suffix = "pw"
            else:
                suffix = "pcm"
            return f"£{price} {suffix}"
        return None

    def _extract_jsonld_candidates(self, soup: BeautifulSoup) -> list[dict[str, str]]:
        candidates: list[dict[str, str]] = []
        for script in soup.select("script[type='application/ld+json']"):
            raw = script.string or script.get_text()
            if not raw:
                continue
            try:
                parsed = json.loads(raw)
            except Exception:
                continue
            for node in self._walk_json_values(parsed):
                url = clean_text(node.get("url"))
                title = clean_text(node.get("name") or node.get("headline"))
                address = self._jsonld_address_text(node.get("address"))
                if not address and isinstance(node.get("itemOffered"), dict):
                    address = self._jsonld_address_text(node["itemOffered"].get("address"))
                price_text = self._jsonld_price_text(node.get("offers"))
                if not any([url, title, address, price_text]):
                    continue
                candidates.append(
                    {
                        "url": url or "",
                        "title": title or "",
                        "address": address or "",
                        "price_text": price_text or "",
                    }
                )
        return candidates

    def _match_jsonld_candidate(
        self,
        listing_url: str | None,
        title: str | None,
        candidates: list[dict[str, str]],
        used_indexes: set[int],
    ) -> dict[str, str] | None:
        listing_norm = self._normalize_url(listing_url)
        if listing_norm:
            for idx, item in enumerate(candidates):
                if idx in used_indexes:
                    continue
                if self._normalize_url(item.get("url")) == listing_norm:
                    used_indexes.add(idx)
                    return item

        listing_title = (title or "").strip().lower()
        if listing_title:
            for idx, item in enumerate(candidates):
                if idx in used_indexes:
                    continue
                candidate_title = (item.get("title") or "").strip().lower()
                if candidate_title and (candidate_title in listing_title or listing_title in candidate_title):
                    used_indexes.add(idx)
                    return item
        return None

    def _known_area_hints(self) -> list[str]:
        return CONFIG.preferred_areas + CONFIG.caution_areas

    def _compute_quality_metrics(
        self,
        cards_seen: int,
        listings_before_dedup: int,
        listings: list[Listing],
        render_mode_used: str,
    ) -> dict[str, Any]:
        listings_emitted = len(listings)
        price_hits = sum(1 for item in listings if item.price_pcm is not None)
        location_hits = sum(1 for item in listings if clean_text(item.address_text) or clean_text(item.postcode))

        price_coverage = round(price_hits / listings_emitted, 3) if listings_emitted else 0.0
        location_coverage = round(location_hits / listings_emitted, 3) if listings_emitted else 0.0
        return {
            "cards_seen": cards_seen,
            "listings_emitted": listings_emitted,
            "price_coverage": price_coverage,
            "location_coverage": location_coverage,
            "duplicates_dropped": max(0, listings_before_dedup - listings_emitted),
            "render_mode_used": render_mode_used,
            "min_quality_price": round(float(self.min_quality_price), 3),
            "min_quality_location": round(float(self.min_quality_location), 3),
        }

    def _evaluate_quality(self, metrics: dict[str, Any]) -> tuple[str, str]:
        if metrics["listings_emitted"] == 0:
            return "degraded", "No listings emitted after parsing."
        if metrics["price_coverage"] < float(self.min_quality_price):
            return "degraded", (
                f"Price coverage {metrics['price_coverage']:.3f} is below threshold "
                f"{float(self.min_quality_price):.3f}."
            )
        if metrics["location_coverage"] < float(self.min_quality_location):
            return "degraded", (
                f"Location coverage {metrics['location_coverage']:.3f} is below threshold "
                f"{float(self.min_quality_location):.3f}."
            )
        return "healthy", "Quality gate passed."

    def _fetch_http(self) -> tuple[str, str]:
        headers = {"User-Agent": self.user_agent}
        response = requests.get(self.url, headers=headers, timeout=self.timeout)
        response.raise_for_status()
        return response.text, response.url

    def _fetch_browser(self) -> tuple[str, str]:
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            raise RuntimeError("Playwright is not installed. Install `playwright` and browser binaries.") from exc

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(user_agent=self.user_agent)
            page = context.new_page()
            page.goto(self.url, wait_until="domcontentloaded", timeout=max(5000, self.timeout * 1000))
            wait_selector = self.wait_for_selector or next(
                iter(self._configured_selector_candidates("card") or self._profile_selector_candidates("card")),
                None,
            )
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=6000)
                except Exception:
                    pass
            for _ in range(max(0, int(self.scroll_steps))):
                page.mouse.wheel(0, 2200)
                page.wait_for_timeout(350)
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            html = page.content()
            final_url = page.url
            context.close()
            browser.close()
            return html, final_url

    def _fetch_content(self, mode: str) -> tuple[str, str]:
        normalized = mode.strip().lower()
        if normalized == "browser":
            return self._fetch_browser()
        return self._fetch_http()

    def parse_html(
        self,
        html: str,
        *,
        render_mode_used: str,
        robots_note: str | None = None,
    ) -> ScrapeResult:
        soup = BeautifulSoup(html, "html.parser")
        cards, card_selector_used, attempted_card_selectors, used_card_fallback = self._select_cards(soup)
        selector_usage: dict[str, set[str]] = defaultdict(set)
        jsonld_candidates = self._extract_jsonld_candidates(soup)
        matched_jsonld_indexes: set[int] = set()

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

            jsonld_match = self._match_jsonld_candidate(listing_url, title, jsonld_candidates, matched_jsonld_indexes)
            jsonld_address = clean_text((jsonld_match or {}).get("address"))
            jsonld_price_text = clean_text((jsonld_match or {}).get("price_text"))

            price_text, price_selector = self._get_text(card, "price", include_fallback=self.fallback_to_similar_selectors)
            if price_selector:
                selector_usage["price"].add(price_selector)
            if not price_text:
                price_text = jsonld_price_text
            if not price_text:
                price_text = self._extract_near_keyword(card_text, FIELD_KEYWORDS["price"])

            address_text, address_selector = self._get_text(
                card,
                "address",
                include_fallback=self.fallback_to_similar_selectors,
            )
            if address_selector:
                selector_usage["address"].add(address_selector)

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

            combined = " ".join(filter(None, [title, price_text, address_text, jsonld_address, description, card_text]))
            resolved_address, postcode = resolve_location(
                selector_address=address_text,
                jsonld_address=jsonld_address,
                combined_text=combined,
                known_areas=self._known_area_hints(),
            )

            price_pcm = parse_price_pcm(price_text or combined)
            bedrooms = parse_bedrooms(bedrooms_text or combined)
            postcode = postcode or extract_postcode(combined)
            property_type = infer_property_type(title, description)

            raw = {
                "source_url": self.url,
                "price_text": price_text,
                "address_text": resolved_address,
                "jsonld_address": jsonld_address,
                "jsonld_price_text": jsonld_price_text,
                "selectors": self.selectors,
                "card_selector_used": card_selector_used,
                "selector_usage": {k: sorted(v) for k, v in selector_usage.items()},
                "render_mode_used": render_mode_used,
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
                address_text=resolved_address,
                postcode=postcode,
                available_from=available_from,
                description=description,
                raw_json=json.dumps(raw, ensure_ascii=False),
            )
            listings.append(enrich_and_score_listing(listing))

        listings_before_dedup = len(listings)
        listings = deduplicate_listings(listings)
        metrics = self._compute_quality_metrics(
            cards_seen=len(cards),
            listings_before_dedup=listings_before_dedup,
            listings=listings,
            render_mode_used=render_mode_used,
        )

        if not cards:
            quality_status = "degraded"
            quality_reason = "No cards found."
            message = (
                "Fetched 0 listings: no cards found. "
                f"Attempted selectors: {attempted_card_selectors[:8]}"
            )
            return ScrapeResult(
                source=self.source_name,
                listings=listings,
                metrics=metrics,
                quality_status=quality_status,
                quality_gate_reason=quality_reason,
                message=message,
            )

        quality_status, quality_reason = self._evaluate_quality(metrics)
        fallback_note = ""
        if used_card_fallback:
            fallback_note = f" Used fallback card selector '{card_selector_used}'."
        elif card_selector_used:
            fallback_note = f" Used configured card selector '{card_selector_used}'."
        robots_note_text = f" {robots_note}" if robots_note else ""

        quality_note = (
            f" Quality={quality_status} (price={metrics['price_coverage']:.3f}, "
            f"location={metrics['location_coverage']:.3f})."
        )
        message = (
            f"Fetched {len(listings)} listings from {len(cards)} cards via {render_mode_used}."
            f"{fallback_note}{quality_note}{robots_note_text}"
        )

        return ScrapeResult(
            source=self.source_name,
            listings=listings,
            message=message,
            metrics=metrics,
            quality_status=quality_status,
            quality_gate_reason=quality_reason,
        )

    def _mode_candidates(self) -> list[str]:
        modes = [self.render_mode.strip().lower() if self.render_mode else "http"]
        if self.fallback_render_mode:
            fallback = self.fallback_render_mode.strip().lower()
            if fallback and fallback not in modes:
                modes.append(fallback)
        return modes

    @staticmethod
    def _quality_rank(result: ScrapeResult) -> tuple[int, float]:
        metrics = result.metrics or {}
        gate = 1 if result.quality_status == "healthy" else 0
        coverage_score = float(metrics.get("price_coverage", 0.0)) + float(metrics.get("location_coverage", 0.0))
        return gate, coverage_score

    def scrape(self) -> ScrapeResult:
        allowed, robots_note = self._allowed_by_robots()
        if not allowed:
            return ScrapeResult(
                source=self.source_name,
                listings=[],
                quality_status="degraded",
                quality_gate_reason=robots_note or "Skipped due robots policy.",
                message=robots_note or "Skipped due robots policy.",
                metrics={
                    "cards_seen": 0,
                    "listings_emitted": 0,
                    "price_coverage": 0.0,
                    "location_coverage": 0.0,
                    "duplicates_dropped": 0,
                    "render_mode_used": "none",
                    "min_quality_price": round(float(self.min_quality_price), 3),
                    "min_quality_location": round(float(self.min_quality_location), 3),
                },
            )

        best_result: ScrapeResult | None = None
        mode_errors: list[str] = []

        for mode in self._mode_candidates():
            try:
                html, _final_url = self._fetch_content(mode)
                parsed = self.parse_html(
                    html,
                    render_mode_used=mode,
                    robots_note=robots_note,
                )
                if best_result is None or self._quality_rank(parsed) > self._quality_rank(best_result):
                    best_result = parsed
                if parsed.quality_status == "healthy":
                    break
            except Exception as exc:
                mode_errors.append(f"{mode}: {exc}")

        if best_result is None:
            error_text = "; ".join(mode_errors) if mode_errors else "Unknown fetch error."
            raise RuntimeError(f"Failed to scrape {self.source_name}: {error_text}")

        if best_result.quality_status == "degraded" and mode_errors:
            best_result.message = (
                f"{best_result.message} Render attempts with errors: {'; '.join(mode_errors)}."
            )
        elif mode_errors:
            best_result.message = (
                f"{best_result.message} Non-selected render mode errors: {'; '.join(mode_errors)}."
            )
        return best_result


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
                selectors=source.get("selectors", {}),
                user_agent=source.get("user_agent", "bristol-housing-dashboard/0.1 personal-use"),
                respect_robots_txt=source.get("respect_robots_txt", True),
                allow_if_robots_unavailable=source.get("allow_if_robots_unavailable", True),
                ignore_robots_restrictions=source.get("ignore_robots_restrictions", True),
                fallback_to_similar_selectors=source.get("fallback_to_similar_selectors", True),
                enable_whole_page_block_discovery=source.get("enable_whole_page_block_discovery", True),
                render_mode=source.get("render_mode", "http"),
                wait_for_selector=source.get("wait_for_selector"),
                scroll_steps=int(source.get("scroll_steps", 0) or 0),
                fallback_render_mode=source.get("fallback_render_mode"),
                min_quality_price=float(source.get("min_quality_price", 0.8)),
                min_quality_location=float(source.get("min_quality_location", 0.8)),
            )
        )
    return scrapers
