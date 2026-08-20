"""Compare full inclusive occurrence artifacts with the Mumford reference.

Run ``citations locate --scope inclusive`` first.  This script projects the
full HUDOC block coordinates into each imported XMI Sofa before performing
identity-gated, one-to-one alignment.  It evaluates recovery of Mumford's
selected ECHR annotations, not detector precision or full-document recall.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from hudoc_py.citations import (
    benchmark_citation_annotations,
    load_benchmark_rows,
    project_mumford_occurrences,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--imported", type=Path, required=True)
    parser.add_argument("--occurrences", type=Path, required=True)
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    documents = load_benchmark_rows(args.imported / "documents.jsonl")
    annotations = load_benchmark_rows(args.imported / "annotations.jsonl")
    reference_itemids = {
        str(row["source_itemid"])
        for row in documents
        if row.get("curated") is True and row.get("source_itemid")
    }
    occurrences = [
        row for row in load_benchmark_rows(args.occurrences)
        if str(row.get("source_itemid") or "") in reference_itemids
    ]
    labels = load_benchmark_rows(args.labels) if args.labels else []
    projected, projection_report = project_mumford_occurrences(documents, occurrences)
    report = benchmark_citation_annotations(
        annotations,
        projected,
        labels=labels,
        reference_scope="echr",
    )
    report["offset_projection"] = projection_report
    report["evaluation_scope"] = {
        "mode": "full deterministic inclusive HUDOC occurrence pipeline",
        "reference": "Mumford curated ECHR annotations inside supplied THE LAW text",
        "matching": "identity-gated one-to-one alignment in projected XMI Sofa offsets",
        "precision_warning": (
            "Mumford annotations are selective and provide no exhaustive negatives; "
            "unmatched local occurrences are not classified as false positives."
        ),
        "resolution_warning": (
            "The reference has no canonical target item IDs or target-paragraph links; "
            "document and paragraph accuracy cannot be inferred from coverage alone."
        ),
    }

    args.out.mkdir(parents=True, exist_ok=True)
    diagnostics = projection_report.pop("diagnostics")
    (args.out / "projection-diagnostics.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in diagnostics),
        encoding="utf-8",
    )
    projected_path = args.out / "occurrences-projected.jsonl"
    projected_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in projected),
        encoding="utf-8",
    )
    (args.out / "comparison.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(
        {key: value for key, value in report.items() if key != "alignments"},
        ensure_ascii=False,
        indent=2,
        default=str,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
