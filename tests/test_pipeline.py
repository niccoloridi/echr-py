"""Tests for the generic pipeline runner and human-validation helpers."""

from __future__ import annotations

import json

import pytest

from hudoc_py.pipeline import (
    export_validation_sample,
    run_pipeline,
)
from hudoc_py.utils.jsonl import iter_jsonl


def _worker(item):
    if item["itemid"] == "boom":
        raise RuntimeError("kaput")
    return {
        "itemid": item["itemid"],
        "status": "ok",
        "data": {"value": item["itemid"].upper()},
        "_meta": {"input_tokens": 100, "output_tokens": 10, "cost_usd": 0.001},
    }


def _items(*ids):
    return [{"itemid": i} for i in ids]


def test_run_pipeline_writes_records_and_stats(tmp_path):
    out = tmp_path / "results.jsonl"
    stats = run_pipeline(
        _items("a", "b", "c"), _worker, output_path=out,
        max_workers=2, submit_delay=0, progress=False,
    )
    assert stats.total == 3 and stats.ok == 3 and stats.errors == 0
    assert stats.input_tokens == 300
    assert stats.cost_usd == pytest.approx(0.003)
    records = {r["itemid"]: r for r in iter_jsonl(out)}
    assert set(records) == {"a", "b", "c"}
    assert records["a"]["data"]["value"] == "A"


def test_run_pipeline_resume_skips_processed(tmp_path):
    out = tmp_path / "results.jsonl"
    run_pipeline(_items("a", "b"), _worker, output_path=out, submit_delay=0, progress=False)
    stats = run_pipeline(
        _items("a", "b", "c"), _worker, output_path=out, submit_delay=0, progress=False
    )
    assert stats.skipped == 2 and stats.processed == 1
    assert len(list(iter_jsonl(out))) == 3


def test_run_pipeline_worker_exception_becomes_error_record(tmp_path):
    out = tmp_path / "results.jsonl"
    stats = run_pipeline(
        _items("a", "boom"), _worker, output_path=out, submit_delay=0, progress=False
    )
    assert stats.ok == 1 and stats.errors == 1
    records = {r["itemid"]: r for r in iter_jsonl(out)}
    assert records["boom"]["status"] == "error"
    assert "kaput" in records["boom"]["_meta"]["error"]


def test_run_pipeline_retry_errors_reruns_only_failures(tmp_path):
    out = tmp_path / "results.jsonl"
    out.write_text(
        json.dumps({"itemid": "a", "status": "ok", "_meta": {}}) + "\n"
        + json.dumps({"itemid": "b", "status": "error", "_meta": {}}) + "\n",
        encoding="utf-8",
    )
    stats = run_pipeline(
        _items("a", "b"), _worker, output_path=out,
        retry_errors=True, submit_delay=0, progress=False, backup=False,
    )
    assert stats.skipped == 1 and stats.processed == 1


def test_run_pipeline_max_items(tmp_path):
    out = tmp_path / "results.jsonl"
    stats = run_pipeline(
        _items("a", "b", "c", "d"), _worker, output_path=out,
        max_items=2, submit_delay=0, progress=False,
    )
    assert stats.total == 2 and len(list(iter_jsonl(out))) == 2


def test_run_pipeline_custom_id_field(tmp_path):
    out = tmp_path / "results.jsonl"

    def worker(item):
        return {"execidentifier": item["execidentifier"], "status": "ok", "_meta": {}}

    stats = run_pipeline(
        [{"execidentifier": "DH-1"}, {"execidentifier": "DH-2"}], worker,
        output_path=out, id_field="execidentifier", submit_delay=0, progress=False,
    )
    assert stats.ok == 2
    # Resume respects the custom field.
    stats2 = run_pipeline(
        [{"execidentifier": "DH-1"}], worker,
        output_path=out, id_field="execidentifier", submit_delay=0, progress=False,
    )
    assert stats2.skipped == 1


def _write_results(path, n=20):
    rows = []
    for i in range(n):
        rows.append({
            "itemid": f"001-{i}",
            "status": "ok" if i % 5 else "error",
            "data": {"polarity": "positive" if i % 2 else "negative", "confidence": 0.9},
        })
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_export_validation_sample_stratified(tmp_path):
    pytest.importorskip("pandas")
    import pandas as pd

    input_path = tmp_path / "results.jsonl"
    _write_results(input_path, n=40)
    out = export_validation_sample(
        input_path, tmp_path / "sample.csv",
        sample_size=10, stratify_by="polarity", human_fields=["polarity"],
    )
    df = pd.read_csv(out)
    assert len(df) >= 10
    assert "human_polarity" in df.columns and "human_notes" in df.columns
    assert set(df["polarity"].unique()) == {"positive", "negative"}
    # Error records excluded.
    assert not (df["status"] == "error").any() if "status" in df.columns else True


def test_export_validation_sample_empty_input(tmp_path):
    input_path = tmp_path / "empty.jsonl"
    input_path.write_text("", encoding="utf-8")
    assert export_validation_sample(input_path, tmp_path / "s.csv") is None
