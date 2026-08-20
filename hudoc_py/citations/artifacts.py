"""Persistence and human-review artifacts for citation resolution."""

from __future__ import annotations

import csv
import html
import json
from pathlib import Path
from typing import Any

from .models import (
    CitationCandidate,
    CitationMention,
    CitationOccurrenceResult,
    CitationResolution,
    CitationResolutionResult,
)


def _tabular(value: object) -> object:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return value


def _flatten_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: _tabular(value) for key, value in row.items()} for row in rows]


def _frame(rows: list[dict[str, Any]], *, empty_columns: list[str]):
    import pandas as pd

    flattened = _flatten_rows(rows)
    return pd.DataFrame(flattened) if flattened else pd.DataFrame(columns=empty_columns)


def write_resolution_artifacts(
    result: CitationResolutionResult, out_dir: str | Path
) -> dict[str, Path]:
    """Write the versioned Parquet/JSON artifacts for one resolution run."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    mentions: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    for resolution in result.resolutions:
        row = resolution.mention.model_dump(mode="json")
        row.update(
            {
                "status": resolution.status,
                "method": resolution.method,
                "resolved": resolution.resolved,
                "target_node_id": resolution.target.node_id if resolution.target else None,
                "target_ecli": resolution.target.ecli if resolution.target else None,
                "target_itemid": resolution.target.itemid if resolution.target else None,
                "authority_entry_id": resolution.authority_entry_id,
                "override_note": resolution.override_note,
                "override_reviewed_at": resolution.override_reviewed_at,
                "documented_exclusion": resolution.documented_exclusion,
            }
        )
        mentions.append(row)
        for candidate in resolution.candidates:
            candidate_rows.append(
                {
                    "mention_id": resolution.mention.mention_id,
                    "reference_hash": resolution.mention.reference_hash,
                    "status": resolution.status,
                    **candidate.model_dump(mode="json"),
                }
            )

    paths = {
        "mentions": out / "mentions.parquet",
        "targets": out / "targets.parquet",
        "candidates": out / "candidates.parquet",
        "nodes": out / "nodes.parquet",
        "edges": out / "edges.parquet",
        "report": out / "resolution-report.json",
    }
    _frame(
        mentions,
        empty_columns=["mention_id", "reference_hash", "raw_ref", "status", "resolved"],
    ).to_parquet(paths["mentions"], index=False)
    _frame(
        [target.model_dump(mode="json") for target in result.targets],
        empty_columns=["node_id", "itemid", "ecli", "docname"],
    ).to_parquet(paths["targets"], index=False)
    _frame(
        candidate_rows,
        empty_columns=["mention_id", "reference_hash", "status", "node_id"],
    ).to_parquet(paths["candidates"], index=False)
    _frame(result.nodes, empty_columns=["node_id", "itemid", "ecli", "docname"]).to_parquet(
        paths["nodes"], index=False
    )
    _frame(
        result.edges,
        empty_columns=["source", "target", "citation_count", "mention_ids"],
    ).to_parquet(paths["edges"], index=False)
    paths["report"].write_text(result.report.model_dump_json(indent=2), encoding="utf-8")
    return paths


def load_resolution_artifacts(resolution_dir: str | Path) -> dict[str, Any]:
    """Load graph-ready artifacts and the completeness report."""
    import pandas as pd

    root = Path(resolution_dir)
    return {
        "nodes": pd.read_parquet(root / "nodes.parquet"),
        "edges": pd.read_parquet(root / "edges.parquet"),
        "mentions": pd.read_parquet(root / "mentions.parquet"),
        "targets": pd.read_parquet(root / "targets.parquet"),
        "candidates": pd.read_parquet(root / "candidates.parquet"),
        "report": json.loads((root / "resolution-report.json").read_text(encoding="utf-8")),
    }


def _restore(value: object) -> object:
    """Undo the JSON-in-cell representation used by resolution Parquet files."""
    if value is None:
        return None
    try:
        import pandas as pd

        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, str) and value[:1] in {"[", "{"}:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def load_resolutions(resolution_dir: str | Path) -> list[CitationResolution]:
    """Rehydrate resolution rows needed by the occurrence locator."""
    artifacts = load_resolution_artifacts(resolution_dir)
    targets = {
        row["node_id"]: CitationCandidate.model_validate(
            {key: _restore(value) for key, value in row.items()}
        )
        for row in artifacts["targets"].to_dict("records")
    }
    extra = {
        "status",
        "method",
        "resolved",
        "target_node_id",
        "target_ecli",
        "target_itemid",
        "authority_entry_id",
        "override_note",
        "override_reviewed_at",
        "documented_exclusion",
    }
    resolutions: list[CitationResolution] = []
    for raw in artifacts["mentions"].to_dict("records"):
        row = {key: _restore(value) for key, value in raw.items()}
        mention = CitationMention.model_validate(
            {key: value for key, value in row.items() if key not in extra}
        )
        resolutions.append(
            CitationResolution(
                mention=mention,
                status=str(row["status"]),  # type: ignore[arg-type]
                method=str(row.get("method") or "artifact"),
                target=targets.get(str(row.get("target_node_id"))),
                authority_entry_id=row.get("authority_entry_id"),  # type: ignore[arg-type]
                override_note=row.get("override_note"),  # type: ignore[arg-type]
                override_reviewed_at=row.get("override_reviewed_at"),  # type: ignore[arg-type]
                documented_exclusion=bool(row.get("documented_exclusion") or False),
            )
        )
    return resolutions


def write_occurrence_artifacts(
    result: CitationOccurrenceResult, out_dir: str | Path
) -> dict[str, Path]:
    """Write portable occurrence Parquet/JSONL plus a versioned report."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = [value.model_dump(mode="json") for value in result.occurrences]
    paths = {
        "parquet": out / "occurrences.parquet",
        "jsonl": out / "occurrences.jsonl",
        "report": out / "occurrence-report.json",
        "mentions_inclusive": out / "mentions-inclusive.parquet",
        "mentions_inclusive_jsonl": out / "mentions-inclusive.jsonl",
        "edges_inclusive": out / "edges-inclusive.parquet",
        "paragraph_edges": out / "paragraph-edges-inclusive.parquet",
        "paragraph_edges_jsonl": out / "paragraph-edges-inclusive.jsonl",
    }
    _frame(
        rows,
        empty_columns=[
            "occurrence_id",
            "locus_id",
            "citation_group_id",
            "mention_id",
            "source_itemid",
            "source_para_id",
            "raw_text",
            "finder",
            "target_node_id",
            "target_paragraphs",
        ],
    ).to_parquet(paths["parquet"], index=False)
    paths["jsonl"].write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    mention_rows = [value.model_dump(mode="json") for value in result.mentions]
    _frame(
        mention_rows,
        empty_columns=[
            "mention_id",
            "origin",
            "source_itemid",
            "raw_ref",
            "cited_name",
            "explicit_appnos",
            "source_block_id",
            "source_opinion_id",
        ],
    ).to_parquet(paths["mentions_inclusive"], index=False)
    paths["mentions_inclusive_jsonl"].write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in mention_rows),
        encoding="utf-8",
    )
    _frame(
        result.inclusive_edges,
        empty_columns=[
            "source",
            "target",
            "occurrence_count",
            "mention_count",
            "scl_covered_occurrence_count",
            "text_only_occurrence_count",
        ],
    ).to_parquet(paths["edges_inclusive"], index=False)
    _frame(
        result.paragraph_edges,
        empty_columns=[
            "paragraph_edge_id",
            "occurrence_id",
            "locus_id",
            "citation_group_id",
            "source_itemid",
            "source_para_id",
            "source_footnote_id",
            "source_invoking_block_ids",
            "source_invoking_para_ids",
            "target_itemid",
            "target_para_id",
            "target_section",
            "target_text",
            "printed_pinpoint",
            "mapping_status",
        ],
    ).to_parquet(paths["paragraph_edges"], index=False)
    paths["paragraph_edges_jsonl"].write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, default=str) + "\n"
            for row in result.paragraph_edges
        ),
        encoding="utf-8",
    )
    report = result.report.model_dump(mode="json")
    report["diagnostics"] = result.diagnostics
    paths["report"].write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return paths


