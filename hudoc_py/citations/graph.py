"""Citation graph: resolve citations against a corpus and export nodes/edges."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from ..models import Case
from ..models.citation import Citation, CitationCollection
from .extractor import extract_citations
from .models import CitationResolutionResult, IncompleteCitationResolutionError

if TYPE_CHECKING:
    import networkx as nx
    import pandas as pd


class CitationGraph:
    """Build graphs from authoritative artifacts or strict legacy matching.

    Usage::

        from hudoc_py.citations import CitationGraph

        g = CitationGraph.from_artifacts("corpus/citations/")
        metrics = g.metrics_dataframe(weight="citation_count")
        g.to_gexf("citations.gexf", with_metrics=True)
    """

    def __init__(self, cases: Iterable[Case], *, use_extracted_appno: bool = False):
        """Build the graph over a corpus of cases.

        ``use_extracted_appno=True`` adds HUDOC's deprecated
        ``extractedappno`` field as a fallback resolution path. That field
        contains every appno mentioned anywhere in the body (including
        cited cases), so using it tends to produce false positive edges
        (a citation to case X resolves to the source case Y because Y's
        body happens to mention X's appno). Leave it off unless you
        understand the trade-off. Graph metrics still require authoritative
        resolution artifacts unless diagnostic mode is explicitly selected.
        """
        self.cases: list[Case] = list(cases)
        self.citations: CitationCollection = CitationCollection()
        # Primary index – only the case's own appno(s). This is the reliable one.
        self._appno_index: dict[str, list[Case]] = defaultdict(list)
        # Fallback index – built lazily, only used when use_extracted_appno=True.
        self._extracted_index: dict[str, list[Case]] = defaultdict(list)
        self.use_extracted_appno = use_extracted_appno
        self._artifact_nodes: list[dict] | None = None
        self._artifact_edges: list[dict] | None = None
        self._resolution_report: dict | None = None
        self._build_indices()

    @classmethod
    def from_resolution(cls, result: CitationResolutionResult) -> CitationGraph:
        """Build an ECLI-keyed graph from authoritative resolution output."""
        graph = cls([])
        graph._artifact_nodes = [dict(row) for row in result.nodes]
        graph._artifact_edges = [dict(row) for row in result.edges]
        graph._resolution_report = result.report.model_dump(mode="json")
        graph.citations = CitationCollection(
            Citation(
                source_itemid=resolution.mention.source_itemid,
                source_appno=resolution.mention.source_appnos,
                source_ecli=resolution.mention.source_ecli,
                raw_ref=resolution.mention.raw_ref,
                cited_name=resolution.mention.cited_name,
                cited_appnos=resolution.mention.explicit_appnos,
                cited_itemid=resolution.target.itemid if resolution.target else None,
                cited_ecli=resolution.target.ecli if resolution.target else None,
                cited_docname=resolution.target.docname if resolution.target else None,
                target_node_id=resolution.target.node_id if resolution.target else None,
                mention_id=resolution.mention.mention_id,
                reference_hash=resolution.mention.reference_hash,
                resolution_status=resolution.status,
                resolution_method=resolution.method,
                resolved=resolution.resolved,
            )
            for resolution in result.resolutions
        )
        return graph

    @classmethod
    def from_artifacts(
        cls, resolution_dir: str | Path, *, require_complete: bool = True
    ) -> CitationGraph:
        """Load canonical nodes/edges and their completeness contract from disk."""
        from .artifacts import load_resolution_artifacts

        artifacts = load_resolution_artifacts(resolution_dir)
        report = artifacts["report"]
        if require_complete and not report.get("complete", False):
            raise IncompleteCitationResolutionError(
                f"citation resolution is {report.get('completeness', 0):.1%} complete; "
                "review unresolved references before computing metrics"
            )
        graph = cls([])
        graph._artifact_nodes = artifacts["nodes"].to_dict("records")
        graph._artifact_edges = artifacts["edges"].to_dict("records")
        graph._resolution_report = report
        return graph

    def _build_indices(self) -> None:
        for case in self.cases:
            for appno in case.appno:
                if appno:
                    self._appno_index[appno].append(case)
            if self.use_extracted_appno:
                for appno in case.extracted_appno:
                    if appno and case not in self._extracted_index[appno]:
                        self._extracted_index[appno].append(case)

    def _resolve_one(self, citation: Citation, source_case: Case) -> bool:
        """Look up ``citation.cited_appnos`` against the corpus index.

        Resolution order:
          1. Primary appno index (the case's own ``appno`` field).
          2. Optional extractedappno fallback (only if
             ``use_extracted_appno=True``).

        When multiple cases share an appno (typically ENG + FRE rows of the
        same judgment, or grouped applications), prefer the one whose
        ``language`` matches the source case.
        """
        candidates: list[Case] = []
        for appno in citation.cited_appnos:
            for cand in self._appno_index.get(appno, []):
                if cand is source_case:
                    continue
                if cand not in candidates:
                    candidates.append(cand)

        if not candidates and self.use_extracted_appno:
            for appno in citation.cited_appnos:
                for cand in self._extracted_index.get(appno, []):
                    if cand is source_case:
                        continue
                    if cand not in candidates:
                        candidates.append(cand)

        if not candidates:
            return False

        # Collapse language variants first. An appno shared by several distinct
        # ECLIs is procedurally ambiguous and must not be guessed.
        canonical = {(c.ecli or f"itemid:{c.itemid}"): c for c in candidates}
        if len(canonical) != 1:
            return False
        same_document = [c for c in candidates if (c.ecli or f"itemid:{c.itemid}") in canonical]

        # Prefer same-language presentation within that one canonical document.
        chosen: Case
        if source_case.language:
            same_lang = [c for c in same_document if c.language == source_case.language]
            chosen = same_lang[0] if same_lang else same_document[0]
        else:
            chosen = same_document[0]

        citation.cited_itemid = chosen.itemid
        citation.cited_ecli = chosen.ecli
        citation.cited_docname = chosen.docname
        citation.resolved = True
        return True

    def resolve(self) -> CitationCollection:
        """Extract citations from every case and resolve them.

        Returns the populated :class:`CitationCollection` (same as ``self.citations``).
        """
        self.citations = CitationCollection()
        for case in self.cases:
            for citation in extract_citations(case):
                self._resolve_one(citation, case)
                self.citations.append(citation)
        return self.citations

    # --- Reporting -----------------------------------------------------

    def stats(self) -> dict[str, int]:
        """Return a small dict summarising resolution outcomes."""
        if self._artifact_nodes is not None and self._artifact_edges is not None:
            report = self._resolution_report or {}
            return {
                "total_refs": int(report.get("mentions", 0)),
                "resolved": int(report.get("resolved", 0)),
                "unresolved": int(report.get("unresolved", 0)),
                "refs_with_appno": 0,
                "refs_without_appno": 0,
                "corpus_size": len(self._artifact_nodes),
            }
        total = len(self.citations)
        resolved = sum(1 for c in self.citations if c.resolved)
        with_appno = sum(1 for c in self.citations if c.cited_appnos)
        return {
            "total_refs": total,
            "resolved": resolved,
            "unresolved": total - resolved,
            "refs_with_appno": with_appno,
            "refs_without_appno": total - with_appno,
            "corpus_size": len(self.cases),
        }

    @property
    def resolution_report(self) -> dict | None:
        """Return the persisted authority/completeness contract, if present."""
        return dict(self._resolution_report) if self._resolution_report else None

    # --- Exports -------------------------------------------------------

    def nodes_dataframe(self) -> pd.DataFrame:
        """One row per case in the corpus."""
        import pandas as pd

        if self._artifact_nodes is not None:
            return pd.DataFrame(self._artifact_nodes)
        rows = []
        for case in self.cases:
            rows.append(
                {
                    "itemid": case.itemid,
                    "docname": case.docname,
                    "ecli": case.ecli,
                    "appno": ";".join(case.appno) if case.appno else None,
                    "kp_date": case.kp_date.isoformat() if case.kp_date else None,
                    "articles": ";".join(case.articles) if case.articles else None,
                    "respondent": ";".join(case.respondent) if case.respondent else None,
                    "importance": case.importance,
                    "language": case.language,
                    "doctype": case.doctype,
                }
            )
        return pd.DataFrame(rows)

    def edges_dataframe(self, *, include_unresolved: bool = False) -> pd.DataFrame:
        """One row per citation. By default only resolved citations."""
        import pandas as pd

        if self._artifact_edges is not None:
            return pd.DataFrame(self._artifact_edges)
        rows = []
        for c in self.citations:
            if not include_unresolved and not c.resolved:
                continue
            rows.append(
                {
                    "source_itemid": c.source_itemid,
                    "source_appno": ";".join(c.source_appno) if c.source_appno else None,
                    "cited_itemid": c.cited_itemid,
                    "cited_appnos": ";".join(c.cited_appnos) if c.cited_appnos else None,
                    "cited_name": c.cited_name,
                    "cited_docname": c.cited_docname,
                    "cited_ecli": c.cited_ecli,
                    "resolved": c.resolved,
                    "raw_ref": c.raw_ref,
                }
            )
        return pd.DataFrame(rows)

    def missing_refs_dataframe(self) -> pd.DataFrame:
        """Convenience: just the unresolved refs (for audit)."""
        return self.edges_dataframe(include_unresolved=True).query("not resolved")

    def to_networkx(self) -> nx.DiGraph:
        """Build a directed citation graph. Edges go source → cited."""
        try:
            import networkx as nx
        except ImportError as exc:
            raise ImportError(
                "networkx is required for CitationGraph.to_networkx(). "
                'Install it with: pip install "echr-py[analysis]"'
            ) from exc

        graph: nx.DiGraph = nx.DiGraph()
        if self._artifact_nodes is not None and self._artifact_edges is not None:
            for row in self._artifact_nodes:
                attrs = dict(row)
                node_id = str(attrs.pop("node_id"))
                graph.add_node(node_id, **attrs)
            for row in self._artifact_edges:
                attrs = dict(row)
                source = str(attrs.pop("source"))
                target = str(attrs.pop("target"))
                graph.add_edge(source, target, **attrs)
            return graph
        for case in self.cases:
            if not case.itemid:
                continue
            graph.add_node(
                case.itemid,
                docname=case.docname or "",
                ecli=case.ecli or "",
                kp_date=case.kp_date.isoformat() if case.kp_date else "",
                respondent=";".join(case.respondent) if case.respondent else "",
                articles=";".join(case.articles) if case.articles else "",
                importance=case.importance or "",
            )
        for c in self.citations:
            if not c.resolved:
                continue
            if c.source_itemid and c.cited_itemid:
                graph.add_edge(c.source_itemid, c.cited_itemid, raw_ref=c.raw_ref)
        return graph

    def to_gexf(
        self,
        path: str,
        *,
        with_metrics: bool = False,
        allow_incomplete: bool = False,
        **metric_kwargs,
    ) -> None:
        """Write a GEXF file readable by Gephi and other graph tools.

        With ``with_metrics=True``, centrality metrics are computed first and
        included as (GEXF-sanitised) node attributes.
        """
        from ..graphs import export_gexf, from_networkx
        from .metrics import compute_metrics

        if not allow_incomplete:
            if self._resolution_report is not None and not self._resolution_report.get(
                "complete", False
            ):
                raise IncompleteCitationResolutionError("citation resolution is incomplete")
            if self._resolution_report is None:
                raise IncompleteCitationResolutionError(
                    "legacy corpus-local resolution has no authority/completeness report; "
                    "run `echr-py citations resolve`"
                )
        graph = self.to_networkx()
        if with_metrics:
            compute_metrics(graph, **metric_kwargs)
        export_gexf(from_networkx(graph, graph_id="citation-scl", kind="citation-scl"), path)

    def metrics_dataframe(self, *, allow_incomplete: bool = False, **metric_kwargs) -> pd.DataFrame:
        """Nodes joined with centrality metrics (requires the analysis extra).

        Columns: everything from :meth:`nodes_dataframe` plus ``in_degree``,
        ``out_degree``, ``pagerank``, ``betweenness``, ``community``.
        """
        import pandas as pd

        from .metrics import METRIC_KEYS, compute_metrics

        if not allow_incomplete:
            if self._resolution_report is not None and not self._resolution_report.get(
                "complete", False
            ):
                raise IncompleteCitationResolutionError("citation resolution is incomplete")
            if self._resolution_report is None:
                raise IncompleteCitationResolutionError(
                    "legacy corpus-local resolution has no authority/completeness report; "
                    "run `echr-py citations resolve`"
                )
        graph = self.to_networkx()
        metrics = compute_metrics(graph, **metric_kwargs)
        nodes = self.nodes_dataframe()
        key = "node_id" if self._artifact_nodes is not None else "itemid"
        metrics_df = pd.DataFrame(
            [{key: node, **values} for node, values in metrics.items()],
            columns=[key, *METRIC_KEYS],
        )
        return nodes.merge(metrics_df, on=key, how="left")

    def to_html(
        self,
        path: str,
        *,
        max_nodes: int | None = None,
        allow_incomplete: bool = False,
        **metric_kwargs,
    ) -> None:
        """Write a self-contained interactive D3 viewer for the citation graph.

        Node size reflects PageRank, colour reflects Louvain community.
        ``max_nodes`` keeps only the top-N nodes by PageRank (plus the edges
        among them) – useful for very large corpora.
        """
        from ..graphs import export_html, from_networkx, prune_bundle
        from .metrics import compute_metrics

        if not allow_incomplete:
            if self._resolution_report is not None and not self._resolution_report.get(
                "complete", False
            ):
                raise IncompleteCitationResolutionError("citation resolution is incomplete")
            if self._resolution_report is None:
                raise IncompleteCitationResolutionError(
                    "legacy corpus-local resolution has no authority/completeness report; "
                    "run `echr-py citations resolve`"
                )
        graph = self.to_networkx()
        compute_metrics(graph, **metric_kwargs)

        appno_by_itemid = {
            case.itemid: ";".join(case.appno) for case in self.cases if case.itemid and case.appno
        }
        for node, attrs in graph.nodes(data=True):
            attrs.setdefault("appno", attrs.get("appnos", appno_by_itemid.get(node, "")))
        bundle = from_networkx(graph, graph_id="citation-scl", kind="citation-scl")
        export_html(prune_bundle(bundle, max_nodes), path)

    def to_jsonl(self, edges_path: str, nodes_path: str | None = None) -> None:
        """Persist the graph as JSONL (one row per edge, optionally one per node)."""
        import json
        from pathlib import Path

        edges = Path(edges_path)
        edges.parent.mkdir(parents=True, exist_ok=True)
        with edges.open("w", encoding="utf-8") as fh:
            if self._artifact_edges is not None:
                for row in self._artifact_edges:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            else:
                for c in self.citations:
                    if c.resolved:
                        fh.write(json.dumps(c.model_dump(mode="json"), ensure_ascii=False) + "\n")
        if nodes_path:
            nodes = Path(nodes_path)
            nodes.parent.mkdir(parents=True, exist_ok=True)
            with nodes.open("w", encoding="utf-8") as fh:
                if self._artifact_nodes is not None:
                    for row in self._artifact_nodes:
                        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                else:
                    for case in self.cases:
                        fh.write(
                            json.dumps(
                                {
                                    "itemid": case.itemid,
                                    "docname": case.docname,
                                    "ecli": case.ecli,
                                    "appno": case.appno,
                                    "kp_date": case.kp_date.isoformat() if case.kp_date else None,
                                    "articles": case.articles,
                                    "respondent": case.respondent,
                                    "importance": case.importance,
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
