"""Regression test for the %22 (double-quote) URL-encoding pitfall.

Feeding aiohttp a *pre-encoded URL string* makes yarl re-encode ``%22`` into
``%2522``, breaking the appno phrase match. echr-py's client passes a params
*dict*, which aiohttp/yarl encode exactly once. This test pins that behaviour
so a future refactor to pre-built URL strings is caught.
"""

from __future__ import annotations

from yarl import URL

from hudoc_py import config
from hudoc_py.main.queries import build_search_query


def test_appno_query_single_encodes_quotes():
    query = build_search_query(appno="46221/99")
    assert '"46221/99"' in query  # literal quotes in the query string

    # Reproduce what aiohttp does: URL(base).with_query(params).
    params = {
        "query": query,
        "select": "itemid",
        "length": 1,
        "start": 0,
    }
    url = str(URL(config.HUDOC_SEARCH_URL).with_query(params))

    # Quotes are percent-encoded exactly once...
    assert "%22" in url
    # ...never doubly (the yarl bug that forced the source onto `requests`).
    assert "%2522" not in url


def test_encoded_url_escape_hatch_documented():
    # If a raw pre-encoded URL string is ever needed, this is the correct form
    # (encoded=True prevents re-encoding). Pin it so the idiom is discoverable.
    raw = "https://hudoc.echr.coe.int/app/query/results?query=appno%3D%2246221%2F99%22"
    url = URL(raw, encoded=True)
    assert "%2522" not in str(url)
    assert "%22" in str(url)
