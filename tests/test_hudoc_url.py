"""Tests for deterministic ingestion of shareable HUDOC URLs."""

from __future__ import annotations

import pytest

from hudoc_py.main.url import query_from_hudoc_url


def test_itemid_short_link():
    query = query_from_hudoc_url("https://hudoc.echr.coe.int/eng?i=001-57574")
    assert query == 'contentsitename:ECHR AND itemid:"001-57574"'


def test_encoded_json_fragment_preserves_filters():
    url = (
        "https://hudoc.echr.coe.int/eng#"
        "%7B%22article%22:%5B%223%22%5D,%22languageisocode%22:"
        "%5B%22ENG%22,%22FRE%22%5D,%22itemid%22:%5B%22001-57574%22%5D%7D"
    )
    query = query_from_hudoc_url(url)
    assert 'article:"3"' in query
    assert 'languageisocode="ENG"' in query
    assert 'languageisocode="FRE"' in query
    assert 'itemid:"001-57574"' in query


def test_date_and_fulltext_fragment():
    url = (
        "https://hudoc.echr.coe.int/eng#"
        '{"fulltext":["\\"positive obligations\\""],'
        '"kpdate":["2020-01-01","2021-12-31"]}'
    )
    query = query_from_hudoc_url(url)
    assert '("positive obligations")' in query
    assert 'kpdate>="2020-01-01"' in query
    assert 'kpdate<="2021-12-31"' in query


def test_direct_api_query_is_retained():
    url = "https://hudoc.echr.coe.int/app/query/results?query=article%3A%223%22"
    assert query_from_hudoc_url(url) == 'article:"3"'


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/eng?i=001-57574",
        "https://hudoc.echr.coe.int/eng",
        "https://hudoc.echr.coe.int/eng#not-json",
    ],
)
def test_invalid_urls_fail_closed(url):
    with pytest.raises(ValueError):
        query_from_hudoc_url(url)
