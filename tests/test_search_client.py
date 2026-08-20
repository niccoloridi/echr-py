"""Tests for AsyncHudocClient: sort threading, count(), Q coercion, pagination."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from hudoc_py.main import client as client_module
from hudoc_py.main.client import AsyncHudocClient, HudocResultWindowError
from hudoc_py.main.dsl import Q


class FakeRequestClient(AsyncHudocClient):
    """Client whose _request is replaced by a canned-response recorder."""

    def __init__(self, envelopes: list[dict[str, Any]]):
        super().__init__(rate_limit_seconds=0.0)
        self.envelopes = envelopes
        self.calls: list[dict[str, Any]] = []

    async def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(dict(params))
        data = self.envelopes[min(len(self.calls) - 1, len(self.envelopes) - 1)]
        if "resultcount" in data:
            self.last_result_count = int(data["resultcount"])
        return data


def _envelope(rows: list[dict[str, Any]], count: int | None = None) -> dict[str, Any]:
    env: dict[str, Any] = {"results": [{"columns": r} for r in rows]}
    if count is not None:
        env["resultcount"] = count
    return env


def test_count_uses_length_zero_and_resultcount():
    client = FakeRequestClient([_envelope([], count=42)])
    n = asyncio.run(client.count(article="3"))
    assert n == 42
    assert client.calls[0]["length"] == 0
    assert client.calls[0]["select"] == "itemid"


def test_count_falls_back_to_length_one():
    client = FakeRequestClient([
        {"results": []},  # no resultcount at length=0
        _envelope([{"itemid": "001-1"}], count=7),
    ])
    n = asyncio.run(client.count(article="3"))
    assert n == 7
    assert client.calls[0]["length"] == 0
    assert client.calls[1]["length"] == 1


def test_sort_lands_in_params():
    client = FakeRequestClient([_envelope([{"itemid": "001-1"}], count=1), _envelope([])])
    asyncio.run(client.search(article="3", sort="kpdate Descending", limit=1))
    assert client.calls[0]["sort"] == "kpdate Descending"


def test_default_sort_is_empty():
    client = FakeRequestClient([_envelope([])])
    asyncio.run(client.search(article="3", limit=1))
    assert client.calls[0]["sort"] == ""


def test_q_instance_compiled_to_lucene():
    client = FakeRequestClient([_envelope([])])
    asyncio.run(client.search(query=Q.article("3") & Q.respondent("ITA"), limit=1))
    assert client.calls[0]["query"] == 'article:"3" AND respondent:"ITA"'


def test_pagination_stops_on_empty_page():
    rows = [{"itemid": f"001-{i}"} for i in range(3)]
    client = FakeRequestClient([_envelope(rows, count=3), _envelope([])])
    out = asyncio.run(client.search(article="3", page_size=3))
    assert [r["itemid"] for r in out] == ["001-0", "001-1", "001-2"]
    # The result-count envelope proves the first page is also the last.
    assert len(client.calls) == 1


def test_pagination_continues_when_envelope_omits_resultcount():
    client = FakeRequestClient(
        [
            _envelope([{"itemid": "001-1"}]),
            _envelope([{"itemid": "001-2"}]),
            _envelope([]),
        ]
    )
    out = asyncio.run(client.search(article="3", page_size=1))
    assert [row["itemid"] for row in out] == ["001-1", "001-2"]
    assert len(client.calls) == 3
    assert client.last_result_count == 2


def test_limit_respected():
    rows = [{"itemid": f"001-{i}"} for i in range(5)]
    client = FakeRequestClient([_envelope(rows, count=100)])
    out = asyncio.run(client.search(article="3", page_size=5, limit=2))
    assert len(out) == 2


def test_last_result_count_stashed():
    client = FakeRequestClient([_envelope([{"itemid": "001-1"}], count=1234), _envelope([])])
    asyncio.run(client.search(article="3", limit=1))
    assert client.last_result_count == 1234


def test_oversized_query_is_partitioned_without_duplicate_first_page(monkeypatch):
    monkeypatch.setattr(client_module, "HUDOC_MAX_RESULTS_PER_QUERY", 3)
    client = FakeRequestClient(
        [
            _envelope([{"itemid": "discarded-first-page"}], count=4),
            _envelope([], count=4),
            _envelope([], count=2),
            _envelope([], count=2),
            _envelope([{"itemid": "a"}, {"itemid": "b"}], count=2),
            _envelope([{"itemid": "c"}, {"itemid": "d"}], count=2),
        ]
    )
    rows = asyncio.run(
        client.search(
            article="3",
            date_from="2020-01-01",
            date_to="2020-01-02",
            page_size=2,
        )
    )
    assert [row["itemid"] for row in rows] == ["a", "b", "c", "d"]
    assert client.last_result_count == 4
    assert 'kpdate>="2020-01-01T00:00:00.0Z"' in client.calls[4]["query"]
    assert 'kpdate>="2020-01-02T00:00:00.0Z"' in client.calls[5]["query"]


def test_partition_count_mismatch_fails_closed(monkeypatch):
    monkeypatch.setattr(client_module, "HUDOC_MAX_RESULTS_PER_QUERY", 3)
    client = FakeRequestClient(
        [
            _envelope([{"itemid": "discarded"}], count=4),
            _envelope([], count=4),
            _envelope([], count=2),
            _envelope([], count=1),
        ]
    )
    with pytest.raises(HudocResultWindowError, match="did not preserve"):
        asyncio.run(
            client.search(
                article="3",
                date_from="2020-01-01",
                date_to="2020-01-02",
                page_size=2,
            )
        )


def test_single_day_above_cap_fails_closed(monkeypatch):
    monkeypatch.setattr(client_module, "HUDOC_MAX_RESULTS_PER_QUERY", 3)
    client = FakeRequestClient(
        [
            _envelope([{"itemid": "discarded"}], count=4),
            _envelope([], count=4),
        ]
    )
    with pytest.raises(HudocResultWindowError, match="share 2020-01-01"):
        asyncio.run(
            client.search(
                article="3",
                date_from="2020-01-01",
                date_to="2020-01-01",
                page_size=2,
            )
        )
