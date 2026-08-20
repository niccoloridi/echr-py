"""Offline MCP registration, metadata, and response-contract tests."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("mcp", reason="mcp extra not installed")


EXPECTED_TOOLS = {
    "search_cases",
    "count_cases",
    "search_and_read",
    "get_case_metadata",
    "get_case_text",
    "search_exec",
    "search_exec_documents",
    "get_exec_document",
    "list_articles_referenced",
    "get_case_segments",
    "get_case_citations",
    "get_case_citation_network",
    "search_keypoints",
}

JOB_TOOLS = {
    "create_study_job",
    "get_study_job",
    "list_study_jobs",
    "cancel_study_job",
    "resume_study_job",
    "list_study_artifacts",
}


@pytest.mark.asyncio
async def test_build_server_registers_expected_tools():
    from hudoc_py.mcp import build_server

    server = build_server()
    tools = await server.list_tools()
    names = {t.name for t in tools}
    assert EXPECTED_TOOLS.issubset(names), f"missing: {EXPECTED_TOOLS - names}"
    citations = next(t for t in tools if t.name == "get_case_citations")
    assert "include_occurrences" in citations.inputSchema["properties"]
    assert "include_target_paragraphs" in citations.inputSchema["properties"]
    assert "citation_scope" in citations.inputSchema["properties"]
    segments = next(t for t in tools if t.name == "get_case_segments")
    assert "include_text" in segments.inputSchema["properties"]
    network = next(t for t in tools if t.name == "get_case_citation_network")
    assert "top_targets" in network.inputSchema["properties"]
    for tool in tools:
        assert tool.annotations is not None
        assert tool.annotations.title
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False
    assert not (JOB_TOOLS & names)


def test_compact_citation_network_preserves_component_provenance():
    from hudoc_py.mcp.server import _citation_network_response

    response = {
        "found": True,
        "itemid": "001-source",
        "appno": ["1/01"],
        "resolution_report": {"mentions": 2, "resolved": 2, "target_documents": 1},
        "citations": [
            {
                "target": {
                    "node_id": "target-node",
                    "itemid": "001-target",
                    "ecli": "ECLI:TARGET",
                    "docname": "Target v. State",
                    "appnos": ["2/02"],
                    "document_kind": "judgment",
                    "procedural_phase": "merits",
                }
            }
        ],
        "occurrences": [
            {
                "occurrence_id": "occ-majority",
                "source_component": "majority",
                "source_section": "the_law",
                "target_node_id": "target-node",
                "target_itemid": "001-target",
                "target_paragraphs": ["§ 10"],
                "resolution_scope": "document",
                "scl_coverage": "covered",
            },
            {
                "occurrence_id": "occ-opinion",
                "source_component": "opinion",
                "source_opinion_id": "opinion-1",
                "source_opinion_type": "dissenting",
                "source_opinion_authors": ["Judge Example"],
                "source_section": "separate_opinion",
                "target_node_id": "target-node",
                "target_itemid": "001-target",
                "target_paragraphs": [],
                "resolution_scope": "document",
                "scl_coverage": "not_covered",
            },
        ],
    }

    result = _citation_network_response(response, source_title="Source v. State", top_targets=10)

    assert result["summary"] == {
        "mentions": 2,
        "resolved_mentions": 2,
        "target_documents": 1,
        "occurrences": 2,
        "majority_occurrences": 1,
        "opinion_occurrences": 1,
        "appendix_occurrences": 0,
        "pinpoint_occurrences": 1,
        "text_only_occurrences": 1,
        "unresolved_occurrences": 0,
        "included_target_nodes": 1,
        "included_aggregate_edges": 2,
    }
    graph = result["graph"]
    assert graph["schema_version"] == "hudoc-graph/v1"
    assert graph["meta"]["edge_count"] == 2
    assert {node["attributes"].get("component") for node in graph["nodes"]} >= {
        "majority",
        "opinion",
    }


@pytest.mark.asyncio
async def test_job_tools_are_registered_only_with_opt_in_manager():
    from hudoc_py.mcp import build_server

    class DummyJobs:
        pass

    tools = await build_server(job_manager=DummyJobs()).list_tools()
    by_name = {tool.name: tool for tool in tools}
    assert set(by_name) >= JOB_TOOLS
    for name in {"create_study_job", "cancel_study_job", "resume_study_job"}:
        assert by_name[name].annotations.readOnlyHint is False
    assert by_name["cancel_study_job"].annotations.destructiveHint is True
    for name in {"get_study_job", "list_study_jobs", "list_study_artifacts"}:
        assert by_name[name].annotations.readOnlyHint is True


@pytest.mark.asyncio
async def test_read_only_local_search_does_not_create_missing_database(tmp_path):
    from hudoc_py.mcp import build_server

    missing = tmp_path / "does-not-exist.sqlite"
    tool = build_server()._tool_manager.get_tool("search_local_paragraphs")

    with pytest.raises(FileNotFoundError, match="does not exist"):
        await tool.fn(database=str(missing), query="Article 3")

    assert not missing.exists()


@pytest.mark.asyncio
async def test_occurrence_identity_matches_python_api(monkeypatch):
    from hudoc_py.citations import extract_citation_occurrences
    from hudoc_py.mcp import build_server
    from hudoc_py.models import Case

    case = Case(
        itemid="001-source",
        scl="Soering v. the United Kingdom, 7 July 1989, § 88",
        text="<p>1. See Soering v. the United Kingdom, 7 July 1989, § 88.</p>",
    )

    async def fake_fetch_case(**_kwargs):
        return case

    monkeypatch.setattr("hudoc_py.mcp.server.main_aio.fetch_case", fake_fetch_case)
    expected = extract_citation_occurrences(case, html=case.text).occurrences[0].occurrence_id

    result = await build_server().call_tool(
        "get_case_citations",
        {"itemid": case.itemid, "include_occurrences": True},
    )
    payload = json.loads(result[0][0].text)

    assert payload["occurrences"][0]["occurrence_id"] == expected


@pytest.mark.asyncio
async def test_mcp_occurrence_identity_matches_python_api(monkeypatch):
    from hudoc_py.citations import extract_citation_occurrences
    from hudoc_py.mcp import build_server
    from hudoc_py.models import Case

    html = "<p>1. See Soering v. the United Kingdom, 7 July 1989, § 88.</p>"
    case = Case(
        itemid="001-source",
        languageisocode="ENG",
        scl="Soering v. the United Kingdom, 7 July 1989, § 88",
        text=html,
    )

    async def fake_fetch_case(**kwargs):
        return case

    monkeypatch.setattr("hudoc_py.mcp.server.main_aio.fetch_case", fake_fetch_case)
    server = build_server()
    tool = server._tool_manager.get_tool("get_case_citations")
    response = await tool.fn(itemid=case.itemid, include_occurrences=True)
    direct = extract_citation_occurrences(case, html=html)

    assert response["occurrences"][0]["occurrence_id"] == direct.occurrences[0].occurrence_id


def test_module_entry_point_importable():
    import importlib

    mod = importlib.import_module("hudoc_py.mcp.__main__")
    # The module imports ``run``; we just confirm the symbol exists.
    assert hasattr(mod, "run")
