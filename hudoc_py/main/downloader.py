"""Async downloader for HUDOC document HTML (and optional MD/TXT conversions).

HTML→MD and HTML→text conversion functions are imported from the
:mod:`hudoc_py.text` package; they are stubbed in Phase B and fleshed out
in Phase D.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import aiohttp

from .. import config
from ..utils.downloads import ResponseTooLargeError, read_limited, read_text_limited

logger = logging.getLogger(__name__)

DOWNLOAD_HEADERS = {
    "Accept": "text/html, */*; q=0.01",
    "Accept-Language": "en-GB,en;q=0.9",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Cookie": "AS=SC%2Cfalse%3B; HUDOC=AV%2C72%3B",
    "User-Agent": "Mozilla/5.0 echr-py/0.1",
}


def _doc_url(itemid: str) -> str:
    return f"{config.HUDOC_HTML_URL}?library=ECHR&id={itemid}"


def _docx_url(itemid: str) -> str:
    # The ECHR conversion endpoint 404s without a `filename` query param
    # (verified live); the value itself is only used for the download name.
    return f"{config.HUDOC_DOCX_URL}?library=ECHR&id={itemid}&filename={itemid}.docx"


@dataclass(frozen=True)
class DownloadResponse:
    """Network/derivation outcome retained for acquisition manifests."""

    status: Literal["downloaded", "derived", "missing", "error"]
    url: str | None
    payload: str | bytes | None = None
    http_status: int | None = None
    content_type: str | None = None
    attempts: int = 0
    retrieved_at: datetime | None = None
    error: str | None = None


async def _fetch_response(
    session: aiohttp.ClientSession,
    url: str,
    itemid: str,
    *,
    label: str,
    binary: bool,
    timeout: int,
    max_retries: int,
) -> DownloadResponse:
    for attempt in range(1, max_retries + 1):
        retrieved_at = datetime.now(UTC)
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                content_type = getattr(resp, "headers", {}).get("Content-Type")
                if resp.status == 200:
                    payload: str | bytes = (
                        await read_limited(resp, max_bytes=config.HUDOC_MAX_BINARY_BYTES)
                        if binary
                        else await read_text_limited(resp, max_bytes=config.HUDOC_MAX_HTML_BYTES)
                    )
                    if not payload or (isinstance(payload, str) and not payload.strip()):
                        return DownloadResponse(
                            status="missing",
                            url=url,
                            http_status=resp.status,
                            content_type=content_type,
                            attempts=attempt,
                            retrieved_at=retrieved_at,
                            error="empty_response_body",
                        )
                    return DownloadResponse(
                        status="downloaded",
                        url=url,
                        payload=payload,
                        http_status=resp.status,
                        content_type=content_type,
                        attempts=attempt,
                        retrieved_at=retrieved_at,
                    )
                if resp.status == 204:
                    return DownloadResponse(
                        status="missing",
                        url=url,
                        http_status=resp.status,
                        content_type=content_type,
                        attempts=attempt,
                        retrieved_at=retrieved_at,
                        error="no_content",
                    )
                if resp.status in (429, 500, 502, 503, 504) and attempt < max_retries:
                    await asyncio.sleep(1.0 * attempt)
                    continue
                logger.error("Failed %s %s: status %d", label, itemid, resp.status)
                return DownloadResponse(
                    status="error",
                    url=url,
                    http_status=resp.status,
                    content_type=content_type,
                    attempts=attempt,
                    retrieved_at=retrieved_at,
                    error=f"http_status_{resp.status}",
                )
        except ResponseTooLargeError as exc:
            logger.error("Oversize %s %s: %s", label, itemid, exc)
            return DownloadResponse(
                status="error",
                url=url,
                attempts=attempt,
                retrieved_at=retrieved_at,
                error=f"ResponseTooLarge: {exc}",
            )
        except aiohttp.ClientError as exc:
            if attempt < max_retries:
                await asyncio.sleep(1.0 * attempt)
                continue
            logger.error("Exception fetching %s %s: %s", label, itemid, exc)
            return DownloadResponse(
                status="error",
                url=url,
                attempts=attempt,
                retrieved_at=retrieved_at,
                error=f"{type(exc).__name__}: {exc}",
            )
    raise AssertionError("retry loop exited unexpectedly")


async def fetch_document_html_response(
    session: aiohttp.ClientSession,
    itemid: str,
    *,
    max_retries: int = config.HUDOC_MAX_RETRIES,
) -> DownloadResponse:
    return await _fetch_response(
        session,
        _doc_url(itemid),
        itemid,
        label="HTML",
        binary=False,
        timeout=30,
        max_retries=max_retries,
    )


async def fetch_document_docx_response(
    session: aiohttp.ClientSession,
    itemid: str,
    *,
    max_retries: int = config.HUDOC_MAX_RETRIES,
) -> DownloadResponse:
    return await _fetch_response(
        session,
        _docx_url(itemid),
        itemid,
        label="DOCX",
        binary=True,
        timeout=60,
        max_retries=max_retries,
    )


async def fetch_document_html(
    session: aiohttp.ClientSession,
    itemid: str,
    *,
    max_retries: int = config.HUDOC_MAX_RETRIES,
) -> str | None:
    """Fetch raw HUDOC document HTML for an itemid.

    Returns the HTML string on success, ``None`` on empty body (204) or
    after exhausting retries.
    """
    response = await fetch_document_html_response(session, itemid, max_retries=max_retries)
    return response.payload if isinstance(response.payload, str) else None


async def fetch_document_docx(
    session: aiohttp.ClientSession,
    itemid: str,
    *,
    max_retries: int = config.HUDOC_MAX_RETRIES,
) -> bytes | None:
    """Fetch the raw DOCX bytes for a HUDOC document.

    Mirrors :func:`fetch_document_html`'s retry ladder. Returns the bytes on
    success, ``None`` on empty body / 204 / exhausted retries.
    """
    response = await fetch_document_docx_response(session, itemid, max_retries=max_retries)
    return response.payload if isinstance(response.payload, bytes) else None


class AsyncDocumentDownloader:
    """Download HUDOC documents (HTML body) by itemid, optionally rendering MD/TXT.

    Each itemid produces up to four files under ``output_root``::

        output_root/html/{itemid}.html
        output_root/md/{itemid}.md
        output_root/txt/{itemid}.txt
        output_root/docx/{itemid}.docx   (when save_docx=True)
    """

    def __init__(
        self,
        output_root: str | Path,
        *,
        save_html: bool = True,
        save_md: bool = True,
        save_txt: bool = True,
        save_docx: bool = False,
        max_retries: int = config.HUDOC_MAX_RETRIES,
    ):
        self.output_root = Path(output_root)
        self.save_html = save_html
        self.save_md = save_md
        self.save_txt = save_txt
        self.save_docx = save_docx
        self.max_retries = max_retries
        self.last_outcomes: dict[str, dict[str, DownloadResponse]] = {}

        self.html_dir = self.output_root / "html"
        self.md_dir = self.output_root / "md"
        self.txt_dir = self.output_root / "txt"
        for d in (self.html_dir, self.md_dir, self.txt_dir):
            d.mkdir(parents=True, exist_ok=True)
        # Only create the docx dir when asked, to avoid churning existing layouts.
        self.docx_dir = self.output_root / "docx"
        if self.save_docx:
            self.docx_dir.mkdir(parents=True, exist_ok=True)

    async def fetch_html(self, session: aiohttp.ClientSession, itemid: str) -> str | None:
        """Backwards-compat shim – see :func:`fetch_document_html`."""
        return await fetch_document_html(session, itemid, max_retries=self.max_retries)

    async def fetch_and_save(self, session: aiohttp.ClientSession, itemid: str) -> bool:
        # DOCX is a separate request from the HTML body; fetch it independently
        # so a case with only a DOCX (or only HTML) still saves what exists.
        saved_any = False
        outcomes: dict[str, DownloadResponse] = {}

        if self.save_docx:
            response = await fetch_document_docx_response(
                session, itemid, max_retries=self.max_retries
            )
            outcomes["docx"] = replace(response, payload=None)
            if isinstance(response.payload, bytes):
                (self.docx_dir / f"{itemid}.docx").write_bytes(response.payload)
                saved_any = True

        if not (self.save_html or self.save_md or self.save_txt):
            self.last_outcomes[itemid] = outcomes
            return saved_any

        response = await fetch_document_html_response(
            session, itemid, max_retries=self.max_retries
        )
        html = response.payload if isinstance(response.payload, str) else None
        if html is None:
            for fmt in ("html", "md", "txt"):
                if getattr(self, f"save_{fmt}"):
                    outcomes[fmt] = replace(response, payload=None)
            self.last_outcomes[itemid] = outcomes
            return saved_any

        if self.save_html:
            (self.html_dir / f"{itemid}.html").write_text(html, encoding="utf-8")
            outcomes["html"] = replace(response, payload=None)

        if self.save_md or self.save_txt:
            # Lazy imports avoid a circular dep with hudoc_py.text and keep
            # the html2text dependency out of the import path for callers
            # that only need raw HTML.
            from ..text import html_to_md, html_to_text

            if self.save_md:
                (self.md_dir / f"{itemid}.md").write_text(html_to_md(html), encoding="utf-8")
                outcomes["md"] = DownloadResponse(
                    status="derived",
                    url=response.url,
                    http_status=response.http_status,
                    content_type="text/markdown; charset=utf-8",
                    attempts=response.attempts,
                    retrieved_at=response.retrieved_at,
                )
            if self.save_txt:
                (self.txt_dir / f"{itemid}.txt").write_text(html_to_text(html), encoding="utf-8")
                outcomes["txt"] = DownloadResponse(
                    status="derived",
                    url=response.url,
                    http_status=response.http_status,
                    content_type="text/plain; charset=utf-8",
                    attempts=response.attempts,
                    retrieved_at=response.retrieved_at,
                )

        self.last_outcomes[itemid] = outcomes
        return True

    async def download_batch(
        self,
        itemids: list[str],
        *,
        concurrency: int = config.HUDOC_CONCURRENCY,
    ) -> dict[str, bool]:
        """Download many itemids in parallel; returns {itemid: success}."""
        sem = asyncio.Semaphore(concurrency)
        results: dict[str, bool] = {}

        async with aiohttp.ClientSession(headers=DOWNLOAD_HEADERS) as session:

            async def bounded(iid: str) -> None:
                async with sem:
                    ok = await self.fetch_and_save(session, iid)
                    results[iid] = ok

            await asyncio.gather(*(bounded(i) for i in itemids))

        success = sum(1 for v in results.values() if v)
        logger.info("Batch finished: %d/%d downloaded successfully.", success, len(itemids))
        return results
