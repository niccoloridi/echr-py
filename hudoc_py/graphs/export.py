"""JSON, GEXF and offline browser exports for any supported graph."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import GraphBundle, GraphLink, GraphMeta, GraphNode


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def from_networkx(graph: Any, *, graph_id: str = "graph", kind: str = "networkx") -> GraphBundle:
    nodes = [
        GraphNode(
            id=str(node),
            label=str(attrs.get("label") or attrs.get("docname") or node),
            attributes={str(k): _json_safe(v) for k, v in attrs.items() if k != "label"},
        )
        for node, attrs in graph.nodes(data=True)
    ]
    links = []
    if graph.is_multigraph():
        iterator = graph.edges(keys=True, data=True)
        for index, (source, target, key, attrs) in enumerate(iterator):
            links.append(_link(source, target, attrs, f"{source}|{target}|{key}|{index}"))
    else:
        for index, (source, target, attrs) in enumerate(graph.edges(data=True)):
            links.append(_link(source, target, attrs, f"{source}|{target}|{index}"))
    return GraphBundle(
        meta=GraphMeta(
            graph_id=graph_id,
            kind=kind,
            directed=bool(graph.is_directed()),
            multigraph=bool(graph.is_multigraph()),
            node_count=len(nodes),
            edge_count=len(links),
            # A graph bundle is a content contract. Acquisition/run timestamps
            # belong in caller-supplied metadata so repeated exports are byte-stable.
            generated_at=None,
        ),
        nodes=sorted(nodes, key=lambda node: node.id),
        links=sorted(links, key=lambda edge: edge.id),
    )


def _link(source: Any, target: Any, attrs: dict[str, Any], edge_id: str) -> GraphLink:
    attributes = {str(k): _json_safe(v) for k, v in attrs.items() if k != "weight"}
    return GraphLink(
        id=edge_id,
        source=str(source),
        target=str(target),
        weight=float(attrs.get("weight", attrs.get("citation_count", 1)) or 1),
        attributes=attributes,
    )


def to_networkx(bundle: GraphBundle) -> Any:
    try:
        import networkx as nx
    except ImportError as exc:
        raise ImportError("Graph exports require echr-py[analysis]") from exc
    factory = (
        nx.MultiDiGraph
        if bundle.meta.directed and bundle.meta.multigraph
        else nx.MultiGraph
        if bundle.meta.multigraph
        else nx.DiGraph
        if bundle.meta.directed
        else nx.Graph
    )
    graph = factory()
    for node in bundle.nodes:
        graph.add_node(node.id, label=node.label or node.id, **node.attributes)
    for link in bundle.links:
        attributes = {"weight": link.weight, "edge_id": link.id, **link.attributes}
        if graph.is_multigraph():
            graph.add_edge(link.source, link.target, key=link.id, **attributes)
        else:
            graph.add_edge(link.source, link.target, **attributes)
    return graph


def prune_bundle(bundle: GraphBundle, max_nodes: int | None) -> GraphBundle:
    if max_nodes is None or len(bundle.nodes) <= max_nodes:
        return bundle
    graph = to_networkx(bundle)
    try:
        import networkx as nx

        scores = nx.pagerank(graph, weight="weight")
    except Exception:
        scores = {node: float(graph.degree(node)) for node in graph}
    keep = set(sorted(scores, key=lambda node: (-scores[node], str(node)))[:max_nodes])
    nodes = [node for node in bundle.nodes if node.id in keep]
    links = [link for link in bundle.links if link.source in keep and link.target in keep]
    return bundle.model_copy(
        update={
            "nodes": nodes,
            "links": links,
            "meta": bundle.meta.model_copy(
                update={
                    "node_count": len(nodes),
                    "edge_count": len(links),
                    "pruned": True,
                    "original_node_count": len(bundle.nodes),
                }
            ),
        }
    )


def _gexf_value(value: Any) -> Any:
    if isinstance(value, (list, tuple, set, dict)):
        return json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True)
    return _json_safe(value)


def export_gexf(bundle: GraphBundle, path: str | Path) -> Path:
    import networkx as nx

    graph = to_networkx(bundle)
    for _, attrs in graph.nodes(data=True):
        converted = {key: _gexf_value(value) for key, value in attrs.items() if value is not None}
        attrs.clear()
        attrs.update(converted)
    for *_, attrs in graph.edges(data=True):
        converted = {key: _gexf_value(value) for key, value in attrs.items() if value is not None}
        attrs.clear()
        attrs.update(converted)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    nx.write_gexf(graph, path)
    return path


def _d3_source() -> str:
    source = Path(__file__).with_name("assets") / "d3.v7.min.js"
    if not source.exists():
        raise FileNotFoundError("vendored D3 asset is missing from echr-py")
    return source.read_text(encoding="utf-8")


def _d3_license() -> str:
    source = Path(__file__).with_name("assets") / "LICENSE-D3.txt"
    if not source.exists():
        raise FileNotFoundError("vendored D3 licence is missing from echr-py")
    return source.read_text(encoding="utf-8")


def export_json(bundle: GraphBundle, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(bundle.model_dump_json(indent=2), encoding="utf-8")
    return path


def export_html(bundle: GraphBundle, path: str | Path) -> Path:
    from .template import HTML_TEMPLATE

    payload = json.dumps(bundle.model_dump(mode="json"), ensure_ascii=False).replace("</", "<\\/")
    licence = _d3_license().replace("</", "<\\/")
    html = (
        HTML_TEMPLATE.replace("__D3_LICENSE__", licence)
        .replace("__D3_SOURCE__", _d3_source())
        .replace("__GRAPH_DATA__", payload)
    )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path


def export_graph(
    bundle: GraphBundle,
    path: str | Path,
    *,
    fmt: str | None = None,
    max_nodes: int | None = None,
) -> Path:
    path = Path(path)
    fmt = (fmt or path.suffix.lstrip(".")).lower()
    value = prune_bundle(bundle, max_nodes)
    if fmt == "json":
        return export_json(value, path)
    if fmt == "gexf":
        return export_gexf(value, path)
    if fmt in {"html", "htm"}:
        return export_html(value, path)
    raise ValueError("graph format must be json, gexf or html")


__all__ = [
    "export_gexf",
    "export_graph",
    "export_html",
    "export_json",
    "from_networkx",
    "prune_bundle",
    "to_networkx",
]
