"""Tests for French-sibling text fallback and provenance stamping."""

from __future__ import annotations

import asyncio

from hudoc_py import _aio
from hudoc_py.models import Case, CaseCollection

_HTML = "<html><body><p>Judgment body text here.</p></body></html>"


def _fake_html_source(available: dict[str, str]):
    """Return a fake fetch_document_html that serves HTML for known itemids."""

    async def fake(session, itemid, *, max_retries=3):
        return available.get(itemid)

    return fake


def test_own_text_stamps_own_provenance(monkeypatch):
    monkeypatch.setattr(_aio, "fetch_document_html", _fake_html_source({"e1": _HTML}))
    case = Case.model_validate({"itemid": "e1", "languageisocode": "ENG"})

    async def run():
        import aiohttp

        async with aiohttp.ClientSession() as s:
            return await _aio._fetch_case_text(s, case)

    ok = asyncio.run(run())
    assert ok is True
    assert case.text and "Judgment body" in case.text
    assert case.text_source_itemid == "e1"
    assert case.text_source_language == "ENG"
    assert case.text_provenance is not None
    assert case.text_provenance.is_fallback is False


def test_french_fallback_stamps_fre_provenance(monkeypatch):
    # Own itemid has no text; the French sibling does.
    monkeypatch.setattr(_aio, "fetch_document_html", _fake_html_source({"f1": _HTML}))
    case = Case.model_validate(
        {"itemid": "e1", "languageisocode": "ENG", "french_itemid": "f1"}
    )

    async def run():
        import aiohttp

        async with aiohttp.ClientSession() as s:
            return await _aio._fetch_case_text(s, case)

    ok = asyncio.run(run())
    assert ok is True
    assert case.text and "Judgment body" in case.text
    assert case.text_source_itemid == "f1"
    assert case.text_source_language == "FRE"
    assert case.text_provenance is not None
    assert case.text_provenance.is_fallback is True


def test_french_fallback_disabled(monkeypatch):
    monkeypatch.setattr(_aio, "fetch_document_html", _fake_html_source({"f1": _HTML}))
    case = Case.model_validate(
        {"itemid": "e1", "languageisocode": "ENG", "french_itemid": "f1"}
    )

    async def run():
        import aiohttp

        async with aiohttp.ClientSession() as s:
            return await _aio._fetch_case_text(s, case, french_fallback=False)

    ok = asyncio.run(run())
    assert ok is False
    assert case.text is None


def test_rich_population_uses_source_aware_html_spine():
    html = (
        "<p>THE FACTS</p><p>1. Facts.</p><p>THE LAW</p>"
        "<p>2. Reasons.</p><p>FOR THESE REASONS</p><p>Holds.</p>"
    )
    case = Case.model_validate(
        {"itemid": "001-structured", "languageisocode": "ENG", "doctype": "HEJUD"}
    )

    _aio._populate_text(case, html, rich_sections=True)

    assert case.sections is not None
    assert case.sections.status == "complete"
    assert case.sections.spine is not None
    assert case.sections.spine.source_format == "hudoc_html"
    assert case.sections.spine.document_id == "001-structured"
    assert [block.source_tag for block in case.sections.spine.blocks] == ["p"] * 6


def test_hydrate_texts_counters(monkeypatch):
    monkeypatch.setattr(
        _aio, "fetch_document_html", _fake_html_source({"e1": _HTML, "f2": _HTML})
    )
    coll = CaseCollection(
        [
            Case.model_validate({"itemid": "e1", "languageisocode": "ENG"}),  # own text
            Case.model_validate(  # fallback to sibling
                {"itemid": "e2", "languageisocode": "ENG", "french_itemid": "f2"}
            ),
            Case.model_validate({"itemid": "e3", "languageisocode": "ENG"}),  # missing
        ]
    )
    counts = asyncio.run(_aio.hydrate_texts(coll))
    assert counts == {"fetched": 2, "fallback_used": 1, "missing": 1}
    assert coll[1].text_source_language == "FRE"
