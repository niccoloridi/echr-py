"""HUDOC-EXEC document downloader.

Mirrors :mod:`hudoc_py.main.downloader` but points at the EXEC content store.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import aiohttp

from .. import config
from ..utils.downloads import ResponseTooLargeError, read_limited, read_text_limited

logger = logging.getLogger(__name__)

DOWNLOAD_HEADERS = {
    "Accept": "text/html, */*; q=0.01",
    "Accept-Language": "en-GB,en;q=0.9",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "User-Agent": "Mozilla/5.0 echr-py/0.1",
}


def _doc_url(content_store_id: str) -> str:
    return f"{config.HUDOC_EXEC_HTML_URL}?library=EXEC&id={content_store_id}"


def _docx_url(content_store_id: str) -> str:
    # A `filename` param is required by the conversion endpoint (verified on
    # the ECHR library); the value only names the download.
    safe = content_store_id.replace("/", "_")
    return (
        f"{config.HUDOC_EXEC_DOCX_URL}?library=EXEC&id={content_store_id}"
        f"&filename={safe}.docx"
    )


async def fetch_exec_document_html(
    session: aiohttp.ClientSession,
    content_store_id: str,
    *,
    max_retries: int = config.HUDOC_MAX_RETRIES,
) -> str | None:
    """Fetch raw HTML body for a HUDOC-EXEC document.

    ``content_store_id`` must be the **storage UUID** (the
    ``execcontentstoreid`` field on an :class:`ExecutionDocument`), not the
    human-readable ``execidentifier`` – the HUDOC-EXEC conversion endpoint
    silently returns 204 No Content for the latter.
    """
    url = _doc_url(content_store_id)
    doc_id = content_store_id  # internal alias for log messages
    for attempt in range(1, max_retries + 1):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    html = await read_text_limited(resp, max_bytes=config.HUDOC_MAX_HTML_BYTES)
                    if not html.strip():
                        logger.warning("Empty HTML for %s", doc_id)
                        return None
                    return html
                if resp.status == 204:
                    logger.info("No content (204) for %s", doc_id)
                    return None
                if resp.status in (429, 500, 502, 503, 504) and attempt < max_retries:
                    await asyncio.sleep(1.0 * attempt)
                    continue
                logger.error("Failed %s: status %d", doc_id, resp.status)
                return None
        except (aiohttp.ClientError, ResponseTooLargeError) as exc:
            if attempt == max_retries:
                logger.error("Exception fetching %s: %s", doc_id, exc)
                return None
            await asyncio.sleep(1.0 * attempt)
    return None


async def fetch_exec_document_docx(
    session: aiohttp.ClientSession,
    content_store_id: str,
    *,
    max_retries: int = config.HUDOC_MAX_RETRIES,
) -> bytes | None:
    """Fetch raw DOCX bytes for a HUDOC-EXEC document by ``execcontentstoreid``.

    Same store-UUID caveat as :func:`fetch_exec_document_html`.
    """
    url = _docx_url(content_store_id)
    for attempt in range(1, max_retries + 1):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status == 200:
                    data = await read_limited(resp, max_bytes=config.HUDOC_MAX_BINARY_BYTES)
                    if not data:
                        logger.warning("Empty DOCX for %s", content_store_id)
                        return None
                    return data
                if resp.status == 204:
                    logger.info("No content (204) for %s", content_store_id)
                    return None
                if resp.status in (429, 500, 502, 503, 504) and attempt < max_retries:
                    await asyncio.sleep(1.0 * attempt)
                    continue
                logger.error("Failed DOCX %s: status %d", content_store_id, resp.status)
                return None
        except (aiohttp.ClientError, ResponseTooLargeError) as exc:
            if attempt == max_retries:
                logger.error("Exception fetching DOCX %s: %s", content_store_id, exc)
                return None
            await asyncio.sleep(1.0 * attempt)
    return None


class AsyncExecDocumentDownloader:
    """Batch HUDOC-EXEC document downloader with HTML/MD/TXT/DOCX outputs."""

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

        self.html_dir = self.output_root / "html"
        self.md_dir = self.output_root / "md"
        self.txt_dir = self.output_root / "txt"
        for d in (self.html_dir, self.md_dir, self.txt_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.docx_dir = self.output_root / "docx"
        if self.save_docx:
            self.docx_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_filename(doc_id: str) -> str:
        return doc_id.replace("/", "_").replace("\\", "_")

    async def fetch_and_save(
        self,
        session: aiohttp.ClientSession,
        content_store_id: str,
        *,
        filename: str | None = None,
    ) -> bool:
        """Fetch one document by its ``execcontentstoreid``. Filename on disk
        defaults to the store id, but you can pass an ``execidentifier``-based
        name via ``filename`` for human-readable files."""
        safe = self._safe_filename(filename or content_store_id)
        saved_any = False

        if self.save_docx:
            docx = await fetch_exec_document_docx(
                session, content_store_id, max_retries=self.max_retries
            )
            if docx is not None:
                (self.docx_dir / f"{safe}.docx").write_bytes(docx)
                saved_any = True

        if not (self.save_html or self.save_md or self.save_txt):
            return saved_any

        html = await fetch_exec_document_html(
            session, content_store_id, max_retries=self.max_retries
        )
        if html is None:
            return saved_any

        if self.save_html:
            (self.html_dir / f"{safe}.html").write_text(html, encoding="utf-8")

        if self.save_md or self.save_txt:
            from ..text import html_to_md, html_to_text

            if self.save_md:
                (self.md_dir / f"{safe}.md").write_text(html_to_md(html), encoding="utf-8")
            if self.save_txt:
                (self.txt_dir / f"{safe}.txt").write_text(html_to_text(html), encoding="utf-8")

        return True

    async def download_batch(
        self,
        content_store_ids: list[str],
        *,
        concurrency: int = config.HUDOC_CONCURRENCY,
        filenames: dict[str, str] | None = None,
    ) -> dict[str, bool]:
        """Download many EXEC documents by ``execcontentstoreid``.

        Pass ``filenames`` as a ``{content_store_id: pretty_filename}`` map
        to get human-readable output filenames.
        """
        sem = asyncio.Semaphore(concurrency)
        results: dict[str, bool] = {}
        filenames = filenames or {}

        async with aiohttp.ClientSession(headers=DOWNLOAD_HEADERS) as session:

            async def bounded(csid: str) -> None:
                async with sem:
                    results[csid] = await self.fetch_and_save(
                        session, csid, filename=filenames.get(csid),
                    )

            await asyncio.gather(*(bounded(d) for d in content_store_ids))

        success = sum(1 for v in results.values() if v)
        logger.info(
            "EXEC batch finished: %d/%d downloaded successfully.",
            success, len(content_store_ids),
        )
        return results
