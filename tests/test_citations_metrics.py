"""Tests for citation-graph metrics and HTML export (requires networkx)."""

from __future__ import annotations

import pytest

nx = pytest.importorskip("networkx", reason="analysis extra not installed")

from hudoc_py.citations.graph import CitationGraph  # noqa: E402
from hudoc_py.citations.metrics import compute_metrics, sanitise_for_gexf  # noqa: E402
from hudoc_py.models import Case  # noqa: E402


def _graph():
    """hub ← a, b, c; a → b. Directed toward the cited case."""
    graph = nx.DiGraph()
    for node in ("hub", "a", "b", "c"):
        graph.add_node(node, docname=f"CASE {node.upper()}")
    graph.add_edge("a", "hub")
    graph.add_edge("b", "hub")
    graph.add_edge("c", "hub")
    graph.add_edge("a", "b")
    return graph


def test_compute_metrics_degree_and_pagerank():
    graph = _graph()
    metrics = compute_metrics(graph)
    assert metrics["hub"]["in_degree"] == 3
    assert metrics["a"]["out_degree"] == 2
    assert sum(m["pagerank"] for m in metrics.values()) == pytest.approx(1.0, rel=1e-3)
    assert metrics["hub"]["pagerank"] == max(m["pagerank"] for m in metrics.values())
    assert all("betweenness" in m for m in metrics.values())
    assert all(isinstance(m.get("community"), int) for m in metrics.values())


def test_compute_metrics_k_larger_than_n_guard():
    graph = _graph()
    metrics = compute_metrics(graph, betweenness_k=500)
    assert len(metrics) == 4


def test_compute_metrics_empty_graph():
    assert compute_metrics(nx.DiGraph()) == {}


def test_sanitise_for_gexf():
    graph = nx.DiGraph()
    graph.add_node("x", none_attr=None, list_attr=["a", "b"], dict_attr={"k": 1}, ok="fine")
    clean = sanitise_for_gexf(graph)
    attrs = clean.nodes["x"]
    assert "none_attr" not in attrs
    assert attrs["list_attr"] == "a;b"
    assert attrs["dict_attr"] == '{"k": 1}'
    assert attrs["ok"] == "fine"
    # Original untouched.
    assert graph.nodes["x"]["none_attr"] is None


def _cases():
    # b cites a via scl with a's appno; c cites a too.
    a = Case.model_validate(
        {"itemid": "001-A", "appno": "111/99", "docname": "CASE A", "languageisocode": "ENG"}
    )
    b = Case.model_validate(
        {
            "itemid": "001-B",
            "appno": "222/99",
            "docname": "CASE B",
            "languageisocode": "ENG",
            "scl": "Case A v. State, no. 111/99, 1 January 2000",
        }
    )
    c = Case.model_validate(
        {
            "itemid": "001-C",
            "appno": "333/99",
            "docname": "CASE C",
            "languageisocode": "ENG",
            "scl": "Case A v. State, no. 111/99, 1 January 2000",
        }
    )
    return [a, b, c]


def test_metrics_dataframe_columns():
    pytest.importorskip("pandas")
    g = CitationGraph(_cases())
    g.resolve()
    df = g.metrics_dataframe(allow_incomplete=True)
    for col in ("itemid", "pagerank", "in_degree", "out_degree", "betweenness"):
        assert col in df.columns
    hub = df.set_index("itemid").loc["001-A"]
    assert hub["in_degree"] == 2


def test_to_html_writes_embedded_data(tmp_path):
    g = CitationGraph(_cases())
    g.resolve()
    out = tmp_path / "graph.html"
    g.to_html(str(out), allow_incomplete=True)
    html = out.read_text(encoding="utf-8")
    assert "__DATA_PLACEHOLDER__" not in html
    assert '"001-A"' in html
    assert "CASE A" in html
    assert "d3.forceSimulation" in html


def test_to_html_max_nodes(tmp_path):
    g = CitationGraph(_cases())
    g.resolve()
    out = tmp_path / "graph.html"
    g.to_html(str(out), max_nodes=1, allow_incomplete=True)
    html = out.read_text(encoding="utf-8")
    # Only the top-PageRank node (the cited hub) survives.
    assert '"001-A"' in html
    assert '"001-B"' not in html


def test_to_gexf_with_metrics(tmp_path):
    g = CitationGraph(_cases())
    g.resolve()
    out = tmp_path / "graph.gexf"
    g.to_gexf(str(out), with_metrics=True, allow_incomplete=True)
    content = out.read_text(encoding="utf-8")
    assert "pagerank" in content
