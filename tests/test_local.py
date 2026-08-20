"""Tests for the local corpus convenience layer (registry / search / browse)."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("pandas")

from hudoc_py.local import (  # noqa: E402
    available_tables,
    export_data,
    generate_codebook_markdown,
    run_browse,
    run_search,
    write_codebook,
)


def _make_corpus(tmp_path):
    import pandas as pd

    cases = pd.DataFrame(
        [
            {"itemid": "e1", "docname": "CASE OF A v. FRANCE", "respondent": "FRA",
             "kp_date": "2020-05-01", "importance": "1", "represented_by": "DUPONT M."},
            {"itemid": "e2", "docname": "CASE OF B v. ITALY", "respondent": "ITA",
             "kp_date": "2018-03-15", "importance": "3", "represented_by": "ROSSI G."},
        ]
    )
    cases.to_parquet(tmp_path / "cases.parquet", index=False)

    with (tmp_path / "texts.jsonl").open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"itemid": "e1", "source_itemid": "e1",
                             "source_language": "ENG",
                             "text": "The applicant alleged torture under Article 3."}) + "\n")
        fh.write(json.dumps({"itemid": "e2", "source_itemid": "e2",
                             "source_language": "ENG",
                             "text": "This case concerns freedom of expression."}) + "\n")

    return tmp_path


def test_available_tables(tmp_path):
    _make_corpus(tmp_path)
    names = {n for n, _fmt, _p in available_tables(tmp_path)}
    assert {"cases", "texts"} <= names


def test_search_text_enriched(tmp_path):
    _make_corpus(tmp_path)
    out = run_search(tmp_path, "text", "torture", fmt="json")
    rows = json.loads(out)
    assert len(rows) == 1
    assert rows[0]["itemid"] == "e1"
    assert rows[0]["docname"] == "CASE OF A v. FRANCE"  # enriched from cases
    assert "torture" in rows[0]["snippet"].lower()


def test_search_party(tmp_path):
    _make_corpus(tmp_path)
    out = run_search(tmp_path, "party", "rossi", fmt="json")
    rows = json.loads(out)
    assert [r["itemid"] for r in rows] == ["e2"]


def test_search_list(tmp_path):
    _make_corpus(tmp_path)
    out = run_search(tmp_path, "list", "respondent", fmt="json")
    rows = json.loads(out)
    respondents = {r["respondent"] for r in rows}
    assert respondents == {"FRA", "ITA"}


def test_search_date_filter(tmp_path):
    _make_corpus(tmp_path)
    out = run_search(tmp_path, "party", "case", fmt="json", date_from="2019-01-01")
    rows = json.loads(out)
    # Only the 2020 case survives the ISO date filter.
    assert [r["itemid"] for r in rows] == ["e1"]


def test_browse_list_and_stats(tmp_path):
    _make_corpus(tmp_path)
    listing = run_browse(tmp_path)
    assert "cases" in listing and "texts" in listing

    cols = run_browse(tmp_path, "cases", columns=True)
    assert "docname" in cols

    stats = run_browse(tmp_path, "cases", stats=True)
    assert "rows" in stats
    assert "respondent" in stats  # categorical value counts


def test_browse_unknown_table_suggests(tmp_path):
    _make_corpus(tmp_path)
    out = run_browse(tmp_path, "case")  # missing 's'
    assert "not found" in out
    assert "cases" in out  # fuzzy suggestion


def test_export_csv(tmp_path):
    _make_corpus(tmp_path)
    out_dir = tmp_path / "export"
    results = export_data(tmp_path, out_dir, fmt="csv")
    assert "cases" in results and "texts" in results
    assert (out_dir / "cases.csv").exists()
    # Nested list column (respondent/articles) flattened to a JSON string.
    text = (out_dir / "cases.csv").read_text(encoding="utf-8")
    assert "e1" in text


def test_export_xlsx(tmp_path):
    pytest.importorskip("openpyxl")
    _make_corpus(tmp_path)
    out_dir = tmp_path / "export"
    results = export_data(tmp_path, out_dir, fmt="xlsx", tables=["cases"])
    assert set(results) == {"cases"}
    assert (out_dir / "cases.xlsx").exists()


def test_codebook_generation(tmp_path):
    md = generate_codebook_markdown()
    assert "# echr-py Corpus Codebook" in md
    assert "## `cases`" in md
    assert "itemid" in md
    # Enumerated values render.
    assert "GRANDCHAMBER" in md
    out = write_codebook(tmp_path / "CODEBOOK.md")
    assert out.exists()
    assert out.read_text(encoding="utf-8").startswith("# echr-py Corpus Codebook")
