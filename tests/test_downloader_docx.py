"""Tests for raw DOCX download (main + execution downloaders)."""

from __future__ import annotations

import asyncio

from hudoc_py.execution import downloader as exec_dl
from hudoc_py.main import downloader as main_dl


class _FakeResp:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def read(self) -> bytes:
        return self._body


class _FakeSession:
    """Session returning a scripted response and recording the URL requested."""

    def __init__(self, status: int, body: bytes):
        self._status = status
        self._body = body
        self.urls: list[str] = []

    def get(self, url, **kw):
        self.urls.append(url)
        return _FakeResp(self._status, self._body)


def test_main_docx_url_and_bytes():
    session = _FakeSession(200, b"PK\x03\x04docx-bytes")
    data = asyncio.run(main_dl.fetch_document_docx(session, "001-94054"))
    assert data == b"PK\x03\x04docx-bytes"
    assert "library=ECHR&id=001-94054" in session.urls[0]
    assert "/app/conversion/docx?" in session.urls[0]
    # The endpoint 404s without a filename param (verified live).
    assert "filename=" in session.urls[0]


def test_exec_docx_url_uses_exec_library():
    session = _FakeSession(200, b"PK\x03\x04")
    data = asyncio.run(exec_dl.fetch_exec_document_docx(session, "store-uuid-123"))
    assert data == b"PK\x03\x04"
    assert "library=EXEC&id=store-uuid-123" in session.urls[0]
    assert "filename=" in session.urls[0]


def test_empty_body_returns_none():
    session = _FakeSession(200, b"")
    assert asyncio.run(main_dl.fetch_document_docx(session, "x")) is None


def test_204_returns_none():
    session = _FakeSession(204, b"")
    assert asyncio.run(main_dl.fetch_document_docx(session, "x")) is None


def test_downloader_saves_docx_file(tmp_path, monkeypatch):
    async def fake_docx(session, itemid, *, max_retries=3):
        return main_dl.DownloadResponse(
            status="downloaded",
            url=f"https://example.test/{itemid}.docx",
            payload=b"PK\x03\x04file-for-" + itemid.encode(),
            http_status=200,
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            attempts=1,
        )

    monkeypatch.setattr(main_dl, "fetch_document_docx_response", fake_docx)
    dl = main_dl.AsyncDocumentDownloader(
        tmp_path, save_html=False, save_md=False, save_txt=False, save_docx=True
    )
    ok = asyncio.run(dl.fetch_and_save(session=None, itemid="001-1"))
    assert ok is True
    out = tmp_path / "docx" / "001-1.docx"
    assert out.exists()
    assert out.read_bytes() == b"PK\x03\x04file-for-001-1"
    assert dl.last_outcomes["001-1"]["docx"].http_status == 200


def test_docx_dir_not_created_when_disabled(tmp_path):
    main_dl.AsyncDocumentDownloader(tmp_path, save_docx=False)
    assert not (tmp_path / "docx").exists()


def test_fetch_docx_facade_writes_file(tmp_path, monkeypatch):
    from hudoc_py import _aio

    async def fake_docx(session, itemid, *, max_retries=3):
        return b"docx-payload"

    monkeypatch.setattr(_aio, "fetch_document_docx", fake_docx)
    out = tmp_path / "case.docx"
    data = asyncio.run(_aio.fetch_docx("001-1", out=out))
    assert data == b"docx-payload"
    assert out.read_bytes() == b"docx-payload"


def test_fetch_docx_facade_none_when_missing(tmp_path, monkeypatch):
    from hudoc_py import _aio

    async def fake_docx(session, itemid, *, max_retries=3):
        return None

    monkeypatch.setattr(_aio, "fetch_document_docx", fake_docx)
    out = tmp_path / "nope.docx"
    assert asyncio.run(_aio.fetch_docx("x", out=out)) is None
    assert not out.exists()
