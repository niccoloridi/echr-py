"""Opt-in live acceptance checks; disabled unless HUDOC_EXEC_LIVE_TESTS=1."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import aiohttp
import pytest

from hudoc_py import config
from hudoc_py.execution import aio as exec_aio
from hudoc_py.execution.raw_download import fetch_exec_document_pdf
from hudoc_py.execution.related_scraper import RelatedTabScraper
from hudoc_py.execution.textract import extract_text, ocr_pdf

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("HUDOC_EXEC_LIVE_TESTS") != "1",
        reason="set HUDOC_EXEC_LIVE_TESTS=1 to run live acceptance tests",
    ),
]


def _appno() -> str:
    # LASHMANKIN AND OTHERS: a public leading case with linked documents and
    # repetitive cases. Override when the live fixture changes upstream.
    return os.environ.get("HUDOC_EXEC_LIVE_APPNO", "57818/09")


def _case_id() -> str:
    return os.environ.get("HUDOC_EXEC_LIVE_CASE_ID", "004-47097")


def test_live_case_and_document_discovery():
    case = asyncio.run(exec_aio.fetch_case(_appno(), with_documents=True))
    assert case is not None and case.appno
    assert any(
        (
            case.action_plans,
            case.action_reports,
            case.communications,
            case.cm_decisions,
            case.resolutions,
        )
    )


def test_live_raw_pdf_and_local_conversion():
    case = asyncio.run(exec_aio.fetch_case(_appno(), with_documents=True))
    assert case is not None
    documents = [*case.action_plans, *case.action_reports]
    if not documents:
        pytest.skip("case has no action plan/report")

    async def fetch():
        async with aiohttp.ClientSession() as session:
            return await fetch_exec_document_pdf(session, documents[0].content_store_id)

    data = asyncio.run(fetch())
    assert data and extract_text(data, file_type="pdf")


def test_live_related_page_structure():
    async def scrape():
        async with RelatedTabScraper() as scraper:
            structure = await scraper.inspect_related_tab_structure(_case_id())
            documents = await scraper.scrape_related(_case_id())
            return structure, documents

    result, documents = asyncio.run(scrape())
    assert result["has_related_tab"]
    assert documents and all(document.reference for document in documents)


def test_live_optional_mistral_ocr():
    sample = os.environ.get("HUDOC_EXEC_LIVE_SCANNED_PDF")
    if not config.get_mistral_api_key() or not sample:
        pytest.skip("MISTRAL_API_KEY and HUDOC_EXEC_LIVE_SCANNED_PDF are required")
    assert ocr_pdf(Path(sample).read_bytes())
