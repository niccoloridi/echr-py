#!/usr/bin/env python3
"""Exercise a multi-tool live MCP research workflow and render a terminal GIF."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp_demo_gif import _content_text, _font_path, _wrap_lines

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "images" / "mcp-research-demo.gif"


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


async def _call(session: Any, name: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], float]:
    started = time.perf_counter()
    result = await session.call_tool(name, arguments)
    elapsed = time.perf_counter() - started
    if result.isError:
        raise RuntimeError(f"{name}: {_content_text(result)}")
    return json.loads(_content_text(result)), elapsed


async def _live_workflow(python: str) -> tuple[list[tuple[str, str]], dict[str, Any]]:
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError as exc:
        raise RuntimeError("Install the MCP extra: pip install -e '.[mcp]'") from exc

    parameters = StdioServerParameters(
        command=python,
        args=["-m", "hudoc_py.mcp"],
        cwd=str(REPO_ROOT),
    )
    async with (
        stdio_client(parameters) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        initialized = await session.initialize()
        tools = await session.list_tools()

        search_args = {
            "ecli": "ECLI:CE:ECHR:2005:0512JUD004622199",
            "sort": "date-desc",
            "limit": 10,
        }
        search, search_seconds = await _call(session, "search_cases", search_args)
        candidates = search.get("results") or []
        selected = next(
            (
                row
                for row in candidates
                if row.get("language") == "ENG" and "46221/99" in (row.get("appno") or [])
            ),
            candidates[0] if candidates else None,
        )
        if not selected or not selected.get("itemid"):
            raise RuntimeError("The live Öcalan search returned no usable HUDOC item")
        itemid = str(selected["itemid"])

        segment_args = {
            "itemid": itemid,
            "sections": ["the_law", "separate_opinion"],
            "include_spine": True,
        }
        segments, segment_seconds = await _call(session, "get_case_segments", segment_args)

        citation_args = {
            "itemid": itemid,
            "max_refs": 200,
            "resolve": True,
            "include_occurrences": True,
            "citation_scope": "inclusive",
        }
        citations, citation_seconds = await _call(
            session,
            "get_case_citations",
            citation_args,
        )

    spine = segments.get("spine") or {}
    blocks = spine.get("blocks") or []
    opinions = segments.get("opinions") or []
    bench = segments.get("bench") or {}
    judges = bench.get("judges") or bench.get("members") or []
    occurrences = citations.get("occurrences") or []
    resolution_report = citations.get("resolution_report") or {}
    resolved_mentions = int(resolution_report.get("resolved") or 0)
    target_documents = int(resolution_report.get("target_documents") or 0)
    majority_occurrences = sum(
        1 for row in occurrences if row.get("source_component") == "majority"
    )
    opinion_occurrences = sum(1 for row in occurrences if row.get("source_component") == "opinion")
    pinpoint_occurrences = sum(1 for row in occurrences if row.get("target_paragraphs"))
    total_seconds = search_seconds + segment_seconds + citation_seconds

    lines: list[tuple[str, str]] = [
        ("command", "$ echr-py mcp"),
        ("success", f"✓ {initialized.serverInfo.name} · {len(tools.tools)} read-only tools"),
        ("plain", ""),
        (
            "question",
            "You: Analyse Öcalan's structure and citations, distinguishing the judgment from opinions.",
        ),
        ("action", "→ search_cases(ecli=exact Grand Chamber judgment)"),
        (
            "success",
            f"✓ selected {selected.get('docname')} · {itemid} ({search_seconds:.2f}s)",
        ),
        ("action", "→ get_case_segments(the_law, separate_opinion, include_spine=true)"),
        (
            "success",
            f"✓ {len(blocks):,} source blocks · {len(opinions)} opinions · {len(judges)} bench members "
            f"({segment_seconds:.2f}s)",
        ),
        ("action", "→ get_case_citations(scope=inclusive, resolve=true, occurrences=true)"),
        (
            "success",
            f"✓ {len(occurrences)} occurrences · {resolved_mentions} resolved mentions · "
            f"{target_documents} target documents "
            f"({citation_seconds:.2f}s)",
        ),
        (
            "answer",
            f"Majority: {majority_occurrences} · individual opinions: {opinion_occurrences} · "
            f"owned pinpoints: {pinpoint_occurrences}",
        ),
        ("plain", ""),
        ("success", f"✓ 3 MCP tools chained in {total_seconds:.2f}s · deterministic evidence only"),
    ]
    provenance = {
        "schema_version": "echr-py-mcp-research-demo/v1",
        "created_at": datetime.now(UTC).isoformat(),
        "live_public_hudoc_request": True,
        "package_commit": _git_commit(),
        "script_sha256": _sha256(Path(__file__)),
        "server": initialized.serverInfo.name,
        "tool_count": len(tools.tools),
        "selected_itemid": itemid,
        "selected_title": selected.get("docname"),
        "tool_calls": [
            {"name": "search_cases", "arguments": search_args, "elapsed_seconds": search_seconds},
            {
                "name": "get_case_segments",
                "arguments": segment_args,
                "elapsed_seconds": segment_seconds,
            },
            {
                "name": "get_case_citations",
                "arguments": citation_args,
                "elapsed_seconds": citation_seconds,
            },
        ],
        "results": {
            "spine_blocks": len(blocks),
            "opinions": len(opinions),
            "bench_members": len(judges),
            "citation_occurrences": len(occurrences),
            "resolved_mentions": resolved_mentions,
            "target_documents": target_documents,
            "majority_occurrences": majority_occurrences,
            "opinion_occurrences": opinion_occurrences,
            "pinpoint_occurrences": pinpoint_occurrences,
            "total_elapsed_seconds": total_seconds,
        },
    }
    return lines, provenance


def _render(
    lines: list[tuple[str, str]],
    output: Path,
    *,
    title: str = "echr-py · MCP evidence workflow",
) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError("Install Pillow to render the GIF: pip install pillow") from exc

    width, height = 1280, 760
    colors = {
        "command": "#f0f6fc",
        "action": "#79c0ff",
        "success": "#56d364",
        "question": "#d2a8ff",
        "answer": "#f0f6fc",
        "muted": "#8b949e",
        "plain": "#c9d1d9",
    }
    font_path = _font_path()
    font = ImageFont.truetype(font_path, 19)
    title_font = ImageFont.truetype(font_path, 16)
    wrapped = _wrap_lines(lines, width=103)

    def frame(visible: list[tuple[str, str]], cursor: bool = False) -> Any:
        image = Image.new("RGB", (width, height), "#0d1117")
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            (12, 12, width - 12, height - 12),
            radius=14,
            fill="#161b22",
            outline="#30363d",
            width=2,
        )
        for x, color in ((38, "#ff5f56"), (62, "#ffbd2e"), (86, "#27c93f")):
            draw.ellipse((x - 7, 24, x + 7, 38), fill=color)
        title_width = draw.textlength(title, font=title_font)
        draw.text(((width - title_width) / 2, 22), title, fill="#8b949e", font=title_font)
        draw.line((14, 52, width - 14, 52), fill="#30363d", width=1)
        y = 77
        for kind, value in visible[-21:]:
            draw.text((38, y), value, fill=colors.get(kind, colors["plain"]), font=font)
            y += 31
        if cursor:
            draw.rectangle((38, y + 4, 49, y + 25), fill="#c9d1d9")
        return image.quantize(colors=128)

    frames: list[Any] = [frame([], cursor=True)]
    durations = [650]
    visible: list[tuple[str, str]] = []
    command_kind, command = wrapped[0]
    for end in range(2, len(command) + 1, 3):
        frames.append(frame([(command_kind, command[:end])], cursor=True))
        durations.append(45)
    visible.append((command_kind, command))
    frames.append(frame(visible))
    durations.append(450)

    for kind, value in wrapped[1:]:
        visible.append((kind, value))
        frames.append(frame(visible, cursor=kind == "action"))
        durations.append(600 if kind not in {"answer", "muted"} else 760)
        if kind == "action":
            for spinner in ("[·  ] tool running", "[·· ] tool running", "[···] tool running"):
                frames.append(frame([*visible, ("muted", spinner)]))
                durations.append(280)

    frames.append(frame(visible))
    durations.append(3200)
    output.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        output,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        disposal=2,
        optimize=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Required acknowledgement that the workflow queries public HUDOC",
    )
    args = parser.parse_args()
    if not args.live:
        raise SystemExit("Pass --live to acknowledge the public HUDOC network requests")
    lines, provenance = asyncio.run(_live_workflow(args.python))
    _render(lines, args.out)
    provenance["gif_sha256"] = _sha256(args.out)
    provenance_path = args.out.with_suffix(".provenance.json")
    provenance_path.write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"MCP research GIF written to {args.out}")
    print(f"Provenance written to {provenance_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
