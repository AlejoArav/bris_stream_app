from __future__ import annotations

from housing_dashboard.text_utils import resolve_location


def test_location_prefers_selector_address() -> None:
    address, postcode = resolve_location(
        selector_address="24 Clifton Down Road, Bristol BS8 1EJ",
        jsonld_address="Different address BS1 1AA",
        combined_text="",
        known_areas=["Clifton", "Cotham"],
    )
    assert address == "24 Clifton Down Road, Bristol BS8 1EJ"
    assert postcode == "BS8 1EJ"


def test_location_uses_jsonld_address() -> None:
    address, postcode = resolve_location(
        selector_address=None,
        jsonld_address="Flat 1, 10 Redland Road, Bristol BS6 6XP",
        combined_text="",
        known_areas=["Redland"],
    )
    assert address == "Flat 1, 10 Redland Road, Bristol BS6 6XP"
    assert postcode == "BS6 6XP"


def test_location_uses_postcode_when_address_missing() -> None:
    address, postcode = resolve_location(
        selector_address=None,
        jsonld_address=None,
        combined_text="Great location close to campus. Postcode BS8 2QT.",
        known_areas=["Clifton"],
    )
    assert address == "BS8 2QT"
    assert postcode == "BS8 2QT"


def test_location_uses_area_hint_fallback() -> None:
    address, postcode = resolve_location(
        selector_address=None,
        jsonld_address=None,
        combined_text="Modern studio in Redland near local cafes.",
        known_areas=["Clifton", "Redland"],
    )
    assert address == "Redland"
    assert postcode is None


def test_location_returns_none_when_unusable() -> None:
    address, postcode = resolve_location(
        selector_address=None,
        jsonld_address=None,
        combined_text="Brand new listing coming soon.",
        known_areas=["Clifton", "Redland"],
    )
    assert address is None
    assert postcode is None
