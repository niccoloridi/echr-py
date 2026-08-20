"""Build a self-contained graph from a pinned, real-shape HUDOC artifact."""

from __future__ import annotations

import argparse
from pathlib import Path

from hudoc_py.graphs import export_graph, from_paragraph_edges


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--in",
        dest="input",
        type=Path,
        default=Path(__file__).with_name("data") / "ocalan-paragraph-edges.ndjson",
        help="Paragraph-edge JSONL/Parquet file or citation artifact directory",
    )
    parser.add_argument("--out", type=Path, default=Path("citation-example.html"))
    args = parser.parse_args()

    bundle = from_paragraph_edges(args.input)
    bundle.meta.graph_id = "ocalan-paragraph-citation-example"
    export_graph(bundle, args.out, fmt="html")
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
