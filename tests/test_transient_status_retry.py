"""HTTP 403 (the Cloudflare challenge in front of HUDOC) is retried like 429 and 5xx, within the bounded policy."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from hudoc_py.main import downloader
from hudoc_py.main.client import AsyncHudocClient


class _Stream:
    def __init__(self, chunks):
        self.chunks = chunks

    async def iter_chunked(self, _size):
        for chunk in self.chunks:
            yield chunk


class _Response:
    def __init__(self, status: int, *, body: str = "", data: dict[str, Any] | None = None):
        self.status = status
        self._body = body
        self._data = data or {}
        self.headers = {"Content-Type": "text/html"}
        self.content_type = "application/json" if data is not None else "text/html"
        self.content = _Stream([body.encode()])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._data

    async def text(self):
        return self._body

    def get_encoding(self):
        return "utf-8"


class _Session:
    def __init__(self, responses: list[_Response]):
        self.responses = responses
        self.calls = 0

    def get(self, url, **_kwargs):
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return response


CHALLENGE = "<!DOCTYPE html><html><head><title>Just a moment...</title></head></html>"


def test_search_client_retries_cloudflare_403_then_succeeds():
    client = AsyncHudocClient(rate_limit_seconds=0.0, max_retries=3)
    client.session = _Session(
        [_Response(403, body=CHALLENGE), _Response(200, data={"resultcount": 1, "results": []})]
    )
    data = asyncio.run(client._request({"query": "x"}))
    assert data["resultcount"] == 1
    assert client.session.calls == 2


def test_search_client_gives_up_after_bounded_403_retries():
    client = AsyncHudocClient(rate_limit_seconds=0.0, max_retries=3)
    client.session = _Session([_Response(403, body=CHALLENGE)])
    with pytest.raises(RuntimeError, match="status=403"):
        asyncio.run(client._request({"query": "x"}))
    assert client.session.calls == 3


def test_search_client_user_agent_names_package_and_version():
    from hudoc_py import __version__
    from hudoc_py.main.client import user_agent

    assert user_agent() == f"echr-py/{__version__} (+https://github.com/niccoloridi/echr-py)"


def test_downloader_retries_403_then_downloads(monkeypatch):
    monkeypatch.setattr(downloader.asyncio, "sleep", _no_sleep)
    session = _Session(
        [_Response(403, body=CHALLENGE), _Response(200, body="<html>judgment</html>")]
    )
    response = asyncio.run(
        downloader._fetch_response(
            session, "https://hudoc", "001-1", label="HTML", binary=False, timeout=5, max_retries=3
        )
    )
    assert response.status == "downloaded"
    assert response.attempts == 2
    assert response.payload == "<html>judgment</html>"


def test_downloader_records_persistent_403_as_typed_error(monkeypatch):
    monkeypatch.setattr(downloader.asyncio, "sleep", _no_sleep)
    session = _Session([_Response(403, body=CHALLENGE)])
    response = asyncio.run(
        downloader._fetch_response(
            session, "https://hudoc", "001-1", label="HTML", binary=False, timeout=5, max_retries=3
        )
    )
    assert response.status == "error"
    assert response.http_status == 403
    assert response.attempts == 3
    assert response.error == "http_status_403"


async def _no_sleep(_seconds):
    return None
