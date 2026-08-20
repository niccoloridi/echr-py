"""Optional browser fallback for HUDOC-EXEC Related-tab discovery."""

from __future__ import annotations

import asyncio
import html
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .. import config
from ..utils.jsonl import append_jsonl, load_processed_ids


class ScrapedRelatedDocument(BaseModel):
    case_id: str
    title: str
    date: str = ""
    document_type: str = "unknown"
    url: str = ""
    reference: str = ""


def infer_document_type(title: str) -> str:
    value = title.casefold()
    if "action plan" in value or "plan d'action" in value:
        return "acp"
    if "action report" in value or "bilan d'action" in value or "rapport d'action" in value:
        return "acr"
    if ("decision" in value or "décision" in value) and (
        "committee" in value or "comité" in value or "cm" in value
    ):
        return "CMDEC"
    if "resolution" in value or "résolution" in value:
        return "EXECUTION"
    if "memorandum" in value or "mémo" in value or "memo" in value:
        return "HEXEC"
    if "communication" in value:
        if "ngo" in value or "ong" in value or "non-gouvernement" in value:
            return "ngo"
        if "national human rights" in value or "nhri" in value or "indh" in value:
            return "nhri"
        if "applicant" in value or "requérant" in value:
            return "apo"
        if (
            "government" in value
            or "authorities" in value
            or "gouvernement" in value
            or "autorités" in value
        ):
            return "gvo"
    return "unknown"


_EXEC_IDENTIFIER_RE = re.compile(
    r'["\']execidentifier["\']\s*:\s*\[\s*["\']([^"\']+)',
    re.IGNORECASE,
)
_CASE_IDENTIFIER_RE = re.compile(r"^\d{3}-\d+$")


def _reference_from_url(url: str) -> str:
    """Extract the HUDOC-EXEC identifier embedded in a navigator hash URL."""
    match = _EXEC_IDENTIFIER_RE.search(html.unescape(url))
    return match.group(1).strip() if match else ""


class RelatedTabScraper:
    """Playwright scraper used only when API document discovery is incomplete."""

    def __init__(self, *, concurrency: int = 3, headless: bool = True):
        self.concurrency = max(1, concurrency)
        self.headless = headless
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None

    async def __aenter__(self) -> RelatedTabScraper:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise ImportError(
                "Related-tab scraping requires echr-py[scrape] and Playwright Chromium"
            ) from exc
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.headless)
        self._context = await self._browser.new_context()
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def scrape_related(self, case_id: str) -> list[ScrapedRelatedDocument]:
        if self._context is None:
            raise RuntimeError("Use RelatedTabScraper as an async context manager")
        page = await self._context.new_page()
        try:
            await page.goto(
                f"{config.HUDOC_EXEC_BASE}/eng?i={case_id}",
                wait_until="networkidle",
                timeout=45_000,
            )
            # Verified against the live HUDOC-EXEC navigator in July 2026. The
            # user-facing label is "Case Documents"; the stable DOM id remains
            # ``related``. API discovery is still preferred over this fallback.
            tab = page.locator(".navigator-modal .cm-modal-header-tabs-list > li#related:visible")
            if not await tab.count():
                return []
            await tab.first.click()
            await page.wait_for_timeout(1_000)

            selectors = (
                ".navigator-modal .viewarea > div#related "
                ".relatedLinks .linkview:visible a[href*='execidentifier']",
            )
            documents: list[ScrapedRelatedDocument] = []
            seen: set[tuple[str, str]] = set()
            for selector in selectors:
                entries = page.locator(selector)
                for index in range(await entries.count()):
                    entry = entries.nth(index)
                    title = (await entry.text_content() or "").strip()
                    url = await entry.get_attribute("href") or ""
                    reference = _reference_from_url(url)
                    key = (title, url)
                    # Case records use identifiers such as 004-63800. They are
                    # navigation entries, not execution documents.
                    if (
                        not title
                        or not reference
                        or _CASE_IDENTIFIER_RE.fullmatch(reference)
                        or key in seen
                    ):
                        continue
                    seen.add(key)
                    match = re.search(r"(\d{1,2}[/.]\d{1,2}[/.]\d{4})", title)
                    documents.append(
                        ScrapedRelatedDocument(
                            case_id=case_id,
                            title=title,
                            date=match.group(1) if match else "",
                            document_type=infer_document_type(title),
                            url=url,
                            reference=reference,
                        )
                    )
                if documents:
                    break
            return documents
        finally:
            await page.close()

    async def inspect_related_tab_structure(self, case_id: str) -> dict[str, Any]:
        if self._context is None:
            raise RuntimeError("Use RelatedTabScraper as an async context manager")
        page = await self._context.new_page()
        requests: list[str] = []
        page.on("request", lambda request: requests.append(request.url))
        try:
            await page.goto(f"{config.HUDOC_EXEC_BASE}/eng?i={case_id}", timeout=45_000)
            tab = page.locator(".navigator-modal .cm-modal-header-tabs-list > li#related:visible")
            found = bool(await tab.count())
            before = len(requests)
            if found:
                await tab.first.click()
                await page.wait_for_timeout(1_000)
            return {
                "case_id": case_id,
                "has_related_tab": found,
                "post_click_requests": requests[before:],
                "html": await page.content(),
            }
        finally:
            await page.close()

    async def scrape_batch(
        self,
        case_ids: list[str],
        *,
        checkpoint_path: str | Path | None = None,
        resume: bool = True,
    ) -> dict[str, list[ScrapedRelatedDocument]]:
        processed = (
            load_processed_ids(checkpoint_path, id_field="case_id", ok_only=True)
            if checkpoint_path and resume
            else set()
        )
        semaphore = asyncio.Semaphore(self.concurrency)
        results: dict[str, list[ScrapedRelatedDocument]] = {}

        async def one(case_id: str) -> None:
            if case_id in processed:
                return
            try:
                async with semaphore:
                    documents = await self.scrape_related(case_id)
                results[case_id] = documents
                record: dict[str, Any] = {
                    "case_id": case_id,
                    "status": "ok",
                    "documents": [document.model_dump() for document in documents],
                }
            except Exception as exc:
                results[case_id] = []
                record = {"case_id": case_id, "status": "error", "error": str(exc)}
            if checkpoint_path:
                append_jsonl(checkpoint_path, record)

        await asyncio.gather(*(one(case_id) for case_id in case_ids))
        return results


__all__ = ["RelatedTabScraper", "ScrapedRelatedDocument", "infer_document_type"]
