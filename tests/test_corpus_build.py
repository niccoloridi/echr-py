"""Tests for the end-to-end corpus build orchestration."""

from __future__ import annotations

import asyncio
import json

import pytest

from hudoc_py.bilingual import corpus as corpus_mod
from hudoc_py.models import Case, CaseCollection

pytest.importorskip("pandas")

_HTML = "<html><body><p>Body text.</p></body></html>"


def _fake_search_factory(rows):
    async def fake_search(*, query=None, limit=None, page_size=100, **filters):
        coll = CaseCollection(Case.model_validate(r) for r in rows)
        coll.result_count = len(rows)
        return coll

    return fake_search


def _fake_html_factory(available):
    async def fake_html(session, itemid, *, max_retries=3):
        return available.get(itemid)

    return fake_html


def _rows():
    return [
        {"itemid": "e1", "languageisocode": "ENG", "ecli": "ECLI:A",
         "appno": "1/00", "docname": "CASE OF A v. FRANCE"},
        {"itemid": "f1", "languageisocode": "FRE", "ecli": "ECLI:A",
         "appno": "1/00", "docname": "AFFAIRE A c. FRANCE"},
        {"itemid": "e2", "languageisocode": "ENG", "ecli": "ECLI:B", "appno": "2/00"},
    ]


def test_corpus_build_artifacts(tmp_path, monkeypatch):
    from hudoc_py import _aio

    monkeypatch.setattr(corpus_mod, "search", _fake_search_factory(_rows()))
    monkeypatch.setattr(
        _aio, "fetch_document_html", _fake_html_factory({"e1": _HTML, "e2": _HTML})
    )

    report = asyncio.run(
        corpus_mod.build_corpus(tmp_path, rescue=False, with_texts=True)
    )

    # Artifacts exist.
    assert (tmp_path / "raw.jsonl").exists()
    assert (tmp_path / "cases.parquet").exists()
    assert (tmp_path / "duplicates.parquet").exists()
    assert (tmp_path / "texts.jsonl").exists()
    assert (tmp_path / "report.json").exists()

    # Reconcile matched the ENG/FRE pair.
    assert report.searched == 3
    assert report.reconcile.eng_matched_fre == 1

    # texts.jsonl carries provenance columns.
    text_rows = [json.loads(ln) for ln in (tmp_path / "texts.jsonl").read_text().splitlines()]
    assert all("source_itemid" in r and "source_language" in r for r in text_rows)
    assert {r["itemid"] for r in text_rows} == {"e1", "e2"}

    # cases.parquet has no text column (kept slim); french_itemid present.
    import pandas as pd

    df = pd.read_parquet(tmp_path / "cases.parquet")
    assert "text" not in df.columns
    assert "french_itemid" in df.columns


def test_corpus_build_texts_resume(tmp_path, monkeypatch):
    from hudoc_py import _aio

    monkeypatch.setattr(corpus_mod, "search", _fake_search_factory(_rows()))
    monkeypatch.setattr(
        _aio, "fetch_document_html", _fake_html_factory({"e1": _HTML, "e2": _HTML})
    )

    asyncio.run(corpus_mod.build_corpus(tmp_path, rescue=False, with_texts=True))
    first = len((tmp_path / "texts.jsonl").read_text().splitlines())

    # Second run: texts already present → nothing re-fetched, no duplicate rows.
    report2 = asyncio.run(corpus_mod.build_corpus(tmp_path, rescue=False, with_texts=True))
    second = len((tmp_path / "texts.jsonl").read_text().splitlines())
    assert second == first
    assert report2.texts["skipped"] == 2


def test_load_cases_round_trip(tmp_path, monkeypatch):
    from hudoc_py import _aio

    monkeypatch.setattr(corpus_mod, "search", _fake_search_factory(_rows()))
    monkeypatch.setattr(_aio, "fetch_document_html", _fake_html_factory({}))
    asyncio.run(corpus_mod.build_corpus(tmp_path, rescue=False, with_texts=False))
    coll = corpus_mod.load_cases(tmp_path / "cases.parquet")
    assert isinstance(coll, CaseCollection)
    assert {c.itemid for c in coll} == {"e1", "e2"}
