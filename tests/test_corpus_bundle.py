"""Neutral corpus selection, rich artifacts, validation, and packaging."""

from __future__ import annotations

import asyncio
import json
import zipfile

import pandas as pd

from hudoc_py.bilingual import corpus as corpus_mod
from hudoc_py.bilingual.bundle import (
    generate_corpus_manifest,
    package_corpus,
    validate_corpus,
)
from hudoc_py.cli import build_parser
from hudoc_py.models import Case, CaseCollection


def test_load_selection_supported_shapes(tmp_path):
    selection = tmp_path / "selection.json"
    selection.write_text(
        json.dumps(
            {
                "selection": [
                    "001-1",
                    "ECLI:CE:ECHR:2020:0101JUD000000100",
                    {"itemid": "001-2", "ecli": "ECLI:EXAMPLE"},
                    "001-1",
                ]
            }
        ),
        encoding="utf-8",
    )
    entries = corpus_mod.load_selection(selection)
    assert [(entry.itemid, entry.ecli) for entry in entries] == [
        ("001-1", None),
        (None, "ECLI:CE:ECHR:2020:0101JUD000000100"),
        ("001-2", "ECLI:EXAMPLE"),
    ]
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    corpus_mod._snapshot_selection(corpus, entries)
    snapshot = json.loads((corpus / "selection.json").read_text(encoding="utf-8"))
    assert snapshot["schema_version"] == "hudoc-selection/v1"
    corpus_mod._snapshot_selection(corpus, entries)
    try:
        corpus_mod._snapshot_selection(corpus, [corpus_mod.SelectionEntry(itemid="different")])
    except ValueError as exc:
        assert "differs" in str(exc)
    else:
        raise AssertionError("changed selection was accepted")


def test_selection_resolution_preserves_missing_and_identity_mismatch(monkeypatch):
    from hudoc_py.main import client as client_mod

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def fetch_by_itemids(self, itemids):
            return [{"itemid": "001-1", "ecli": "ECLI:WRONG"}]

        async def search(self, **kwargs):
            return [{"itemid": "001-3", "ecli": "ECLI:EXACT"}]

    monkeypatch.setattr(client_mod, "AsyncHudocClient", FakeClient)
    cases, failures = asyncio.run(
        corpus_mod._fetch_selection(
            [
                corpus_mod.SelectionEntry(itemid="001-1", ecli="ECLI:EXPECTED"),
                corpus_mod.SelectionEntry(itemid="001-2"),
                corpus_mod.SelectionEntry(ecli="ECLI:EXACT"),
            ]
        )
    )
    assert [case.itemid for case in cases] == ["001-3"]
    assert {failure["code"] for failure in failures} == {
        "selection_identity_mismatch",
        "selection_itemid_not_found",
    }


