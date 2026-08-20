"""Raw HUDOC-EXEC PDF retrieval with resumable JSONL manifests."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import aiohttp

from .. import config
from ..utils.downloads import ResponseTooLargeError, read_limited
from ..utils.jsonl import append_jsonl, load_processed_ids
from .downloader import DOWNLOAD_HEADERS

logger = logging.getLogger(__name__)


def _pdf_url(content_store_id: str) -> str:
    return f"{config.HUDOC_EXEC_PDF_URL}?library=EXEC&id={content_store_id}"


async def fetch_exec_document_pdf(
    session: aiohttp.ClientSession,
    content_store_id: str,
    *,
    max_retries: int = config.HUDOC_MAX_RETRIES,
) -> bytes | None:
    """Fetch PDF bytes by ``execcontentstoreid`` (never ``execidentifier``)."""
    for attempt in range(1, max_retries + 1):
        try:
            async with session.get(
                _pdf_url(content_store_id), timeout=aiohttp.ClientTimeout(total=90)
            ) as response:
                if response.status == 200:
                    data = await read_limited(
                        response, max_bytes=config.HUDOC_MAX_BINARY_BYTES
                    )
                    if len(data) < 20:
                        logger.warning("Empty PDF for %s", content_store_id)
                        return None
                    return data
                if response.status == 204:
                    return None
                if response.status in {429, 500, 502, 503, 504} and attempt < max_retries:
                    await asyncio.sleep(float(attempt))
                    continue
                logger.error("Failed PDF %s: status %d", content_store_id, response.status)
                return None
        except (TimeoutError, aiohttp.ClientError, ResponseTooLargeError) as exc:
            if attempt == max_retries:
                logger.error("Exception fetching PDF %s: %s", content_store_id, exc)
                return None
            await asyncio.sleep(float(attempt))
    return None


def _field(item: Any, *names: str) -> Any:
    for name in names:
        value = item.get(name) if isinstance(item, dict) else getattr(item, name, None)
        if value not in (None, ""):
            return value
    return None


class AsyncExecRawDownloader:
    """Download raw PDFs and optionally extract text, with safe resume semantics."""

    def __init__(
        self,
        output_root: str | Path,
        *,
        manifest_path: str | Path | None = None,
        max_retries: int = config.HUDOC_MAX_RETRIES,
    ):
        self.output_root = Path(output_root)
        self.pdf_dir = self.output_root / "pdf"
        self.text_dir = self.output_root / "text"
        self.manifest_path = Path(manifest_path or self.output_root / "manifest.jsonl")
        self.max_retries = max_retries
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self.text_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe(value: str) -> str:
        return value.replace("/", "_").replace("\\", "_").replace(":", "_")

    async def download(
        self,
        documents: list[Any],
        *,
        concurrency: int = 10,
        resume: bool = True,
        extract: bool = False,
        ocr: bool = False,
    ) -> dict[str, int]:
        processed = (
            load_processed_ids(self.manifest_path, id_field="execidentifier", ok_only=True)
            if resume
            else set()
        )
        stats = {"total": len(documents), "downloaded": 0, "skipped": 0, "failed": 0}
        sem = asyncio.Semaphore(max(1, concurrency))

        async with aiohttp.ClientSession(headers=DOWNLOAD_HEADERS) as session:

            async def one(item: Any) -> None:
                identifier = str(_field(item, "execidentifier", "itemid", "id") or "")
                store_id = str(_field(item, "content_store_id", "execcontentstoreid") or "")
                if not identifier or not store_id:
                    stats["failed"] += 1
                    return
                if identifier in processed:
                    stats["skipped"] += 1
                    return
                async with sem:
                    data = await fetch_exec_document_pdf(
                        session, store_id, max_retries=self.max_retries
                    )
                record: dict[str, Any] = {
                    "execidentifier": identifier,
                    "execcontentstoreid": store_id,
                    "status": "error",
                }
                if data is None:
                    record["error"] = "download failed or no PDF content"
                    stats["failed"] += 1
                else:
                    safe = self._safe(identifier)
                    pdf_path = self.pdf_dir / f"{safe}.pdf"
                    pdf_path.write_bytes(data)
                    record.update(status="ok", pdf_file=str(pdf_path), pdf_bytes=len(data))
                    if extract:
                        from .textract import extract_text

                        text = await asyncio.to_thread(extract_text, data, file_type="pdf", ocr=ocr)
                        text_path = self.text_dir / f"{safe}.txt"
                        text_path.write_text(text, encoding="utf-8")
                        record.update(text_file=str(text_path), text_chars=len(text))
                    stats["downloaded"] += 1
                append_jsonl(self.manifest_path, record)

            await asyncio.gather(*(one(item) for item in documents))
        return stats


__all__ = ["AsyncExecRawDownloader", "fetch_exec_document_pdf"]
