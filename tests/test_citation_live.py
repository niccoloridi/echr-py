"""Opt-in live checks for stable citation authority and HUDOC metadata."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from hudoc_py.citations import import_authority_pdf, resolve_citations
from hudoc_py.main.client import AsyncHudocClient
from hudoc_py.models import Case

pytestmark = pytest.mark.live


@pytest.mark.skipif(
    os.getenv("HUDOC_CITATION_LIVE_TESTS") != "1",
    reason="set HUDOC_CITATION_LIVE_TESTS=1 for live HUDOC citation checks",
)
def test_series_a_reporter_only_resolves_to_stable_historical_ecli():
    source = Case.model_validate(
        {
            "itemid": "live-citation-source",
            "ecli": "ECLI:CE:ECHR:2005:0512JUD004622199",
            "kpdate": "2005-05-12",
            "scl": "Series A no. 58",
        }
    )

    async def run():
        async with AsyncHudocClient() as client:
            return await resolve_citations([source], client=client)

    result = asyncio.run(run())
    assert result.resolutions[0].status == "resolved_authority"
    assert result.resolutions[0].target.ecli == "ECLI:CE:ECHR:1983:0210JUD000729975"


@pytest.mark.skipif(
    not os.getenv("HUDOC_CITATION_AUTHORITY_PDF"),
    reason="set HUDOC_CITATION_AUTHORITY_PDF to the current official PDF",
)
def test_current_official_authority_pdf_import(tmp_path):
    authority = import_authority_pdf(Path(os.environ["HUDOC_CITATION_AUTHORITY_PDF"]), tmp_path)
    assert authority.coverage == "full"
    assert len(authority.entries) > 1_000
    assert authority.source_sha256 and len(authority.source_sha256) == 64
