"""Run the text-only deterministic baseline against imported Mumford XMI.

This reports strict source-span and normalized identity/context alignment
separately, with one-to-one reference-to-occurrence assignment. It excludes
HUDOC SCL data, target lookup, and model-backed citation-use labels. It is not
the complete inclusive pipeline and cannot estimate precision because the
external annotations are not exhaustive negatives.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from hudoc_py.citations import benchmark_citation_annotations, discover_citation_mentions
from hudoc_py.models import Case


def _jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--imported", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    documents = _jsonl(args.imported / "documents.jsonl")
    annotations = _jsonl(args.imported / "annotations.jsonl")
    curated = {
        str(row["source_itemid"]): row
        for row in documents
        if row.get("curated") is True
    }
    occurrences: list[dict[str, object]] = []
    for itemid, row in sorted(curated.items()):
        # Preserve the XMI Sofa string byte-for-character: annotations use its
        # original offsets, including CR/CRLF paragraph separators.
        text = str(row["source_text"])
        result = discover_citation_mentions(
            Case(itemid=itemid, language="ENG", text=text)
        )
        for occurrence in result.preliminary_occurrences:
            payload = occurrence.model_dump(mode="json")
            payload["source_text_sha256"] = row.get("source_text_sha256")
            occurrences.append(payload)

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "occurrences-text-only.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in occurrences),
        encoding="utf-8",
    )
    report = benchmark_citation_annotations(annotations, occurrences)
    report["evaluation_scope"] = {
        "documents": len(curated),
        "mode": "deterministic strong-envelope discovery over Mumford source text",
        "matching": "one-to-one reference annotation to local occurrence",
        "metric_families": [
            "strict source-span overlap",
            "normalized identity/context alignment",
            "exact-document resolution coverage and verified accuracy when available",
            "pinpoint recovery and target-paragraph mapping when available",
            "ambiguity and abstention",
        ],
        "does_not_include": [
            "HUDOC SCL-derived local gazetteers",
            "live or cached HUDOC target resolution",
            "optional LLM citation-use labels",
        ],
        "precision_warning": (
            "Mumford annotations are not exhaustive negatives; this run cannot "
            "estimate precision."
        ),
    }
    report["local_preliminary_occurrences"] = len(occurrences)
    (args.out / "comparison.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(
        {key: value for key, value in report.items() if key != "alignments"},
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
