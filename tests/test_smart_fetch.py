"""Tests for the smart_fetch workflow (search → top-N → concurrent text fetch)."""

from __future__ import annotations

import asyncio

import pytest

from hudoc_py import _aio
from hudoc_py.main.client import AsyncHudocClient

FIXTURE_ROWS = [
    {"itemid": "001-1", "docname": "CASE A", "doctype": "HEJUD"},
    {"itemid": "001-2", "docname": "CASE B", "doctype": "HEJUD"},
]

FIXTURE_HTML = """
<html><body>
<p>PROCEDURE</p><p>Some procedure.</p>
<p>THE LAW</p><p>Article 3 analysis here.</p>
<p>FOR THESE REASONS, THE COURT</p><p>Holds there has been a violation.</p>
</body></html>
"""


@pytest.fixture
def fake_network(monkeypatch):
    fetched: list[str] = []

    async def fake_search(self, **kwargs):
        self.last_result_count = 42
        return FIXTURE_ROWS[: kwargs.get("limit") or len(FIXTURE_ROWS)]

    async def fake_fetch_html(session, itemid, **kwargs):
        fetched.append(itemid)
        return FIXTURE_HTML

    monkeypatch.setattr(AsyncHudocClient, "search", fake_search)
    monkeypatch.setattr(_aio, "fetch_document_html", fake_fetch_html)
    return fetched


def test_smart_fetch_populates_texts_and_sections(fake_network):
    cases = asyncio.run(_aio.smart_fetch(article="3", top=2))
    assert len(cases) == 2
    assert all(c.text for c in cases)
    assert all(c.sections is not None for c in cases)
    assert "violation" in (cases[0].sections.dispositif or "").lower()
    assert sorted(fake_network) == ["001-1", "001-2"]


def test_smart_fetch_result_count_stashed(fake_network):
    cases = asyncio.run(_aio.smart_fetch(article="3", top=2))
    assert cases.result_count == 42


def test_smart_fetch_without_text(fake_network):
    cases = asyncio.run(_aio.smart_fetch(article="3", top=2, with_text=False))
    assert len(cases) == 2
    assert all(c.text is None for c in cases)
    assert fake_network == []


def test_smart_fetch_top_limits_results(fake_network):
    cases = asyncio.run(_aio.smart_fetch(article="3", top=1))
    assert len(cases) == 1
    assert fake_network == ["001-1"]