def test_language_selection_hydrates_every_official_version(tmp_path, monkeypatch):
    from hudoc_py import _aio

    selection = tmp_path / "selection.jsonl"
    selection.write_text(
        json.dumps(
            {
                "ecli": "ECLI:EXAMPLE",
                "primary_itemid": "eng-1",
                "language_itemids": {"ENG": ["eng-1"], "FRE": ["fre-1"]},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    versions = CaseCollection.from_records(
        [
            {
                "itemid": "eng-1",
                "ecli": "ECLI:EXAMPLE",
                "languageisocode": "ENG",
                "docname": "CASE OF EXAMPLE",
            },
            {
                "itemid": "fre-1",
                "ecli": "ECLI:EXAMPLE",
                "languageisocode": "FRE",
                "docname": "AFFAIRE EXAMPLE",
            },
        ]
    )

    async def fake_selection(entries):
        return versions, []

    async def fake_html(session, itemid, *, max_retries=3):
        return f'<html><body><h2>THE FACTS</h2><p id="{itemid}">1. {itemid}</p></body></html>'

    monkeypatch.setattr(corpus_mod, "_fetch_selection", fake_selection)
    monkeypatch.setattr(_aio, "fetch_document_html", fake_html)
    report = asyncio.run(
        corpus_mod.build_corpus(
            tmp_path / "corpus",
            selection=selection,
            rescue=False,
            rich_sections=True,
        )
    )
    corpus = tmp_path / "corpus"
    assert report.selection == {
        "requested": 1,
        "metadata_rows": 2,
        "language_versions": 2,
        "failures": 0,
    }
    assert set(pd.read_parquet(corpus / "language-versions.parquet")["itemid"]) == {
        "eng-1",
        "fre-1",
    }
    language_texts = [
        json.loads(line) for line in (corpus / "language-texts.jsonl").read_text().splitlines()
    ]
    assert {row["itemid"] for row in language_texts} == {"eng-1", "fre-1"}
    compatibility = [json.loads(line) for line in (corpus / "texts.jsonl").read_text().splitlines()]
    assert {row["itemid"] for row in compatibility} == {"eng-1"}
    assert set(pd.read_parquet(corpus / "paragraphs.parquet")["itemid"]) == {
        "eng-1",
        "fre-1",
    }
    from hudoc_py.local import build_paragraph_index

    indexed = build_paragraph_index(corpus, corpus / "paragraphs.sqlite")
    assert indexed["documents"] == 2
    generate_corpus_manifest(corpus)
    assert validate_corpus(corpus).valid


def test_rich_build_writes_neutral_tables(tmp_path, monkeypatch):
    from hudoc_py import _aio

    rows = [
        {
            "itemid": "001-1",
            "languageisocode": "ENG",
            "ecli": "ECLI:EXAMPLE",
            "appno": "1/00",
            "docname": "CASE OF EXAMPLE v. FRANCE",
        }
    ]

    async def fake_search(*, query=None, limit=None, page_size=100, **filters):
        return CaseCollection(Case.model_validate(row) for row in rows)

    async def fake_html(session, itemid, *, max_retries=3):
        return """<html><body><h2>PROCEDURE</h2><p id="kp-1">1. Procedure.</p>
        <h2>FOR THESE REASONS</h2><p id="kp-2">1. Holds unanimously.</p></body></html>"""

    monkeypatch.setattr(corpus_mod, "search", fake_search)
    monkeypatch.setattr(_aio, "fetch_document_html", fake_html)
    report = asyncio.run(corpus_mod.build_corpus(tmp_path, rescue=False, rich_sections=True))

    assert report.rich_tables is not None
    assert report.rich_tables["paragraphs"] >= 2
    for name in (
        "paragraphs",
        "sections",
        "opinions",
        "bench",
        "judges",
        "footnotes",
        "dispositive",
    ):
        assert (tmp_path / f"{name}.parquet").exists()
    assert (tmp_path / "sections.jsonl").exists()
    assert json.loads((tmp_path / "corpus-manifest.json").read_text())["schema_version"] == (
        "hudoc-corpus/v1"
    )


def test_empty_case_table_retains_contract_columns(tmp_path):
    corpus_mod._save_parquet([], tmp_path / "cases.parquet", drop_text=True)
    frame = pd.read_parquet(tmp_path / "cases.parquet")
    assert "itemid" in frame.columns
    assert "text" not in frame.columns


def _minimal_corpus(root):
    pd.DataFrame([{"itemid": "001-1", "docname": "Example"}]).to_parquet(
        root / "cases.parquet", index=False
    )
    (root / "report.json").write_text("{}\n", encoding="utf-8")


def test_package_is_deterministic_and_validates_checksums(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    _minimal_corpus(corpus)
    (corpus / ".DS_Store").write_bytes(b"finder metadata")
    cache = corpus / ".ruff_cache"
    cache.mkdir()
    (cache / "state").write_text("local cache", encoding="utf-8")

    first = package_corpus(corpus, tmp_path / "one.zip")
    second = package_corpus(corpus, tmp_path / "two.zip")
    assert first.sha256 == second.sha256
    assert validate_corpus(corpus).valid is True
    with zipfile.ZipFile(first.archive) as archive:
        assert archive.namelist() == ["cases.parquet", "corpus-manifest.json", "report.json"]

    (corpus / "report.json").write_text('{"changed": true}\n', encoding="utf-8")
    report = validate_corpus(corpus)
    assert report.valid is False
    assert {issue.code for issue in report.issues} == {"checksum_mismatch"}


def test_corpus_release_cli_parses():
    parser = build_parser()
    build = parser.parse_args(
        ["corpus", "build", "--selection", "selection.json", "--rich-sections", "--out", "out"]
    )
    assert build.selection == "selection.json"
    assert build.rich_sections is True
    validate = parser.parse_args(
        ["corpus", "validate", "--in", "corpus", "--out", "validation.json"]
    )
    assert validate.corpus_command == "validate"
    package = parser.parse_args(["corpus", "package", "--in", "corpus", "--out", "dist"])
    assert package.corpus_command == "package"
