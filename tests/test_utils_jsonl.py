"""Tests for the resumable JSONL checkpoint helpers."""

from __future__ import annotations

from hudoc_py.utils import (
    append_jsonl,
    append_jsonl_many,
    backup_file,
    iter_jsonl,
    load_processed_ids,
    upsert_jsonl,
)


def test_append_and_iter_roundtrip(tmp_path):
    path = tmp_path / "log.jsonl"
    append_jsonl(path, {"itemid": "001-1", "status": "ok"})
    append_jsonl_many(path, [{"itemid": "001-2", "status": "ok"}, {"itemid": "001-3"}])
    records = list(iter_jsonl(path))
    assert [r["itemid"] for r in records] == ["001-1", "001-2", "001-3"]


def test_iter_skips_blank_and_malformed_lines(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text('{"itemid": "001-1"}\n\nnot json\n{"itemid": "001-2"}\n', encoding="utf-8")
    assert [r["itemid"] for r in iter_jsonl(path)] == ["001-1", "001-2"]


def test_iter_missing_file_yields_nothing(tmp_path):
    assert list(iter_jsonl(tmp_path / "nope.jsonl")) == []


def test_load_processed_ids_with_id_fallback(tmp_path):
    path = tmp_path / "log.jsonl"
    append_jsonl_many(path, [{"itemid": "001-1"}, {"id": "001-2"}, {"other": "x"}])
    assert load_processed_ids(path) == {"001-1", "001-2"}


def test_load_processed_ids_custom_field(tmp_path):
    path = tmp_path / "log.jsonl"
    append_jsonl(path, {"execidentifier": "DH-DD(2024)1"})
    assert load_processed_ids(path, id_field="execidentifier") == {"DH-DD(2024)1"}


def test_load_processed_ids_ok_only(tmp_path):
    path = tmp_path / "log.jsonl"
    append_jsonl_many(
        path,
        [{"itemid": "001-1", "status": "ok"}, {"itemid": "001-2", "status": "error"}],
    )
    assert load_processed_ids(path) == {"001-1", "001-2"}
    assert load_processed_ids(path, ok_only=True) == {"001-1"}


def test_upsert_replaces_in_place_and_appends(tmp_path):
    path = tmp_path / "log.jsonl"
    append_jsonl_many(path, [{"itemid": "a", "v": 1}, {"itemid": "b", "v": 1}])
    upsert_jsonl(path, [{"itemid": "a", "v": 2}, {"itemid": "c", "v": 1}])
    records = list(iter_jsonl(path))
    assert [r["itemid"] for r in records] == ["a", "b", "c"]
    assert records[0]["v"] == 2


def test_upsert_into_missing_file(tmp_path):
    path = tmp_path / "new.jsonl"
    upsert_jsonl(path, [{"itemid": "a"}])
    assert [r["itemid"] for r in iter_jsonl(path)] == ["a"]


def test_backup_file_creates_and_rotates(tmp_path):
    path = tmp_path / "data.jsonl"
    path.write_text("x" * 200, encoding="utf-8")

    backups = []
    for i in range(4):
        # Distinct timestamps aren't guaranteed within a second; vary content
        # and pass max_backups=2 to exercise rotation on mtime order.
        path.write_text("x" * (200 + i), encoding="utf-8")
        b = backup_file(path, max_backups=2)
        if b is not None:
            backups.append(b)

    backup_dir = tmp_path / "backups"
    remaining = list(backup_dir.glob("data_*.jsonl"))
    assert len(remaining) <= 2


def test_backup_skips_tiny_and_missing_files(tmp_path):
    tiny = tmp_path / "tiny.jsonl"
    tiny.write_text("{}", encoding="utf-8")
    assert backup_file(tiny, min_size_bytes=100) is None
    assert backup_file(tmp_path / "absent.jsonl") is None
