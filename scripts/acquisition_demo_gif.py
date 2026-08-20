#!/usr/bin/env python3
"""Run a live bounded HUDOC acquisition and render an honest terminal GIF."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp_demo_gif import _font_path, _wrap_lines

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "images" / "acquisition-terminal-demo.gif"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_commit() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.strip() or None


def _run_live(
    python: str, *, top: int, concurrency: int
) -> tuple[list[tuple[str, str]], dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="echr-py-demo-") as directory:
        artifact = Path(directory) / "cases.jsonl"
        command = [
            python,
            "-m",
            "hudoc_py.cli",
            "smart-fetch",
            "--article",
            "3",
            "--respondent",
            "ITA",
            "--top",
            str(top),
            "--page-size",
            "100",
            "--sort",
            "date-desc",
            "--rich-sections",
            "--concurrency",
            str(concurrency),
            "--out",
            str(artifact),
        ]
        started = time.perf_counter()
        result = subprocess.run(
            command,
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        elapsed = time.perf_counter() - started
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())

        rows = [json.loads(line) for line in artifact.read_text(encoding="utf-8").splitlines()]
        text_rows = [row for row in rows if row.get("text")]
        text_characters = sum(len(str(row.get("text") or "")) for row in rows)
        paragraphs = sum(
            len(((row.get("sections") or {}).get("spine") or {}).get("blocks") or [])
            for row in rows
        )
        artifact_bytes = artifact.stat().st_size
        artifact_hash = _sha256(artifact)

    displayed_command = (
        "echr-py smart-fetch --article 3 --respondent ITA "
        f"--top {top} --page-size 100 --rich-sections "
        f"--concurrency {concurrency} --out cases.jsonl"
    )
    lines: list[tuple[str, str]] = [
        ("command", f"$ {displayed_command}"),
        ("action", "→ querying HUDOC metadata and hydrating official texts"),
        ("muted", f"  bounded concurrency: {concurrency} · deterministic top-N: {top}"),
        (
            "success",
            f"✓ {len(rows)} metadata records · {len(text_rows)} texts · {paragraphs:,} spine blocks",
        ),
        (
            "success",
            f"✓ {text_characters:,} text characters · {artifact_bytes / 1024:.1f} KiB JSONL",
        ),
        ("success", f"✓ completed in {elapsed:.2f}s ({len(rows) / elapsed:.2f} cases/s)"),
        ("plain", ""),
        ("answer", "Recent Article 3 records against Italy:"),
    ]
    for index, row in enumerate(rows[:4], 1):
        appnos = ", ".join(str(value) for value in row.get("appno") or [])
        lines.append(("answer", f"{index}. {row.get('docname') or 'Untitled case'}"))
        lines.append(
            (
                "muted",
                f"   {appnos or 'no appno'} · {row.get('kp_date') or 'date unavailable'} "
                f"· {row.get('itemid') or ''}",
            )
        )
    lines.extend(
        [
            ("plain", ""),
            ("success", "✓ metadata, text and rich legal structure saved locally"),
        ]
    )
    provenance = {
        "schema_version": "echr-py-terminal-demo/v1",
        "created_at": datetime.now(UTC).isoformat(),
        "live_public_hudoc_request": True,
        "command": displayed_command,
        "package_commit": _git_commit(),
        "script_sha256": _sha256(Path(__file__)),
        "query": {"article": "3", "respondent": "ITA", "sort": "date-desc"},
        "settings": {
            "top": top,
            "page_size": 100,
            "concurrency": concurrency,
            "rich_sections": True,
        },
        "results": {
            "case_count": len(rows),
            "text_count": len(text_rows),
            "spine_block_count": paragraphs,
            "text_characters": text_characters,
            "artifact_bytes": artifact_bytes,
            "elapsed_seconds": round(elapsed, 6),
            "cases_per_second": round(len(rows) / elapsed, 6),
            "itemids": [row.get("itemid") for row in rows],
        },
        "temporary_artifact_sha256": artifact_hash,
    }
    return lines, provenance


def _render(lines: list[tuple[str, str]], output: Path) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError("Install Pillow to render the GIF: pip install pillow") from exc

    width, height = 1200, 720
    colors = {
        "command": "#f0f6fc",
        "action": "#79c0ff",
        "success": "#56d364",
        "answer": "#f0f6fc",
        "muted": "#8b949e",
        "plain": "#c9d1d9",
    }
    font_path = _font_path()
    font = ImageFont.truetype(font_path, 19)
    title_font = ImageFont.truetype(font_path, 16)
    wrapped = _wrap_lines(lines, width=96)

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
        title = "echr-py · live case acquisition"
        title_width = draw.textlength(title, font=title_font)
        draw.text(((width - title_width) / 2, 22), title, fill="#8b949e", font=title_font)
        draw.line((14, 52, width - 14, 52), fill="#30363d", width=1)
        y = 76
        for kind, value in visible[-20:]:
            draw.text((38, y), value, fill=colors.get(kind, colors["plain"]), font=font)
            y += 30
        if cursor:
            draw.rectangle((38, y + 4, 49, y + 25), fill="#c9d1d9")
        return image.quantize(colors=128)

    frames: list[Any] = [frame([], cursor=True)]
    durations = [650]
    visible: list[tuple[str, str]] = []
    command_kind, command = wrapped[0]
    for end in range(2, len(command) + 1, 4):
        frames.append(frame([(command_kind, command[:end])], cursor=True))
        durations.append(35)
    visible.append((command_kind, command))
    frames.append(frame(visible))
    durations.append(450)

    for kind, value in wrapped[1:]:
        if value.startswith("→ querying HUDOC"):
            visible.append((kind, value))
            frames.append(frame(visible))
            durations.append(450)
            for spinner in ("[·  ] downloading", "[·· ] downloading", "[···] downloading"):
                frames.append(frame([*visible, ("muted", spinner)]))
                durations.append(320)
            continue
        visible.append((kind, value))
        frames.append(frame(visible, cursor=kind in {"success", "action"}))
        durations.append(540 if kind not in {"answer", "muted"} else 620)

    frames.append(frame(visible))
    durations.append(3000)
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
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--concurrency", type=int, default=12)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Required acknowledgement that the script will query public HUDOC",
    )
    args = parser.parse_args()
    if not args.live:
        raise SystemExit("Pass --live to acknowledge the public HUDOC network request")
    if args.top < 1 or args.concurrency < 1:
        raise SystemExit("--top and --concurrency must be at least 1")

    lines, provenance = _run_live(
        args.python,
        top=args.top,
        concurrency=args.concurrency,
    )
    _render(lines, args.out)
    provenance["gif_sha256"] = _sha256(args.out)
    provenance_path = args.out.with_suffix(".provenance.json")
    provenance_path.write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Acquisition demo GIF written to {args.out}")
    print(f"Provenance written to {provenance_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
