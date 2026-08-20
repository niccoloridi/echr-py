"""Tests for the Gemini Batch API workflow – all offline via mocked client."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from hudoc_py.llm import batch as batch_mod
from hudoc_py.llm.batch import (
    PreparedRequest,
    extract_response_payload,
    load_provenance,
    poll_batch,
    prepare_batch,
    retrieve_batch,
    retry_artifact_path,
    submit_batch,
)


def _requests():
    return [
        PreparedRequest(
            key="001-1",
            user_text="judgment text one",
            system_instruction="EXTRACT FACTS",
            response_schema={"type": "object", "properties": {"facts": {"type": "array"}}},
            provenance={
                "source_itemid": "001-1",
                "source_language": "ENG",
                "word_count": 3,
                "word_limit": 3000,
                "extractor": "facts",
            },
        ),
        PreparedRequest(
            key="001-2",
            user_text="judgment text two",
            system_instruction="EXTRACT FACTS",
            provenance={"source_itemid": "001-2f", "source_language": "FRE"},
        ),
    ]


def test_prepare_batch_writes_requests_and_provenance(tmp_path):
    req_path = tmp_path / "requests.jsonl"
    prov_path = tmp_path / "provenance.csv"
    n = prepare_batch(_requests(), requests_path=req_path, provenance_path=prov_path)
    assert n == 2

    lines = [json.loads(line) for line in req_path.read_text().splitlines()]
    assert lines[0]["key"] == "001-1"
    request = lines[0]["request"]
    assert request["contents"][0]["parts"][0]["text"] == "judgment text one"
    assert request["system_instruction"]["parts"][0]["text"] == "EXTRACT FACTS"
    assert request["generation_config"]["response_mime_type"] == "application/json"
    assert request["generation_config"]["response_schema"]["type"] == "object"
    # No schema on the second request → key absent entirely.
    assert "response_schema" not in lines[1]["request"]["generation_config"]

    prov = load_provenance(prov_path)
    assert prov["001-1"]["source_language"] == "ENG"
    assert prov["001-2"]["source_itemid"] == "001-2f"


def test_retry_artifact_path():
    assert retry_artifact_path("out/results.jsonl").name == "results.retry.jsonl"


def test_sanitise_response_schema_derefs_and_strips():
    from hudoc_py.llm.batch import sanitise_response_schema

    # Pydantic-style schema with $defs/$ref/title/default – all rejected by
    # the live Batch API ("no such field: '$ref'").
    schema = {
        "$defs": {
            "Entry": {
                "title": "Entry",
                "type": "object",
                "properties": {
                    "name": {"type": "string", "default": "", "title": "Name"},
                    "kind": {"type": "string", "enum": ["a", "b"]},
                },
                "required": ["name"],
            }
        },
        "title": "Top",
        "type": "object",
        "properties": {
            "entries": {"type": "array", "items": {"$ref": "#/$defs/Entry"}},
            "flag": {"type": "boolean", "default": False},
        },
    }
    clean = sanitise_response_schema(schema)
    assert "$defs" not in clean and "title" not in clean
    entry = clean["properties"]["entries"]["items"]
    assert entry["type"] == "object"
    assert entry["properties"]["name"] == {"type": "string"}
    assert entry["properties"]["kind"]["enum"] == ["a", "b"]
    assert clean["properties"]["flag"] == {"type": "boolean"}
    # require_all (default) forces complete output at every object level –
    # without it the batch constrained decoder emits minimal objects.
    assert sorted(clean["required"]) == ["entries", "flag"]
    assert sorted(entry["required"]) == ["kind", "name"]
    lenient = sanitise_response_schema(schema, require_all=False)
    assert "required" not in lenient
    assert lenient["properties"]["entries"]["items"]["required"] == ["name"]
    import json as json_mod

    assert "$ref" not in json_mod.dumps(clean)


def test_sanitise_response_schema_cycle_falls_back():
    from hudoc_py.llm.batch import sanitise_response_schema

    schema = {
        "$defs": {"Node": {"type": "object", "properties": {"child": {"$ref": "#/$defs/Node"}}}},
        "type": "object",
        "properties": {"root": {"$ref": "#/$defs/Node"}},
    }
    clean = sanitise_response_schema(schema)
    assert clean["properties"]["root"]["properties"]["child"] == {"type": "object"}


def test_prepare_batch_sanitises_schema(tmp_path):
    req = PreparedRequest(
        key="k",
        user_text="t",
        system_instruction="s",
        response_schema={
            "$defs": {"E": {"type": "object"}},
            "type": "object",
            "title": "X",
            "properties": {"e": {"$ref": "#/$defs/E"}},
        },
    )
    prepare_batch([req], requests_path=tmp_path / "r.jsonl", provenance_path=tmp_path / "p.csv")
    line = json.loads((tmp_path / "r.jsonl").read_text().splitlines()[0])
    embedded = line["request"]["generation_config"]["response_schema"]
    assert "$defs" not in embedded and "title" not in embedded
    assert embedded["properties"]["e"] == {"type": "object"}


def _fake_gemini(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(batch_mod, "get_gemini_client", lambda: client)
    fake_types = MagicMock()
    monkeypatch.setattr(batch_mod, "_import_genai", lambda: (MagicMock(), fake_types))
    return client


def test_submit_batch_lifecycle(tmp_path, monkeypatch):
    client = _fake_gemini(monkeypatch)
    client.files.upload.return_value = MagicMock(name="files/abc")
    client.files.upload.return_value.name = "files/abc"
    client.batches.create.return_value = MagicMock()
    client.batches.create.return_value.name = "batches/job-1"

    req_path = tmp_path / "requests.jsonl"
    prepare_batch(_requests(), requests_path=req_path, provenance_path=tmp_path / "p.csv")
    status_path = tmp_path / "status.json"
    job = submit_batch(requests_path=req_path, status_path=status_path, display_name="test-batch")
    assert job == "batches/job-1"
    status = json.loads(status_path.read_text())
    assert status["state"] == "SUBMITTED"
    assert status["uploaded_file"] == "files/abc"


def test_submit_batch_missing_file_returns_none(tmp_path, monkeypatch):
    _fake_gemini(monkeypatch)
    assert (
        submit_batch(
            requests_path=tmp_path / "absent.jsonl",
            status_path=tmp_path / "s.json",
            display_name="x",
        )
        is None
    )


def _write_status(tmp_path, **extra):
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps({"job_name": "batches/job-1", "model": "gemini-2.5-flash", **extra})
    )
    return status_path


def test_poll_batch_updates_state_and_result_file(tmp_path, monkeypatch):
    client = _fake_gemini(monkeypatch)
    job = MagicMock()
    job.state.name = "JOB_STATE_SUCCEEDED"
    job.dest.file_name = "files/results-1"
    job.error = None
    client.batches.get.return_value = job

    status_path = _write_status(tmp_path)
    status = poll_batch(status_path=status_path)
    assert status["state"] == "JOB_STATE_SUCCEEDED"
    assert status["result_file"] == "files/results-1"
    assert json.loads(status_path.read_text())["state"] == "JOB_STATE_SUCCEEDED"


def test_poll_batch_wait_until_terminal(tmp_path, monkeypatch):
    client = _fake_gemini(monkeypatch)
    running = MagicMock()
    running.state.name = "JOB_STATE_RUNNING"
    running.dest = None
    running.error = None
    done = MagicMock()
    done.state.name = "JOB_STATE_SUCCEEDED"
    done.dest = None
    done.error = None
    client.batches.get.side_effect = [running, running, done]

    waits: list[float] = []
    status = poll_batch(status_path=_write_status(tmp_path), wait=True, sleep=waits.append)
    assert status["state"] == "JOB_STATE_SUCCEEDED"
    assert len(waits) == 2


def test_extract_response_payload_variants():
    ok_line = {
        "key": "001-1",
        "response": {"candidates": [{"content": {"parts": [{"text": '{"a": 1}'}]}}]},
    }
    data, err = extract_response_payload(ok_line)
    assert data == {"a": 1} and err is None

    for bad, expect in [
        ({"key": "x", "error": "quota"}, "quota"),
        ({"key": "x"}, "No response"),
        ({"key": "x", "response": {"candidates": []}}, "Malformed"),
        (
            {"key": "x", "response": {"candidates": [{"content": {"parts": [{"text": ""}]}}]}},
            "Empty",
        ),
        (
            {"key": "x", "response": {"candidates": [{"content": {"parts": [{"text": "nope"}]}}]}},
            "Invalid JSON",
        ),
        (
            {"key": "x", "response": {"candidates": [{"content": {"parts": [{"text": "[1]"}]}}]}},
            "not an object",
        ),
    ]:
        data, err = extract_response_payload(bad)
        assert data is None and err is not None and expect in err


def test_retrieve_batch_parses_and_writes(tmp_path, monkeypatch):
    client = _fake_gemini(monkeypatch)
    result_lines = [
        {
            "key": "001-1",
            "response": {
                "candidates": [{"content": {"parts": [{"text": '{"facts": []}'}]}}],
                "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 100},
            },
        },
        {"key": "001-2", "error": "quota exceeded"},
    ]
    client.files.download.return_value = "\n".join(json.dumps(x) for x in result_lines).encode()

    prov_path = tmp_path / "p.csv"
    prepare_batch(_requests(), requests_path=tmp_path / "r.jsonl", provenance_path=prov_path)
    status_path = _write_status(
        tmp_path, state="JOB_STATE_SUCCEEDED", result_file="files/results-1"
    )
    out_path = tmp_path / "results.jsonl"

    def parse_payload(parsed, prov_row):
        data, error = extract_response_payload(parsed)
        return {
            "itemid": parsed.get("key"),
            "status": "ok" if error is None else "error",
            "source_itemid": prov_row.get("source_itemid", ""),
            "data": data or {},
        }

    n = retrieve_batch(
        status_path=status_path,
        provenance_path=prov_path,
        output_path=out_path,
        parse_payload=parse_payload,
    )
    assert n == 2
    records = [json.loads(line) for line in out_path.read_text().splitlines()]
    assert records[0]["status"] == "ok"
    assert records[0]["source_itemid"] == "001-1"
    assert records[0]["_meta"]["input_tokens"] == 1000
    assert records[0]["_meta"]["cost_usd"] > 0  # batch-discounted, non-zero
    assert records[1]["status"] == "error"
    status = json.loads(status_path.read_text())
    assert status["retrieved"] is True and status["error_count"] == 1


def test_retrieve_batch_requires_succeeded_state(tmp_path, monkeypatch):
    _fake_gemini(monkeypatch)
    status_path = _write_status(tmp_path, state="JOB_STATE_RUNNING")
    n = retrieve_batch(
        status_path=status_path,
        provenance_path=tmp_path / "p.csv",
        output_path=tmp_path / "o.jsonl",
        parse_payload=lambda a, b: {},
    )
    assert n == 0


def test_retrieve_batch_upsert_replaces(tmp_path, monkeypatch):
    client = _fake_gemini(monkeypatch)
    out_path = tmp_path / "results.jsonl"
    out_path.write_text(
        json.dumps({"itemid": "001-1", "status": "ok", "data": {"old": True}}) + "\n"
    )

    client.files.download.return_value = json.dumps(
        {
            "key": "001-1",
            "response": {"candidates": [{"content": {"parts": [{"text": '{"new": true}'}]}}]},
        }
    ).encode()
    status_path = _write_status(tmp_path, state="JOB_STATE_SUCCEEDED", result_file="f")

    def parse_payload(parsed, prov_row):
        data, error = extract_response_payload(parsed)
        return {"itemid": parsed.get("key"), "status": "ok", "data": data or {}}

    retrieve_batch(
        status_path=status_path,
        provenance_path=tmp_path / "p.csv",
        output_path=out_path,
        parse_payload=parse_payload,
        upsert=True,
    )
    records = [json.loads(line) for line in out_path.read_text().splitlines()]
    assert len(records) == 1
    assert records[0]["data"] == {"new": True}
