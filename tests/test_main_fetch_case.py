"""Offline tests for selecting a main-HUDOC document."""

from __future__ import annotations

import asyncio

from hudoc_py import _aio


def test_fetch_case_forwards_requested_language(monkeypatch):
    calls: list[dict] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def search(self, **kwargs):
            calls.append(kwargs)
            return [
                {
                    "itemid": "001-2",
                    "appno": "1/00",
                    "languageisocode": "FRE",
                }
            ]

    monkeypatch.setattr(_aio, "AsyncHudocClient", FakeClient)

    case = asyncio.run(_aio.fetch_case(appno="1/00", language="fre"))

    assert case is not None
    assert case.language == "FRE"
    assert calls == [{"appno": "1/00", "languages": ("FRE",), "limit": 1}]


def test_fetch_case_defaults_to_both_court_languages(monkeypatch):
    calls: list[dict] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def search(self, **kwargs):
            calls.append(kwargs)
            return [{"itemid": "001-1", "appno": "1/00", "languageisocode": "ENG"}]

    monkeypatch.setattr(_aio, "AsyncHudocClient", FakeClient)

    case = asyncio.run(_aio.fetch_case(appno="1/00"))

    assert case is not None
    assert calls == [{"appno": "1/00", "languages": ("ENG", "FRE"), "limit": 1}]


def test_search_accepts_hudoc_url_and_additional_filters(monkeypatch):
    calls: list[dict] = []

    class FakeClient:
        last_result_count = 1

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def search(self, **kwargs):
            calls.append(kwargs)
            return [{"itemid": "001-1", "languageisocode": "ENG"}]

    monkeypatch.setattr(_aio, "AsyncHudocClient", FakeClient)

    cases = asyncio.run(
        _aio.search(
            hudoc_url="https://hudoc.echr.coe.int/eng?i=001-1",
            respondent="ITA",
        )
    )

    assert len(cases) == 1
    query = calls[0]["query"]
    assert 'itemid:"001-1"' in query
    assert 'respondent:"ITA"' in query
    assert "doctype=" not in query
    assert "languageisocode=" not in query
