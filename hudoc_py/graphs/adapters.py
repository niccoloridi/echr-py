"""Adapters from echr-py graph artifacts to the neutral bundle."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..utils.jsonl import iter_jsonl
from .export import from_networkx
from .models import GraphBundle, GraphLink, GraphMeta, GraphNode


def from_citation_graph(graph: Any, *, kind: str = "citation-scl") -> GraphBundle:
    return from_networkx(graph.to_networkx(), graph_id=kind, kind=kind)


def _rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix == ".parquet":
        import pandas as pd

        return pd.read_parquet(path).to_dict("records")
    return list(iter_jsonl(path))


def _decoded(value: Any) -> Any:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, str) or not value or value[0] not in "[{":
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _artifact(path: Path, names: Iterable[str]) -> Path | None:
    return next((path / name for name in names if (path / name).exists()), None)


def _paragraph_artifacts(
    root: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    occurrence_path = _artifact(root, ("occurrences.parquet", "occurrences.jsonl"))
    node_path = _artifact(root, ("nodes.parquet", "nodes.jsonl"))
    occurrences = (
        {str(row.get("occurrence_id")): row for row in _rows(occurrence_path)}
        if occurrence_path
        else {}
    )
    nodes: dict[str, dict[str, Any]] = {}
    if node_path:
        for row in _rows(node_path):
            for key in (row.get("node_id"), row.get("itemid"), row.get("ecli")):
                if key:
                    nodes[str(key)] = row
    return occurrences, nodes


def from_paragraph_edges(
    path: str | Path,
    *,
    source_items: Iterable[str] | None = None,
    source_components: Iterable[str] | None = None,
    opinion_ids: Iterable[str] | None = None,
    footnotes_only: bool = False,
) -> GraphBundle:
    """Build a paragraph graph and enrich artifact directories when possible."""
    source = Path(path)
    root = source if source.is_dir() else None
    if source.is_dir():
        for name in ("paragraph-edges-inclusive.parquet", "paragraph-edges-inclusive.jsonl"):
            candidate = source / name
            if candidate.exists():
                source = candidate
                break
    raw_rows = _rows(source)
    occurrences, metadata = _paragraph_artifacts(root) if root else ({}, {})
    wanted_items = set(source_items or [])
    wanted_components = set(source_components or [])
    wanted_opinions = set(opinion_ids or [])
    rows: list[dict[str, Any]] = []
    for raw_row in raw_rows:
        row = {key: _decoded(value) for key, value in raw_row.items()}
        occurrence = occurrences.get(str(row.get("occurrence_id")), {})
        for key, value in occurrence.items():
            row.setdefault(key, _decoded(value))
        if wanted_items and str(row.get("source_itemid")) not in wanted_items:
            continue
        if wanted_components and str(row.get("source_component")) not in wanted_components:
            continue
        if wanted_opinions and str(row.get("source_opinion_id")) not in wanted_opinions:
            continue
        if footnotes_only and not row.get("source_footnote_id"):
            continue
        rows.append(row)
    nodes: dict[str, GraphNode] = {}
    links = []
    for index, row in enumerate(rows):
        source_id = (
            f"{row.get('source_itemid')}:{row.get('source_para_id') or row.get('source_block_id')}"
        )
        target_id = f"{row.get('target_itemid') or row.get('target_node_id')}:{row.get('target_para_id') or row.get('target_block_id')}"
        source_meta = metadata.get(str(row.get("source_itemid")), {})
        target_meta = metadata.get(
            str(row.get("target_itemid") or row.get("target_node_id") or row.get("target_ecli")),
            {},
        )
        source_title = source_meta.get("docname") or row.get("source_docname")
        target_title = target_meta.get("docname") or row.get("target_docname")
        source_label = source_title or row.get("source_itemid")
        if row.get("source_footnote_id"):
            source_label = f"{source_label} footnote {row.get('source_footnote_id')}"
        else:
            source_label = f"{source_label} § {row.get('source_para_num') or '?'}"
        nodes.setdefault(
            source_id,
            GraphNode(
                id=source_id,
                label=str(source_label),
                attributes={
                    "side": "source",
                    "itemid": row.get("source_itemid"),
                    "title": source_title,
                    "language": row.get("source_language") or source_meta.get("language"),
                    "section": row.get("source_section"),
                    "component": row.get("source_component"),
                    "opinion_id": row.get("source_opinion_id"),
                    "opinion_type": row.get("source_opinion_type"),
                    "opinion_authors": row.get("source_opinion_authors"),
                    "footnote_id": row.get("source_footnote_id"),
                    "invoking_para_ids": row.get("source_invoking_para_ids"),
                    "context": row.get("source_context"),
                },
            ),
        )
        nodes.setdefault(
            target_id,
            GraphNode(
                id=target_id,
                label=f"{target_title or row.get('target_itemid') or row.get('target_node_id')} § {row.get('target_para_num') or '?'}",
                attributes={
                    "side": "target",
                    "itemid": row.get("target_itemid"),
                    "ecli": row.get("target_ecli") or target_meta.get("ecli"),
                    "title": target_title,
                    "language": row.get("target_language") or target_meta.get("language"),
                    "section": row.get("target_section"),
                    "text": row.get("target_text"),
                },
            ),
        )
        links.append(
            GraphLink(
                id=str(row.get("paragraph_edge_id") or f"paragraph-edge-{index}"),
                source=source_id,
                target=target_id,
                attributes={k: v for k, v in row.items() if k not in {"paragraph_edge_id"}},
            )
        )
    return GraphBundle(
        meta=GraphMeta(
            graph_id="citation-paragraph",
            kind="citation-paragraph",
            directed=True,
            multigraph=True,
            node_count=len(nodes),
            edge_count=len(links),
            filters={
                "source_items": sorted(wanted_items),
                "source_components": sorted(wanted_components),
                "opinion_ids": sorted(wanted_opinions),
                "footnotes_only": footnotes_only,
            },
        ),
        nodes=sorted(nodes.values(), key=lambda node: node.id),
        links=links,
    )


def load_graph_json(path: str | Path) -> GraphBundle:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if value.get("schema_version") == "hudoc-graph/v1":
        return GraphBundle.model_validate(value)
    try:
        import networkx as nx

        graph = nx.node_link_graph(value, edges="links")
    except Exception as exc:
        raise ValueError("networkx CLI input must be hudoc-graph/v1 or node-link JSON") from exc
    return from_networkx(graph)


__all__ = [
    "from_citation_graph",
    "from_paragraph_edges",
    "load_graph_json",
]