def _cell(value: object) -> str:
    if value is None:
        return ""
    return html.escape(str(value))


def write_review(
    resolution_dir: str | Path,
    html_path: str | Path,
    csv_path: str | Path,
) -> tuple[int, Path, Path]:
    """Write a read-only side-by-side ambiguity report and blank override sheet."""
    artifacts = load_resolution_artifacts(resolution_dir)
    mentions = artifacts["mentions"]
    unresolved = mentions[~mentions["resolved"].astype(bool)]
    if "documented_exclusion" in unresolved.columns:
        exclusion_mask = unresolved["documented_exclusion"].fillna(False).astype(bool)
        review_required = unresolved[~exclusion_mask]
        documented_exclusions = unresolved[exclusion_mask]
    else:
        # Compatibility with artifacts written before documented exclusions
        # became a distinct, accounted-for resolution outcome.
        review_required = unresolved
        documented_exclusions = unresolved.iloc[0:0]
    candidates = artifacts["candidates"]

    sections: list[str] = []
    for _, mention in unresolved.iterrows():
        mention_candidates = (
            candidates[candidates["mention_id"] == mention["mention_id"]]
            if "mention_id" in candidates.columns
            else candidates
        )
        rows = []
        for _, candidate in mention_candidates.iterrows():
            url = candidate.get("hudoc_url") or ""
            link = f'<a href="{_cell(url)}">HUDOC</a>' if url else ""
            rows.append(
                "<tr>"
                f"<td>{_cell(candidate.get('docname'))}</td>"
                f"<td>{_cell(candidate.get('ecli'))}</td>"
                f"<td>{_cell(candidate.get('appnos'))}</td>"
                f"<td>{_cell(candidate.get('date'))}</td>"
                f"<td>{_cell(candidate.get('doctype'))}</td>"
                f"<td>{_cell(candidate.get('procedural_phase'))}</td>"
                f"<td>{_cell(candidate.get('positive_evidence'))}</td>"
                f"<td>{_cell(candidate.get('conflicting_evidence'))}</td>"
                f"<td>{link}</td></tr>"
            )
        context = mention.get("source_context") or ""
        classification = (
            "Documented HUDOC exclusion – no override required"
            if bool(mention.get("documented_exclusion"))
            else "Reviewer decision required"
        )
        sections.append(
            "<section>"
            f"<h2>{_cell(mention.get('cited_name') or mention.get('raw_ref'))}</h2>"
            f"<p><strong>Classification:</strong> {_cell(classification)}</p>"
            f"<p><strong>Status:</strong> {_cell(mention.get('status'))} · "
            f"<strong>Reason:</strong> {_cell(mention.get('method'))} · "
            f"<strong>Reference hash:</strong> <code>{_cell(mention.get('reference_hash'))}</code></p>"
            f"<blockquote>{_cell(mention.get('raw_ref'))}</blockquote>"
            f"<details><summary>Source context</summary><pre>{_cell(context)}</pre></details>"
            "<table><thead><tr><th>Candidate</th><th>ECLI</th><th>Appnos</th><th>Date</th>"
            "<th>Type</th><th>Phase</th><th>Positive</th><th>Conflicts</th><th>Link</th>"
            f"</tr></thead><tbody>{''.join(rows)}</tbody></table></section>"
        )

    output_html = Path(html_path)
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(
        "<!doctype html><html><head><meta charset='utf-8'><title>Citation review</title>"
        "<style>body{font:14px system-ui;margin:2rem;max-width:1500px}section{border-top:2px solid #333;"
        "padding:1rem 0}table{border-collapse:collapse;width:100%}th,td{border:1px solid #bbb;"
        "padding:.4rem;vertical-align:top}blockquote,pre{white-space:pre-wrap;background:#f6f6f6;padding:1rem}"
        "code{font-size:12px}</style></head><body>"
        f"<h1>Citation resolution review</h1><p>{len(review_required)} references require review; "
        f"{len(documented_exclusions)} documented HUDOC exclusions require no override. "
        "This report does not apply decisions; edit the CSV deliberately and rerun resolution.</p>"
        + "".join(sections)
        + "</body></html>",
        encoding="utf-8",
    )

    output_csv = Path(csv_path)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "reference_hash",
                "source_ecli",
                "source_itemid",
                "raw_ref",
                "resolution_method",
                "target_ecli",
                "target_itemid",
                "reviewer_note",
                "reviewed_at",
            ],
        )
        writer.writeheader()
        for _, mention in review_required.iterrows():
            writer.writerow(
                {
                    "reference_hash": mention.get("reference_hash"),
                    "source_ecli": mention.get("source_ecli"),
                    "source_itemid": mention.get("source_itemid"),
                    "raw_ref": mention.get("raw_ref"),
                    "resolution_method": mention.get("method"),
                }
            )
    return len(review_required), output_html, output_csv
