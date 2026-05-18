from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterable

from ..models import Listing


@dataclass
class ScrapeResult:
    source: str
    listings: list[Listing]
    message: str = ""


class BaseScraper(ABC):
    source_name: str

    @abstractmethod
    def scrape(self) -> ScrapeResult:
        raise NotImplementedError


def deduplicate_listings(listings: Iterable[Listing]) -> list[Listing]:
    seen = set()
    output: list[Listing] = []
    for listing in listings:
        key = (listing.source, listing.external_id or listing.url)
        if key in seen:
            continue
        seen.add(key)
        output.append(listing)
    return output
