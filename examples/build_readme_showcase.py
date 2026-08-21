"""Generate authentic README diagrams and their provenance sidecars.

The figures are either contract diagrams or are derived from the pinned Öcalan
paragraph-edge fixture. They make no corpus-scale claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

matplotlib.rcParams["svg.hashsalt"] = "echr-py-figure-v1"

INK = "#09284c"
BLUE = "#2a78d6"
GOLD = "#f4b41a"
GREEN = "#1baf7a"
PURPLE = "#6554c0"
SURFACE = "#fbfcfe"
MUTED = "#5b6573"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or "uncommitted"


def _card(axis: Any, x: float, y: float, width: float, height: float, title: str, body: str, colour: str) -> None:
    axis.add_patch(
        FancyBboxPatch(
            (x, y),
            width,
            height,
            boxstyle="round,pad=0.02,rounding_size=0.025",
            facecolor="white",
            edgecolor=colour,
            linewidth=2.0,
        )
    )
    axis.text(x + width / 2, y + height * 0.66, title, ha="center", va="center", color=INK, fontsize=11, fontweight="bold")
    axis.text(x + width / 2, y + height * 0.34, body, ha="center", va="center", color=MUTED, fontsize=8.5, linespacing=1.35)


def _save(fig: Any, output: Path, stem: str, provenance: dict[str, Any]) -> list[Path]:
    generated = []
    for suffix in ("png", "svg"):
        path = output / f"{stem}.{suffix}"
        metadata = {"Date": None} if suffix == "svg" else None
        fig.savefig(
            path,
            dpi=180,
            facecolor=SURFACE,
            bbox_inches="tight",
            metadata=metadata,
        )
        if suffix == "svg":
            lines = path.read_text(encoding="utf-8").splitlines()
            path.write_text(
                "\n".join(line.rstrip() for line in lines) + "\n",
                encoding="utf-8",
            )
        generated.append(path)
    plt.close(fig)
    provenance.update(
        {
            "schema_version": "echr-py-figure/v1",
            "generator": Path(__file__).name,
            "generator_sha256": _sha256(Path(__file__)),
            "repository_commit": _commit(),
            "outputs": [
                {"file": path.name, "sha256": _sha256(path), "bytes": path.stat().st_size}
                for path in generated
            ],
        }
    )
    (output / f"{stem}-provenance.json").write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return generated


def _load_edges(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def ocalan_ledger(output: Path, source: Path) -> list[Path]:
    rows = _load_edges(source)
    fig, axis = plt.subplots(figsize=(13.4, 4.8))
    fig.patch.set_facecolor(SURFACE)
    axis.axis("off")
    axis.text(
        0.01,
        0.95,
        "Öcalan: two authorities, two independently owned target pinpoints",
        color=INK,
        fontsize=17,
        fontweight="bold",
        transform=axis.transAxes,
    )
    axis.text(
        0.01,
        0.88,
        "English and French retain distinct source addresses while resolving the same canonical documents.",
        color=MUTED,
        fontsize=10,
        transform=axis.transAxes,
    )
    columns = ["Source record", "Source address", "Printed span", "Exact target", "Pinpoint", "Mapping"]
    cells = [
        [
            f"{row['source_itemid']} · {row['source_language']}",
            str(row["source_para_id"]),
            row["raw_text"],
            f"{row['target_docname']}\n{row['target_itemid']}",
            f"§ {row['target_para_num']}",
            row["mapping_status"],
        ]
        for row in rows
    ]
    table = axis.table(
        cellText=cells,
        colLabels=columns,
        cellLoc="left",
        colLoc="left",
        colWidths=[0.16, 0.13, 0.20, 0.27, 0.10, 0.11],
        bbox=[0.01, 0.12, 0.98, 0.66],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9.5)
    for (row_index, _), cell in table.get_celld().items():
        cell.set_edgecolor("#d9e0e8")
        cell.set_linewidth(0.8)
        cell.set_facecolor(INK if row_index == 0 else ("#eef5ff" if row_index % 2 else "white"))
        cell.get_text().set_color("white" if row_index == 0 else INK)
        if row_index == 0:
            cell.get_text().set_fontweight("bold")
    axis.text(
        0.01,
        0.04,
        "Pinned public HUDOC regression: item IDs 001-69022 (ENG) and 001-69023 (FRE).",
        color=MUTED,
        fontsize=9,
        transform=axis.transAxes,
    )
    return _save(
        fig,
        output,
        "ocalan-citation-ledger",
        {
            "kind": "pinned-real-citation-ledger",
            "source": str(source),
            "source_sha256": _sha256(source),
            "source_documents": sorted({row["source_itemid"] for row in rows}),
            "paragraph_edges": len(rows),
        },
    )


def citation_layers(output: Path) -> list[Path]:
    fig, axis = plt.subplots(figsize=(12.8, 4.6))
    fig.patch.set_facecolor(SURFACE)
    axis.set_xlim(0, 1)
    axis.set_ylim(0, 1)
    axis.axis("off")
    axis.text(0.02, 0.92, "Three citation products – three deliberately different semantics", color=INK, fontsize=17, fontweight="bold")
    cards = [
        (0.03, "SCL decision graph", "HUDOC's cited-case list\nDecision → decision\nCompatibility counts unchanged", BLUE),
        (0.355, "Inclusive occurrence ledger", "Exact source span and context\nMajority/opinion/footnote identity\nText-only discoveries are additive", GOLD),
        (0.68, "Paragraph multigraph", "Source paragraph → cited paragraph\nOnly exact document targets\nOnly independently owned pinpoints", GREEN),
    ]
    for x, title, body, colour in cards:
        _card(axis, x, 0.28, 0.285, 0.42, title, body, colour)
    axis.add_patch(FancyArrowPatch((0.315, 0.49), (0.355, 0.49), arrowstyle="-|>", mutation_scale=14, color="#9aa5b1"))
    axis.add_patch(FancyArrowPatch((0.64, 0.49), (0.68, 0.49), arrowstyle="-|>", mutation_scale=14, color="#9aa5b1"))
    axis.text(0.03, 0.12, "No inclusive discovery or model label is allowed to rewrite the authoritative SCL baseline.", color=MUTED, fontsize=10)
    return _save(fig, output, "citation-layers", {"kind": "contract-diagram", "claim_scope": "artifact semantics"})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("docs/images"))
    parser.add_argument(
        "--ocalan-edges",
        type=Path,
        default=Path(__file__).with_name("data") / "ocalan-paragraph-edges.ndjson",
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    ocalan_ledger(args.out, args.ocalan_edges)
    citation_layers(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
