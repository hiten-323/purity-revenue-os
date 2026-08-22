"""
A searched phone number must never overwrite a published one.

Within an hour of ingesting 25 Delhi NCR distributors from Nestle Professional's
public locator, the enrichment loop replaced two of their phone numbers with
numbers it found by web search — Suntime Traders 9312064004 -> +91-98710-12349,
Moonlight Distributors 8800763764 -> +91-86146-14030 — and rewrote phone_source
to "Perplexity, BraveSearch, ...".

The email path already had a guard for this exact shape of bug (an address
purged at 05:38 was re-enriched minutes later). The phone path had none.

These tests pin the asymmetry that was missing: enrichment may FILL an empty
phone and may CONFIRM one that agrees, but it may not REPLACE a first-party
number with a search result.
"""
from __future__ import annotations

import pytest

from app.services.contact_enricher import (AUTHORITATIVE_PHONE_SOURCES,
                                           digits_only, phone_is_authoritative)


class Lead:
    def __init__(self, phone="", phone_source=""):
        self.phone = phone
        self.phone_source = phone_source


def test_digits_only_ignores_formatting():
    """+91-93120-64004 and 9312064004 are the same number; a guard that cannot
    see that would 'protect' a row by rewriting it with itself reformatted."""
    assert digits_only("+91-93120-64004") == digits_only("9312064004") == "9312064004"
    assert digits_only("") == ""
    assert digits_only(None) == ""


def test_publisher_sourced_phone_is_protected():
    for src in ("NESTLE_DISTRIBUTOR_LOCATOR", "WEBSITE", "FOUNDER_CALL"):
        assert phone_is_authoritative(Lead("9312064004", src)) is True, src


def test_searched_phone_is_not_protected():
    """The whole point: a number that came from a search has no standing to
    block the next search from correcting it."""
    for src in ("Perplexity, BraveSearch, TradeIndia", "GooglePlaces", "", "IndiaMART"):
        assert phone_is_authoritative(Lead("9876543210", src)) is False, src


def test_empty_phone_is_never_protected():
    """Enrichment filling a blank is the useful half of the loop. If an empty
    field counted as authoritative, the guard would freeze the 1,430 leads that
    most need a number."""
    assert phone_is_authoritative(Lead("", "WEBSITE")) is False
    assert phone_is_authoritative(Lead("   ", "NESTLE_DISTRIBUTOR_LOCATOR")) is False


def test_source_set_is_first_party_only():
    """Every member must be something a human or the brand itself stated. If a
    scraper name is ever added here, the guard starts protecting guesses."""
    for src in AUTHORITATIVE_PHONE_SOURCES:
        assert src.isupper(), f"{src} — comparison upper-cases, so entries must too"
    for banned in ("PERPLEXITY", "BRAVESEARCH", "GOOGLEPLACES", "INDIAMART",
                   "TRADEINDIA", "JUSTDIAL", "SEARCH"):
        assert banned not in AUTHORITATIVE_PHONE_SOURCES, (
            f"{banned} is a search source and must not outrank a publisher")
