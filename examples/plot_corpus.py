"""Plot a complete, authoritative citation-resolution artifact directory.

The input must come from ``echr-py citations resolve``. The script refuses an
incomplete resolution report and writes two PNGs:

    citation-network.png – the graph, node size = PageRank, colour = community
    most-cited-cases.png – the leading judgments by in-corpus in-degree

Needs the analysis extra plus matplotlib::

    pip install -e ".[analysis]" matplotlib
    python examples/plot_corpus.py --citations corpus/citations/

The built-in interactive viewer (no matplotlib needed) is a one-liner::

    graph.to_html("network.html", max_nodes=500)   # open in a browser
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx

from hudoc_py.citations import CitationGraph
from hudoc_py.citations.metrics import compute_metrics

# Validated categorical palette (fixed order) + neutral ink.
CATEGORICAL = ["#2a78d6", "#1baf7a", "#eda100", "#008300",
               "#4a3aa7", "#e34948", "#e87ba4", "#eb6834"]
SERIES_1, SURFACE, INK, INK2, MUTED, GRID, OTHER = (
    "#2a78d6", "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c9c8c2")

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
    "text.color": INK, "axes.edgecolor": GRID, "axes.labelcolor": INK2,
    "xtick.color": MUTED, "ytick.color": INK2,
})


def methodology_line(graph: CitationGraph) -> str:
    report = graph.resolution_report or {}
    updated = report.get("authority_updated_through") or "unknown edition"
    rate = float(report.get("completeness", 0))
    return (
        f"authority through {updated} · resolution {rate:.1%} · "
        "one-hop scope · edge weight = citation count"
    )


def plot_network(graph: CitationGraph, title: str, out_dir: Path) -> None:
    citation_graph = graph.to_networkx()
    citation_graph.remove_nodes_from(list(nx.isolates(citation_graph)))
    metrics = compute_metrics(citation_graph, weight="citation_count")

    communities = {n: metrics[n].get("community", -1) for n in citation_graph.nodes()}
    top = [c for c, _ in Counter(communities.values()).most_common(8)]
    colour = [CATEGORICAL[top.index(communities[n])] if communities[n] in top else OTHER
              for n in citation_graph.nodes()]
    sizes = [30 + 4000 * metrics[n]["pagerank"] for n in citation_graph.nodes()]

    fig, ax = plt.subplots(figsize=(9, 7.2), dpi=150)
    fig.subplots_adjust(top=0.88, bottom=0.02, left=0.02, right=0.98)
    pos = nx.spring_layout(citation_graph, k=0.9, seed=3, iterations=140)
    nx.draw_networkx_edges(citation_graph, pos, ax=ax, edge_color="#d8d7d0", width=0.5, alpha=0.5)
    nx.draw_networkx_nodes(citation_graph, pos, ax=ax, node_color=colour, node_size=sizes,
                           linewidths=0.5, edgecolors=SURFACE)
    fig.text(0.03, 0.955, title, fontsize=13, fontweight="bold", color=INK)
    fig.text(0.03, 0.915, methodology_line(graph),
             fontsize=9.5, color=INK2)
    ax.axis("off")
    fig.savefig(out_dir / "citation-network.png", facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)


def plot_most_cited(
    graph: CitationGraph,
    out_dir: Path,
    *,
    corpus_label: str,
    top_n: int = 12,
) -> None:
    citation_graph = graph.to_networkx()
    names = graph.nodes_dataframe().set_index("node_id")["docname"]
    indeg = dict(citation_graph.in_degree())
    ranked = [n for n in sorted(indeg, key=lambda n: indeg[n], reverse=True)[:top_n]
              if indeg[n]]

    def label(n: str) -> str:
        name = str(names.get(n, n)).replace("CASE OF ", "").title()
        return name[:39] + "…" if len(name) > 42 else name

    labels = [label(n) for n in ranked][::-1]
    values = [indeg[n] for n in ranked][::-1]

    fig, ax = plt.subplots(figsize=(9, 5.4), dpi=150)
    fig.subplots_adjust(top=0.83, bottom=0.12, left=0.30, right=0.97)
    bars = ax.barh(labels, values, color=SERIES_1, height=0.68, zorder=3)
    for bar, v in zip(bars, values, strict=True):
        ax.text(bar.get_width() + max(values) * 0.01, bar.get_y() + bar.get_height() / 2,
                str(v), va="center", fontsize=9, color=INK2)
    fig.text(0.02, 0.945, f"Most-cited judgments in {corpus_label}",
             fontsize=13, fontweight="bold", color=INK)
    fig.text(0.02, 0.90, methodology_line(graph),
             fontsize=9.5, color=INK2)
    ax.set_xlabel("citations from the source documents", fontsize=9)
    ax.tick_params(axis="y", length=0, labelsize=9)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.xaxis.grid(True, color=GRID, linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)
    ax.margins(x=0.12)
    fig.savefig(out_dir / "most-cited-cases.png", facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--citations", required=True, help="Complete resolution directory")
    ap.add_argument("--title", default="Authoritatively resolved ECtHR citation network")
    ap.add_argument("--corpus-label", default="the corpus")
    ap.add_argument("--out-dir", type=Path, default=Path("."))
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    graph = CitationGraph.from_artifacts(args.citations, require_complete=True)
    print("citation stats:", graph.stats())
    print(methodology_line(graph))

    plot_network(graph, args.title, args.out_dir)
    plot_most_cited(graph, args.out_dir, corpus_label=args.corpus_label)
    print(f"wrote figures to {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
