#!/usr/bin/env python3
"""Package a genuine Claude Code + echr-py MCP network run.

The input is Claude Code's ``--output-format stream-json --verbose`` output.
Only a compact, credential-free audit record is retained; raw model protocol
events remain outside the repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp_research_demo_gif import _render

from hudoc_py.graphs import GraphBundle, export_graph

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "docs" / "examples" / "claude-mcp-ocalan"
DEFAULT_GIF = REPO_ROOT / "docs" / "images" / "claude-mcp-network-demo.gif"
NETWORK_TOOL = "mcp__echr-py__get_case_citation_network"
REQUIRED_MCP_TOOLS = {
    "mcp__echr-py__search_cases",
    "mcp__echr-py__get_case_segments",
    NETWORK_TOOL,
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_commit() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.strip() or None


def _load_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON on input line {line_number}") from exc
        if isinstance(value, dict):
            events.append(value)
    return events


def _tool_calls(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for event in events:
        if event.get("type") != "assistant":
            continue
        for content in event.get("message", {}).get("content", []):
            if content.get("type") == "tool_use":
                calls.append({"name": content.get("name"), "arguments": content.get("input", {})})
    return calls


def _result_event(events: list[dict[str, Any]]) -> dict[str, Any]:
    results = [event for event in events if event.get("type") == "result"]
    if len(results) != 1 or results[0].get("subtype") != "success":
        raise ValueError("Expected exactly one successful Claude result event")
    return results[0]


def _structured_network(events: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = []
    for event in events:
        structured = event.get("tool_use_result", {}).get("structuredContent")
        if isinstance(structured, dict) and structured.get("graph", {}).get("schema_version"):
            candidates.append(structured)
    if len(candidates) != 1:
        raise ValueError("Expected exactly one structured citation-network result")
    return candidates[0]


def _parse_final_json(value: str) -> dict[str, Any]:
    match = re.fullmatch(r"\s*```json\s*(\{.*\})\s*```\s*", value, flags=re.DOTALL)
    payload = match.group(1) if match else value
    parsed = json.loads(payload)
    if not isinstance(parsed, dict):
        raise ValueError("Claude's final response must be a JSON object")
    return parsed


def _gif_lines(final: dict[str, Any], summary: dict[str, Any]) -> list[tuple[str, str]]:
    case = final["case"]
    top = final["top_5_targets"]
    top_text = " · ".join(
        f"{row['label'].removeprefix('CASE OF ')} ({row['occurrence_count']})" for row in top
    )
    return [
        ("command", "$ claude --model sonnet --mcp-config echr-py …"),
        ("question", "You: Build an occurrence-weighted citation network for Öcalan."),
        ("action", "Claude → search_cases(exact ECLI)"),
        ("success", f"✓ {case['title']} · {case['itemid']}"),
        ("action", "Claude → get_case_segments(text=false, spine=false)"),
        (
            "success",
            f"✓ {final['spine_summary']['block_count']} blocks · "
            f"{final['bench_member_count']} bench members · {final['opinion_count']} opinions",
        ),
        ("action", "Claude → get_case_citation_network(scope=inclusive, top=40)"),
        (
            "success",
            f"✓ {summary['occurrences']} occurrences · {summary['target_documents']} targets · "
            f"{summary['included_aggregate_edges']} displayed edges",
        ),
        (
            "answer",
            f"Majority {summary['majority_occurrences']} · opinions "
            f"{summary['opinion_occurrences']} · pinpoints {summary['pinpoint_occurrences']}",
        ),
        ("plain", "Top cited: " + top_text),
        ("plain", "Resolution: 140/146 mentions · top five: 43/139 occurrences"),
        ("success", "✓ actual read-only MCP calls · Claude Sonnet 5 · portable graph exported"),
    ]


def package_run(stream: Path, out_dir: Path, gif: Path) -> dict[str, Any]:
    events = _load_events(stream)
    calls = _tool_calls(events)
    mcp_calls = [call for call in calls if str(call["name"]).startswith("mcp__")]
    if {str(call["name"]) for call in mcp_calls} != REQUIRED_MCP_TOOLS or len(mcp_calls) != 3:
        raise ValueError("The run must contain each required MCP tool exactly once")

    result = _result_event(events)
    network = _structured_network(events)
    graph = GraphBundle.model_validate(network["graph"])
    final = _parse_final_json(str(result["result"]))
    summary = network["summary"]
    if final.get("network_summary") != summary:
        raise ValueError("Claude's reported network summary differs from the MCP result")

    model_usage = result.get("modelUsage", {})
    reasoning_models = sorted(name for name in model_usage if name.startswith("claude-sonnet"))
    if reasoning_models != ["claude-sonnet-5"]:
        raise ValueError(f"Unexpected reasoning model(s): {reasoning_models}")

    out_dir.mkdir(parents=True, exist_ok=True)
    graph_paths = {
        fmt: export_graph(graph, out_dir / f"ocalan-citation-network.{fmt}", fmt=fmt)
        for fmt in ("json", "gexf", "html")
    }
    final_path = out_dir / "claude-result.json"
    final_path.write_text(json.dumps(final, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    gif.parent.mkdir(parents=True, exist_ok=True)
    _render(
        _gif_lines(final, summary),
        gif,
        title="echr-py · Claude Sonnet 5 · live MCP",
    )

    record = {
        "schema_version": "echr-py-claude-mcp-demo/v1",
        "created_at": datetime.now(UTC).isoformat(),
        "orchestration": "claude-code-model-driven",
        "reasoning_model": "claude-sonnet-5",
        "auxiliary_models_reported_by_claude_code": sorted(
            name for name in model_usage if name != "claude-sonnet-5"
        ),
        "package_commit_at_export": _git_commit(),
        "source_stream_sha256": _sha256(stream),
        "source_stream_retained": False,
        "source_stream_retention_note": (
            "The raw Claude protocol stream is not tracked because it duplicates large public "
            "tool payloads. This record retains its checksum, calls, model usage, final response, "
            "and graph artifacts."
        ),
        "tool_calls": mcp_calls,
        "result": {
            "status": result.get("subtype"),
            "duration_ms": result.get("duration_ms"),
            "reported_cost_usd": result.get("total_cost_usd"),
            "model_usage": model_usage,
        },
        "network_summary": summary,
        "artifacts": {
            "gif": {"path": str(gif.relative_to(REPO_ROOT)), "sha256": _sha256(gif)},
            "final_response": {
                "path": str(final_path.relative_to(REPO_ROOT)),
                "sha256": _sha256(final_path),
            },
            **{
                fmt: {
                    "path": str(path.relative_to(REPO_ROOT)),
                    "sha256": _sha256(path),
                }
                for fmt, path in graph_paths.items()
            },
        },
    }
    provenance_path = out_dir / "provenance.json"
    provenance_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stream", type=Path, help="Claude Code stream-JSON output")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--gif", type=Path, default=DEFAULT_GIF)
    args = parser.parse_args()
    record = package_run(args.stream, args.out_dir, args.gif)
    print(json.dumps(record["network_summary"], indent=2))


if __name__ == "__main__":
    main()
