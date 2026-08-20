"""Centrality metrics and export sanitisation for citation graphs.

Ported from cjeu-py's ``network_export._compute_centrality``. Requires the
``analysis`` extra (networkx).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import networkx as nx

logger = logging.getLogger(__name__)

METRIC_KEYS = ("in_degree", "out_degree", "pagerank", "betweenness", "community")


def _import_networkx() -> Any:
    try:
        import networkx as nx

        return nx
    except ImportError as exc:
        raise ImportError(
            "networkx is required for citation-graph metrics. "
            'Install it with: pip install "echr-py[analysis]"'
        ) from exc


def compute_metrics(
    graph: nx.DiGraph,
    *,
    pagerank_alpha: float = 0.85,
    betweenness_k: int | None = 500,
    louvain: bool = True,
    seed: int = 42,
    weight: str | None = None,
) -> dict[str, dict[str, float | int]]:
    """Compute per-node metrics, set them as node attributes, and return them.

    Metrics: ``in_degree``, ``out_degree``, ``pagerank`` (with a uniform
    fallback on convergence failure), k-sampled ``betweenness``, and Louvain
    ``community`` (on the undirected projection; communities numbered by
    descending size; omitted if detection fails).
    """
    nx = _import_networkx()
    n = graph.number_of_nodes()
    if n == 0:
        return {}

    logger.info("Computing centrality metrics for %d nodes...", n)
    nx.set_node_attributes(graph, dict(graph.in_degree()), "in_degree")
    nx.set_node_attributes(graph, dict(graph.out_degree()), "out_degree")

    try:
        pagerank = nx.pagerank(graph, alpha=pagerank_alpha, max_iter=200, weight=weight)
    except nx.PowerIterationFailedConvergence:
        logger.warning("PageRank failed to converge; using uniform scores")
        pagerank = {node: 1.0 / n for node in graph.nodes()}
    nx.set_node_attributes(graph, pagerank, "pagerank")

    k = min(betweenness_k, n) if betweenness_k is not None else None
    distance_key: str | None = None
    if weight:
        distance_key = "_citation_distance"
        for _source, _target, attrs in graph.edges(data=True):
            strength = float(attrs.get(weight, 1) or 1)
            attrs[distance_key] = 1.0 / strength
    betweenness = nx.betweenness_centrality(graph, k=k, seed=seed, weight=distance_key)
    if distance_key:
        for _source, _target, attrs in graph.edges(data=True):
            attrs.pop(distance_key, None)
    nx.set_node_attributes(graph, betweenness, "betweenness")

    if louvain:
        try:
            communities = sorted(
                nx.community.louvain_communities(graph.to_undirected(), seed=seed, weight=weight),
                key=len,
                reverse=True,
            )
            community_map = {node: i for i, comm in enumerate(communities) for node in comm}
            nx.set_node_attributes(graph, community_map, "community")
            logger.info("%d communities detected", len(communities))
        except Exception as exc:  # noqa: BLE001 – community detection is best-effort
            logger.warning("Louvain community detection failed: %s", exc)

    return {
        node: {key: attrs[key] for key in METRIC_KEYS if key in attrs}
        for node, attrs in graph.nodes(data=True)
    }


def sanitise_for_gexf(graph: nx.DiGraph) -> nx.DiGraph:
    """Copy of ``graph`` with GEXF-safe attributes (no None, no lists/dicts)."""
    clean_graph = graph.copy()
    for node in clean_graph.nodes():
        attrs = clean_graph.nodes[node]
        for key in [k for k, v in attrs.items() if v is None]:
            del attrs[key]
        for key, value in list(attrs.items()):
            if isinstance(value, list):
                attrs[key] = (
                    json.dumps(value, ensure_ascii=False)
                    if value and isinstance(value[0], dict)
                    else ";".join(str(x) for x in value)
                )
            elif isinstance(value, dict):
                attrs[key] = json.dumps(value, ensure_ascii=False)
            elif isinstance(value, str):
                attrs[key] = value.replace("‑", "-").replace("‐", "-")
    return clean_graph
