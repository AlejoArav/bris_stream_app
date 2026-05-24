from __future__ import annotations

import math

from housing_dashboard.text_utils import extract_price_candidates, parse_price_pcm


def test_parse_price_pcm_monthly() -> None:
    assert parse_price_pcm("Rent £1250 pcm") == 1250.0


def test_parse_price_pcm_weekly() -> None:
    assert math.isclose(parse_price_pcm("From £295 pw") or 0.0, 1278.33, rel_tol=0, abs_tol=0.01)


def test_parse_price_pcm_pppw() -> None:
    assert math.isclose(parse_price_pcm("£250 pppw") or 0.0, 1083.33, rel_tol=0, abs_tol=0.01)


def test_parse_price_pcm_ignores_deposit_context() -> None:
    text = "Holding deposit £300. Monthly rent £1,250 pcm."
    assert parse_price_pcm(text) == 1250.0
    candidates = extract_price_candidates(text)
    assert candidates
    assert candidates[0].monthly_amount == 1250.0


def test_parse_price_pcm_missing_returns_none() -> None:
    assert parse_price_pcm("Please contact agent for price") is None
