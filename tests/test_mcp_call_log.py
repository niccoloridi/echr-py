"""The MCP call log records tool name, arguments, identifiers and version for replay."""

from __future__ import annotations

import json

import pytest

from hudoc_py import __version__
from hudoc_py.mcp.calllog import CallLogger, collect_identifiers, read_call_log

pytest.importorskip("mcp", reason="mcp extra not installed")


def test_collect_identifiers_walks_nested_payloads():
    payload = {
        "case": {"itemid": "001-1", "ecli": "ECLI:A"},
        "edges": [{"source_itemid": "001-1", "target_ecli": "ECLI:B"}, {"target_itemid": "001-2"}],
        "noise": {"itemid": ""},
    }
    assert collect_identifiers(payload) == {
        "itemids": ["001-1", "001-2"],
        "eclis": ["ECLI:A", "ECLI:B"],
    }


@pytest.mark.asyncio
async def test_server_call_log_records_each_tool_call(monkeypatch, tmp_path):
    from hudoc_py.mcp import build_server
    from hudoc_py.models import Case

    case = Case(
        itemid="001-source",
        ecli="ECLI:CE:ECHR:2005:0512JUD004622199",
        scl="Soering v. the United Kingdom, 7 July 1989, § 88",
        text="<p>1. See Soering v. the United Kingdom, 7 July 1989, § 88.</p>",
    )

    async def fake_fetch_case(**_kwargs):
        return case

    monkeypatch.setattr("hudoc_py.mcp.server.main_aio.fetch_case", fake_fetch_case)
    log_path = tmp_path / "calls.jsonl"
    server = build_server(call_log=log_path)

    tools = {t.name for t in await server.list_tools()}
    assert "get_case_citations" in tools
    schema = next(
        t for t in await server.list_tools() if t.name == "get_case_citations"
    ).inputSchema
    assert "include_occurrences" in schema["properties"], (
        "wrapping must preserve the tool signature"
    )

    result = await server.call_tool(
        "get_case_citations", {"itemid": case.itemid, "include_occurrences": True}
    )
    payload = json.loads(result[0][0].text)
    assert payload["occurrences"], "the tool still returns its normal payload through the wrapper"

    entries = list(read_call_log(log_path))
    assert len(entries) == 1
    entry = entries[0]
    assert entry["tool"] == "get_case_citations"
    assert entry["arguments"]["itemid"] == case.itemid
    assert entry["arguments"]["include_occurrences"] is True
    assert entry["status"] == "ok"
    assert entry["package_version"] == __version__
    assert case.itemid in entry["identifiers"]["itemids"] or case.itemid in entry["arguments"].values()
    assert isinstance(entry["identifiers"]["itemids"], list)
    assert entry["duration_ms"] >= 0
    assert entry["recorded_at"].endswith("+00:00")


@pytest.mark.asyncio
async def test_call_log_records_errors_and_reraises(tmp_path):
    log_path = tmp_path / "calls.jsonl"
    logger = CallLogger(log_path)

    async def failing(itemid: str) -> dict:
        raise ValueError(f"no such case {itemid}")

    wrapped = logger.wrap(failing)
    with pytest.raises(ValueError):
        await wrapped(itemid="001-x")
    (entry,) = read_call_log(log_path)
    assert entry["status"] == "error"
    assert entry["error"].startswith("ValueError: no such case")
    assert entry["arguments"] == {"itemid": "001-x"}
