"""The shared graph contract is deterministic, portable, and fully offline."""

import json

import networkx as nx

from hudoc_py.cli import main
from hudoc_py.graphs import export_graph, from_networkx, from_paragraph_edges


def _graph():
    graph = nx.MultiDiGraph()
    graph.add_node("a", label="Alpha </script>", section="majority", score=2)
    graph.add_node("b", label="Beta", section="opinion", score=1)
    graph.add_node("isolated", label="Isolated", section="majority", score=0)
    graph.add_edge("a", "b", key="majority", weight=2, pinpoints=["42", "43"])
    graph.add_edge("a", "b", key="opinion", weight=1, opinion_id="op-1")
    return graph


def test_json_bundle_is_deterministic_and_preserves_multiedges(tmp_path):
    bundle = from_networkx(_graph(), graph_id="test", kind="citation-paragraph")
    first = export_graph(bundle, tmp_path / "first.json", fmt="json")
    second = export_graph(bundle, tmp_path / "second.json", fmt="json")

    assert first.read_bytes() == second.read_bytes()
    value = json.loads(first.read_text())
    assert value["schema_version"] == "hudoc-graph/v1"
    assert value["meta"]["multigraph"] is True
    assert len(value["links"]) == 2


def test_offline_html_inlines_d3_and_escapes_script_end(tmp_path):
    bundle = from_networkx(_graph(), graph_id="test", kind="citation-paragraph")
    path = export_graph(bundle, tmp_path / "graph.html", fmt="html")
    html = path.read_text(encoding="utf-8")

    assert "<script src=" not in html
    assert "Connected component" in html
    assert "function(t,n){" in html  # vendored minified D3
    assert "Alpha <\\/script>" in html
    assert "Permission to use, copy, modify, and/or distribute" in html


def test_gexf_omits_none_and_serializes_structured_attributes(tmp_path):
    graph = _graph()
    graph.nodes["a"]["missing"] = None
    bundle = from_networkx(graph, graph_id="test")
    path = export_graph(bundle, tmp_path / "graph.gexf", fmt="gexf")

    loaded = nx.read_gexf(path)
    assert "missing" not in loaded.nodes["a"]
    attributes = list(loaded["a"]["b"].values())
    assert any(value.get("pinpoints") == '["42", "43"]' for value in attributes)


def test_explicit_top_n_pruning_records_original_size(tmp_path):
    bundle = from_networkx(_graph(), graph_id="test")
    path = export_graph(bundle, tmp_path / "small.json", fmt="json", max_nodes=2)
    value = json.loads(path.read_text())

    assert value["meta"]["pruned"] is True
    assert value["meta"]["original_node_count"] == 3
    assert value["meta"]["node_count"] == 2


def test_paragraph_adapter_enriches_artifacts_and_filters_footnotes(tmp_path):
    edges = tmp_path / "paragraph-edges-inclusive.jsonl"
    edges.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "paragraph_edge_id": "footnote-edge",
                        "occurrence_id": "occ-footnote",
                        "source_itemid": "001-source",
                        "source_block_id": "footnote-body",
                        "source_footnote_id": "ftn1",
                        "source_invoking_para_ids": ["17"],
                        "source_component": "opinion",
                        "source_opinion_id": "opinion-1",
                        "target_itemid": "001-target",
                        "target_para_id": "target-42",
                        "target_para_num": 42,
                        "target_section": "the_law",
                    }
                ),
                json.dumps(
                    {
                        "paragraph_edge_id": "majority-edge",
                        "occurrence_id": "occ-majority",
                        "source_itemid": "001-other",
                        "source_para_id": "8",
                        "source_component": "majority",
                        "target_itemid": "001-target",
                        "target_para_id": "target-42",
                        "target_para_num": 42,
                    }
                ),
            ]
        )
        + "\n"
    )
    (tmp_path / "occurrences.jsonl").write_text(
        json.dumps(
            {
                "occurrence_id": "occ-footnote",
                "raw_text": "Rantsev, § 42",
                "source_context": "See Rantsev, § 42.",
                "source_opinion_authors": ["Judge Example"],
            }
        )
        + "\n"
    )
    (tmp_path / "nodes.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"node_id": "item:001-source", "itemid": "001-source", "docname": "Source v. State"}),
                json.dumps({"node_id": "item:001-target", "itemid": "001-target", "docname": "Rantsev v. Cyprus and Russia", "ecli": "ECLI:CE:ECHR:2010:0107JUD002596504"}),
            ]
        )
        + "\n"
    )

    bundle = from_paragraph_edges(
        tmp_path,
        source_items=["001-source"],
        source_components=["opinion"],
        opinion_ids=["opinion-1"],
        footnotes_only=True,
    )

    assert bundle.meta.edge_count == 1
    assert bundle.meta.filters["footnotes_only"] is True
    source = next(node for node in bundle.nodes if node.attributes["side"] == "source")
    target = next(node for node in bundle.nodes if node.attributes["side"] == "target")
    assert source.label == "Source v. State footnote ftn1"
    assert source.attributes["context"] == "See Rantsev, § 42."
    assert source.attributes["invoking_para_ids"] == ["17"]
    assert target.label == "Rantsev v. Cyprus and Russia § 42"
    assert target.attributes["section"] == "the_law"


def test_graph_export_cli_passes_paragraph_filters(tmp_path):
    source = tmp_path / "paragraph-edges.ndjson"
    source.write_text(
        json.dumps(
            {
                "paragraph_edge_id": "edge-1",
                "source_itemid": "001-source",
                "source_block_id": "footnote-body",
                "source_component": "opinion",
                "source_opinion_id": "opinion-1",
                "source_footnote_id": "ftn1",
                "target_itemid": "001-target",
                "target_para_id": "42",
            }
        )
        + "\n"
    )
    out = tmp_path / "graph.json"

    assert (
        main(
            [
                "graph",
                "export",
                "--kind",
                "citation-paragraph",
                "--in",
                str(source),
                "--out",
                str(out),
                "--format",
                "json",
                "--source-item",
                "001-source",
                "--source-component",
                "opinion",
                "--opinion-id",
                "opinion-1",
                "--footnotes-only",
            ]
        )
        == 0
    )
    value = json.loads(out.read_text())
    assert value["meta"]["edge_count"] == 1
    assert value["meta"]["filters"] == {
        "source_items": ["001-source"],
        "source_components": ["opinion"],
        "opinion_ids": ["opinion-1"],
        "footnotes_only": True,
    }
