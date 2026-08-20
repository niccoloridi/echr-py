#!/usr/bin/env python3
"""Run a live echr-py MCP call and render a terminal-style GIF.

The recording contains only the public query, MCP lifecycle messages, tool
count, and compact public HUDOC result metadata. Protocol payloads and local
environment values are deliberately omitted.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import textwrap
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "images" / "mcp-terminal-demo.gif"


def _font_path() -> str:
    candidates = (
        "/System/Library/Fonts/SFNSMono.ttf",
        "/System/Library/Fonts/Supplemental/Menlo.ttc",
        "/Library/Fonts/Menlo.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/dejavu/DejaVuSansMono-Bold.ttf",
    )
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    raise RuntimeError("No supported monospaced font found")


def _content_text(result: Any) -> str:
    for item in result.content:
        value = getattr(item, "text", None)
        if value:
            return str(value)
    raise RuntimeError("MCP tool returned no text content")


def _unique_cases(payload: dict[str, Any], limit: int = 3) -> list[dict[str, Any]]:
    """Deduplicate bilingual result rows, preferring English metadata."""
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in payload.get("results", []):
        key = str(row.get("ecli") or (row.get("appno") or [row.get("itemid")])[0])
        if key not in grouped:
            grouped[key] = row
            order.append(key)
        elif str(row.get("doctype", "")).startswith("HE"):
            grouped[key] = row
    return [grouped[key] for key in order[:limit]]


async def _live_transcript(python: str) -> list[tuple[str, str]]:
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
        result = await session.call_tool(
            "search_cases",
            {
                "article": "3",
                "respondent": "ITA",
                "sort": "date-desc",
                "limit": 10,
            },
        )
        if result.isError:
            raise RuntimeError(_content_text(result))

    payload = json.loads(_content_text(result))
    cases = _unique_cases(payload)
    if not cases:
        raise RuntimeError("HUDOC returned no cases for the demo query")

    protocol = getattr(initialized, "protocolVersion", "MCP")
    lines: list[tuple[str, str]] = [
        ("command", "$ python scripts/mcp_demo_gif.py --live"),
        ("action", "→ launching echr-py MCP server over stdio"),
        ("success", f"✓ initialized {initialized.serverInfo.name} · protocol {protocol}"),
        ("action", "→ tools/list"),
        ("success", f"✓ {len(tools.tools)} read-only tools discovered"),
        ("plain", ""),
        ("question", "You: Find the 3 most recent Article 3 cases against Italy."),
        ("action", "→ tools/call search_cases"),
        (
            "success",
            f"✓ HUDOC returned {payload.get('count', 0)} rows "
            f"({payload.get('total_matches', 0):,} total matches)",
        ),
        ("plain", ""),
        ("answer", "Answer:"),
    ]
    for index, case in enumerate(cases, 1):
        appnos = ", ".join(str(value) for value in case.get("appno", []))
        lines.extend(
            [
                ("answer", f"{index}. {case.get('docname') or 'Untitled case'}"),
                (
                    "muted",
                    f"   {appnos} · {case.get('kp_date') or 'date unavailable'} "
                    f"· {case.get('itemid') or ''}",
                ),
            ]
        )
    lines.extend(
        [
            ("plain", ""),
            ("success", "✓ answer provided from a live MCP tool call"),
        ]
    )
    return lines


def _wrap_lines(lines: list[tuple[str, str]], width: int = 92) -> list[tuple[str, str]]:
    wrapped: list[tuple[str, str]] = []
    for kind, value in lines:
        parts = textwrap.wrap(
            value,
            width=width,
            subsequent_indent="   " if kind in {"answer", "muted"} else "",
            replace_whitespace=False,
        ) or [""]
        wrapped.extend((kind, part) for part in parts)
    return wrapped


def _render(lines: list[tuple[str, str]], output: Path) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError("Install Pillow to render the GIF: pip install pillow") from exc

    width, height = 1200, 720
    background = "#0d1117"
    panel = "#161b22"
    border = "#30363d"
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
    font = ImageFont.truetype(font_path, 20)
    title_font = ImageFont.truetype(font_path, 16)
    line_height = 30
    wrapped = _wrap_lines(lines)

    def frame(visible: list[tuple[str, str]], cursor: bool = False) -> Any:
        image = Image.new("RGB", (width, height), background)
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            (12, 12, width - 12, height - 12), radius=14, fill=panel, outline=border, width=2
        )
        for x, color in ((38, "#ff5f56"), (62, "#ffbd2e"), (86, "#27c93f")):
            draw.ellipse((x - 7, 31 - 7, x + 7, 31 + 7), fill=color)
        title = "echr-py · MCP live demo"
        title_width = draw.textlength(title, font=title_font)
        draw.text(((width - title_width) / 2, 22), title, fill="#8b949e", font=title_font)
        draw.line((14, 52, width - 14, 52), fill=border, width=1)

        y = 78
        for kind, value in visible[-19:]:
            draw.text((38, y), value, fill=colors.get(kind, colors["plain"]), font=font)
            y += line_height
        if cursor:
            draw.rectangle((38, y + 4, 49, y + 25), fill="#c9d1d9")
        return image.quantize(colors=128)

    frames: list[Any] = [frame([], cursor=True)]
    durations: list[int] = [700]
    visible: list[tuple[str, str]] = []

    command_kind, command = wrapped[0]
    for end in range(2, len(command) + 1, 3):
        frames.append(frame([(command_kind, command[:end])], cursor=True))
        durations.append(45)
    visible.append((command_kind, command))
    frames.append(frame(visible, cursor=False))
    durations.append(500)

    for kind, value in wrapped[1:]:
        if value == "→ tools/call search_cases":
            visible.append((kind, value))
            frames.append(frame(visible, cursor=False))
            durations.append(500)
            for spinner in ("[·  ] contacting HUDOC", "[·· ] contacting HUDOC", "[···] contacting HUDOC"):
                frames.append(frame([*visible, ("muted", spinner)], cursor=False))
                durations.append(350)
            continue
        visible.append((kind, value))
        frames.append(frame(visible, cursor=kind not in {"plain", "muted"}))
        durations.append(520 if kind not in {"answer", "muted"} else 620)

    frames.append(frame(visible, cursor=False))
    durations.append(2800)
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--python", default=sys.executable, help="Python used to launch the server")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Required acknowledgement that the script will query public HUDOC",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.live:
        raise SystemExit("Pass --live to acknowledge the public HUDOC network request")
    lines = asyncio.run(_live_transcript(args.python))
    _render(lines, args.out)
    print(f"MCP demo GIF written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
