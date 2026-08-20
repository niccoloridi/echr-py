"""MCP stdio server over HUDOC and HUDOC-EXEC.

The default server is deliberately read-only. Bounded persistent study jobs
are registered only when a path-confined :class:`StudyJobManager` is supplied
by the explicit ``echr-py mcp --enable-jobs`` launch path.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from .. import aio as main_aio
from ..execution import aio as exec_aio
from ..graphs.models import GraphBundle, GraphLink, GraphMeta, GraphNode

logger = logging.getLogger(__name__)


def _citation_network_response(
    response: dict[str, Any],
    *,
    source_title: str | None,
    top_targets: int,
) -> dict[str, Any]:
    """Aggregate a verbose occurrence response into a model-sized graph bundle."""
    occurrences = response.get("occurrences") or []
    resolutions = response.get("citations") or []
    resolution_report = response.get("resolution_report") or {}
    source_itemid = str(response.get("itemid") or "unknown-source")
    target_metadata: dict[str, dict[str, Any]] = {}
    for resolution in resolutions:
        target = resolution.get("target") or {}
        for value in (target.get("node_id"), target.get("itemid"), target.get("ecli")):
            if value:
                target_metadata[str(value)] = target

    target_counts: Counter[str] = Counter()
    edge_rows: dict[tuple[str, str], dict[str, Any]] = {}
    source_nodes: dict[str, GraphNode] = {}
    target_nodes: dict[str, GraphNode] = {}
    unresolved_occurrences = 0
    for occurrence in occurrences:
        if occurrence.get("resolution_scope") != "document":
            unresolved_occurrences += 1
            continue
        target_key = str(
            occurrence.get("target_node_id")
            or occurrence.get("target_itemid")
            or occurrence.get("target_ecli")
            or ""
        )
        if not target_key:
            unresolved_occurrences += 1
            continue
        component = str(occurrence.get("source_component") or "majority")
        if component == "opinion":
            opinion_key = str(
                occurrence.get("source_opinion_id")
                or occurrence.get("source_opinion_ordinal")
                or "unknown"
            )
            source_key = f"source:{source_itemid}:opinion:{opinion_key}"
            authors = occurrence.get("source_opinion_authors") or []
            opinion_type = occurrence.get("source_opinion_type") or "individual opinion"
            author_text = ", ".join(str(value) for value in authors)
            source_label = f"{source_title or source_itemid} – {opinion_type}"
            if author_text:
                source_label += f" ({author_text})"
        else:
            source_key = f"source:{source_itemid}:{component}"
            source_label = f"{source_title or source_itemid} – {component}"
        source_nodes.setdefault(
            source_key,
            GraphNode(
                id=source_key,
                label=source_label,
                attributes={
                    "side": "source",
                    "itemid": source_itemid,
                    "component": component,
                    "opinion_id": occurrence.get("source_opinion_id"),
                    "opinion_type": occurrence.get("source_opinion_type"),
                    "opinion_authors": occurrence.get("source_opinion_authors") or [],
                },
            ),
        )

        target = target_metadata.get(target_key, {})
        target_id = f"target:{target_key}"
        target_nodes.setdefault(
            target_id,
            GraphNode(
                id=target_id,
                label=str(
                    target.get("docname")
                    or ", ".join(str(value) for value in occurrence.get("target_appnos") or [])
                    or occurrence.get("target_ecli")
                    or occurrence.get("target_itemid")
                    or target_key
                ),
                attributes={
                    "side": "target",
                    "node_id": target.get("node_id") or occurrence.get("target_node_id"),
                    "itemid": target.get("itemid") or occurrence.get("target_itemid"),
                    "ecli": target.get("ecli") or occurrence.get("target_ecli"),
                    "appnos": target.get("appnos") or occurrence.get("target_appnos") or [],
                    "document_kind": target.get("document_kind"),
                    "procedural_phase": target.get("procedural_phase"),
                },
            ),
        )
        target_counts[target_id] += 1
        edge = edge_rows.setdefault(
            (source_key, target_id),
            {
                "occurrence_count": 0,
                "pinpoint_occurrence_count": 0,
                "footnote_occurrence_count": 0,
                "scl_covered_occurrence_count": 0,
                "text_only_occurrence_count": 0,
                "sections": set(),
                "pinpoints": set(),
            },
        )
        edge["occurrence_count"] += 1
        if occurrence.get("target_paragraphs"):
            edge["pinpoint_occurrence_count"] += 1
            edge["pinpoints"].update(str(value) for value in occurrence["target_paragraphs"])
        if occurrence.get("source_footnote_id"):
            edge["footnote_occurrence_count"] += 1
        if occurrence.get("scl_coverage") == "covered":
            edge["scl_covered_occurrence_count"] += 1
        if occurrence.get("scl_coverage") == "not_covered":
            edge["text_only_occurrence_count"] += 1
        if occurrence.get("source_section"):
            edge["sections"].add(str(occurrence["source_section"]))

    keep_targets = {
        target
        for target, _ in sorted(target_counts.items(), key=lambda value: (-value[1], value[0]))[
            :top_targets
        ]
    }
    links = []
    retained_sources: set[str] = set()
    for index, ((source, target), values) in enumerate(sorted(edge_rows.items())):
        if target not in keep_targets:
            continue
        retained_sources.add(source)
        attributes = {
            **{key: value for key, value in values.items() if key not in {"sections", "pinpoints"}},
            "sections": sorted(values["sections"]),
            "pinpoints": sorted(values["pinpoints"]),
        }
        links.append(
            GraphLink(
                id=f"mcp-citation-edge-{index}",
                source=source,
                target=target,
                weight=float(values["occurrence_count"]),
                attributes=attributes,
            )
        )
    nodes = [source_nodes[value] for value in sorted(retained_sources)] + [
        target_nodes[value] for value in sorted(keep_targets)
    ]
    bundle = GraphBundle(
        meta=GraphMeta(
            graph_id=f"mcp-citation-network:{source_itemid}",
            kind="citation-inclusive-component-target",
            directed=True,
            multigraph=False,
            node_count=len(nodes),
            edge_count=len(links),
            pruned=len(target_counts) > len(keep_targets),
            original_node_count=len(source_nodes) + len(target_nodes),
            filters={"top_targets": top_targets},
            attributes={
                "source_itemid": source_itemid,
                "source_title": source_title,
                "resolved_mentions": resolution_report.get("resolved", 0),
                "target_documents": resolution_report.get("target_documents", 0),
                "occurrences": len(occurrences),
                "unresolved_occurrences": unresolved_occurrences,
            },
        ),
        nodes=nodes,
        links=links,
    )
    components = Counter(str(value.get("source_component") or "majority") for value in occurrences)
    return {
        "found": bool(response.get("found")),
        "itemid": response.get("itemid"),
        "appno": response.get("appno"),
        "summary": {
            "mentions": resolution_report.get("mentions", 0),
            "resolved_mentions": resolution_report.get("resolved", 0),
            "target_documents": resolution_report.get("target_documents", 0),
            "occurrences": len(occurrences),
            "majority_occurrences": components["majority"],
            "opinion_occurrences": components["opinion"],
            "appendix_occurrences": components["appendix"],
            "pinpoint_occurrences": sum(
                1 for value in occurrences if value.get("target_paragraphs")
            ),
            "text_only_occurrences": sum(
                1 for value in occurrences if value.get("scl_coverage") == "not_covered"
            ),
            "unresolved_occurrences": unresolved_occurrences,
            "included_target_nodes": len(keep_targets),
            "included_aggregate_edges": len(links),
        },
        "top_targets": [
            {
                "node_id": target,
                "label": target_nodes[target].label,
                "occurrence_count": count,
            }
            for target, count in sorted(
                target_counts.items(), key=lambda value: (-value[1], value[0])
            )[: min(10, top_targets)]
        ],
        "graph": bundle.model_dump(mode="json"),
    }


def _import_fastmcp():
    try:
        from mcp.server.fastmcp import FastMCP

        return FastMCP
    except ImportError as exc:
        raise ImportError("MCP SDK is required. Install with: pip install 'echr-py[mcp]'") from exc


def _tool_annotations(
    title: str,
    *,
    read_only: bool = True,
    destructive: bool = False,
    idempotent: bool = True,
    open_world: bool = True,
) -> Any:
    """Return MCP tool hints without making the SDK a base dependency."""
    from mcp.types import ToolAnnotations

    return ToolAnnotations(
        title=title,
        readOnlyHint=read_only,
        destructiveHint=destructive,
        idempotentHint=idempotent,
        openWorldHint=open_world,
    )


def build_server(*, job_manager: Any | None = None) -> Any:
    """Construct and return a configured FastMCP server."""
    fast_mcp = _import_fastmcp()
    server = fast_mcp("echr-py")

    def _search_filters(
        article: str | None,
        respondent: str | None,
        date_from: str | None,
        date_to: str | None,
        importance: str | None,
        conclusion: str | None,
        thesaurus: str | None,
        docname: str | None,
        body: str | None,
        separate_opinion: bool | None,
        ecli: str | None,
        text: str | None,
    ) -> dict[str, Any]:
        filters: dict[str, Any] = dict(
            article=article,
            respondent=respondent,
            date_from=date_from,
            date_to=date_to,
            importance=importance,
            conclusion=conclusion,
            kpthesaurus=thesaurus,
            docname=docname,
            body=body,
            separate_opinion=separate_opinion,
            ecli=ecli,
            text=text,
        )
        return {k: v for k, v in filters.items() if v is not None}

    def _case_summary(c: Any) -> dict[str, Any]:
        return {
            "itemid": c.itemid,
            "docname": c.docname,
            "appno": c.appno,
            "ecli": c.ecli,
            "articles": c.articles,
            "respondent": c.respondent,
            "kp_date": c.kp_date.isoformat() if c.kp_date else None,
            "importance": c.importance,
            "doctype": c.doctype,
            "language": c.language,
            "conclusion": c.conclusion,
            "rank": c.rank,
        }

    @server.tool(annotations=_tool_annotations("Search ECtHR cases"))
    async def search_cases(
        article: str | None = None,
        respondent: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        importance: str | None = None,
        conclusion: str | None = None,
        thesaurus: str | None = None,
        docname: str | None = None,
        body: str | None = None,
        separate_opinion: bool | None = None,
        ecli: str | None = None,
        text: str | None = None,
        sort: str = "relevance",
        limit: int = 25,
    ) -> dict[str, Any]:
        """Search HUDOC main for cases matching filters.

        Returns case summaries (itemid, docname, appno, articles, conclusion,
        rank, ...) plus ``total_matches`` – the server-side match count, which
        may exceed the number returned. Use ``get_case_metadata`` for full
        details on one case, or ``search_and_read`` to get texts in one call.

        - article: Convention article number, e.g. "3" or "8"
        - respondent: ISO country code, e.g. "ITA", "FRA"
        - date_from / date_to: ISO dates ("YYYY-MM-DD")
        - importance: "1" (highest) through "4"
        - conclusion: e.g. "Violation of Article 3"
        - thesaurus: keyword text (e.g. "torture") or a numeric keypoint ID
          ("350"); use search_keypoints to look one up
        - docname: case-title fragment, e.g. "McCann"
        - body: "grand-chamber", "chamber", or "committee"
        - separate_opinion: has separate opinion(s)
        - ecli: ECLI identifier
        - text: free-text Lucene fragment AND-ed onto the query
        - sort: "relevance" (default), "date-desc", or "date-asc". The
          ``rank`` score is only meaningful with a full-text ``text`` query
          under relevance sort.
        """
        cases = await main_aio.search(
            sort=sort,
            limit=limit,
            **_search_filters(
                article,
                respondent,
                date_from,
                date_to,
                importance,
                conclusion,
                thesaurus,
                docname,
                body,
                separate_opinion,
                ecli,
                text,
            ),
        )
        summaries = [_case_summary(c) for c in cases]
        return {
            "count": len(summaries),
            "total_matches": cases.result_count,
            "results": summaries,
        }

    @server.tool(annotations=_tool_annotations("Count ECtHR cases"))
    async def count_cases(
        article: str | None = None,
        respondent: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        importance: str | None = None,
        conclusion: str | None = None,
        thesaurus: str | None = None,
        docname: str | None = None,
        body: str | None = None,
        separate_opinion: bool | None = None,
        ecli: str | None = None,
        text: str | None = None,
    ) -> dict[str, Any]:
        """Count HUDOC matches without fetching any rows.

        Cheap preview before a large ``search_cases`` call. Takes the same
        filters as ``search_cases``.
        """
        n = await main_aio.count(
            **_search_filters(
                article,
                respondent,
                date_from,
                date_to,
                importance,
                conclusion,
                thesaurus,
                docname,
                body,
                separate_opinion,
                ecli,
                text,
            )
        )
        return {"count": n}

    @server.tool(annotations=_tool_annotations("Search and read ECtHR cases"))
    async def search_and_read(
        text: str | None = None,
        article: str | None = None,
        respondent: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        importance: str | None = None,
        conclusion: str | None = None,
        body: str | None = None,
        top: int = 5,
        sort: str = "relevance",
        section: str = "the_law",
        max_chars: int = 20000,
    ) -> dict[str, Any]:
        """Search HUDOC and return the top-N matches WITH their texts.

        The one-call relevance workflow: give a full-text ``text`` query
        (e.g. '"positive obligations"'), get back the most relevant cases
        with the requested section of each judgment, ranked by HUDOC's own
        relevance model. Each text is truncated to ``max_chars``.

        - section: "the_law" (default), "dispositif", or "full"
        - sort: "relevance" (default; requires a ``text`` query to be
          meaningful), "date-desc", or "date-asc"
        """
        filters = _search_filters(
            article,
            respondent,
            date_from,
            date_to,
            importance,
            conclusion,
            None,
            None,
            body,
            None,
            None,
            text,
        )
        cases = await main_aio.smart_fetch(top=top, sort=sort, **filters)
        results = []
        for c in cases:
            if section != "full" and c.sections:
                body_text = getattr(c.sections, section, None) or c.text
            else:
                body_text = c.text
            if body_text and len(body_text) > max_chars:
                body_text = body_text[:max_chars] + "\n[... truncated]"
            results.append({**_case_summary(c), "section": section, "text": body_text})
        return {
            "count": len(results),
            "total_matches": cases.result_count,
            "results": results,
        }

    @server.tool(annotations=_tool_annotations("Get ECtHR case metadata"))
    async def get_case_metadata(
        appno: str | None = None,
        itemid: str | None = None,
    ) -> dict[str, Any]:
        """Fetch full metadata for a single HUDOC case.

        Provide ``appno`` (e.g. "46221/99") or ``itemid`` (e.g. "001-94054").
        Returns the complete typed record as a JSON object.
        """
        if not (appno or itemid):
            raise ValueError("Provide appno or itemid")
        case = await main_aio.fetch_case(appno=appno, itemid=itemid, with_text=False)
        if case is None:
            return {"found": False}
        return {"found": True, "case": case.model_dump(mode="json")}

    @server.tool(annotations=_tool_annotations("Get ECtHR case text"))
    async def get_case_text(
        appno: str | None = None,
        itemid: str | None = None,
        format: str = "text",
        section: str = "full",
    ) -> dict[str, Any]:
        """Fetch the full judgment text for a case (or a specific section).

        - format: "text" (plain), "md" (Markdown), or "html"
        - section: "full" (default), "the_law", or "dispositif"
        """
        if not (appno or itemid):
            raise ValueError("Provide appno or itemid")
        case = await main_aio.fetch_case(
            appno=appno,
            itemid=itemid,
            with_text=True,
            text_format=format,
            segment=True,
        )
        if case is None or case.text is None:
            return {"found": False}

        body: str | None
        if section == "full":
            body = case.text
        elif case.sections and section == "the_law":
            body = case.sections.the_law
        elif case.sections and section == "dispositif":
            body = case.sections.dispositif
        else:
            body = None

        return {
            "found": True,
            "itemid": case.itemid,
            "appno": case.appno,
            "format": format,
            "section": section,
            "text": body,
        }

    @server.tool(annotations=_tool_annotations("Search HUDOC-EXEC cases"))
    async def search_exec(
        state: str | None = None,
        supervision: str | None = None,
        is_closed: bool | None = None,
        case_type: str | None = None,
        limit: int = 25,
    ) -> dict[str, Any]:
        """Search HUDOC-EXEC for execution cases.

        - state: ISO country code (e.g. "ITA")
        - supervision: "standard" or "enhanced"
        - is_closed: filter by closed status
        - case_type: e.g. "leading" or "repetitive"
        """
        cases = await exec_aio.search(
            state=state,
            supervision=supervision,
            is_closed=is_closed,
            case_type=case_type,
            limit=limit,
        )
        summaries = [
            {
                "execidentifier": c.execidentifier,
                "title": c.title,
                "state": c.state,
                "appno": c.appno,
                "supervision": c.supervision,
                "is_closed": c.is_closed,
                "ap_status": c.ap_status,
                "judgment_date": c.judgment_date.isoformat() if c.judgment_date else None,
                # Include the storage UUID so the next tool call
                # (get_exec_document) is directly chainable.
                "content_store_id": c.content_store_id,
                "content_store_type": c.content_store_type,
            }
            for c in cases
        ]
        return {"count": len(summaries), "results": summaries}

    @server.tool(annotations=_tool_annotations("Search HUDOC-EXEC documents"))
    async def search_exec_documents(
        collection: str,
        state: str | None = None,
        appno: str | None = None,
        limit: int = 25,
    ) -> dict[str, Any]:
        """Search HUDOC-EXEC documents (action plans, CM decisions, etc.).

        Each result row exposes ``content_store_id``, which is what
        ``get_exec_document`` needs to fetch the body. Use this rather
        than ``search_exec`` when you want document bodies – ``search_exec``
        returns case-level index rows that don't carry their own body.

        - collection: one of "acp" (action plans), "acr" (action reports),
          "CMDEC" (CM decisions), "CMNOT" (CM notes), "HEXEC" (H/Exec
          memos), "apo" (applicant communications), "gvo" (government
          communications), "EXECUTION" (resolutions), or others. See
          ``hudoc_py.execution.COLLECTION_CODES``.
        - state: ISO country code (optional)
        - appno: application number (optional)
        """
        docs = await exec_aio.search_documents(
            collection=collection,
            state=state,
            appno=appno,
            limit=limit,
        )
        summaries = [
            {
                "execidentifier": d.execidentifier,
                "title": d.title,
                "state": d.state,
                "appno": d.appno,
                "content_store_id": d.content_store_id,
                "content_store_type": d.content_store_type,
                "published_date": d.published_date.isoformat() if d.published_date else None,
                "document_type": d.document_type,
                "document_type_collection": d.document_type_collection,
            }
            for d in docs
        ]
        return {"count": len(summaries), "results": summaries}

    @server.tool(annotations=_tool_annotations("Get a HUDOC-EXEC document"))
    async def get_exec_document(
        content_store_id: str,
        format: str = "text",
    ) -> dict[str, Any]:
        """Fetch a HUDOC-EXEC document body.

        Pass ``content_store_id`` – the ``execcontentstoreid`` field on an
        ExecutionDocument returned by ``search_exec`` or the linked
        action_plans / cm_decisions / resolutions on an ExecutionCase.
        (The human-readable ``execidentifier`` like ``DH-DD(2026)427E`` does
        NOT work here – the HUDOC-EXEC conversion endpoint requires the
        internal storage UUID.)

        - format: "text" (plain), "md" (Markdown), or "html"
        The MCP surface returns official source content and metadata for
        downstream, researcher-defined coding.
        """
        doc = await exec_aio.fetch_document(
            content_store_id,
            with_text=True,
            text_format=format,
        )
        if doc is None or doc.text is None:
            return {"found": False}
        payload: dict[str, Any] = {
            "found": True,
            "content_store_id": doc.content_store_id,
            "format": format,
            "text": doc.text,
        }
        return payload

    @server.tool(annotations=_tool_annotations("Search ECtHR key points"))
    async def search_keypoints(query: str, limit: int = 25) -> dict[str, Any]:
        """Find ECHR keyword (keypoint) IDs by label text.

        HUDOC indexes each case's legal keywords as numeric keypoint IDs. Use
        this to turn a phrase like "positive obligations" or "torture" into the
        matching keypoint id(s), then pass the id (or the same text) as the
        ``thesaurus`` filter to ``search_cases`` / ``count_cases``.
        """
        from ..thesaurus import search_keypoints as _search

        matches = _search(query)
        return {
            "count": len(matches),
            "results": [{"id": tid, "label": label} for tid, label in matches[:limit]],
        }

    @server.tool(annotations=_tool_annotations("List official document versions"))
    async def list_document_versions(
        appno: str | None = None,
        ecli: str | None = None,
    ) -> dict[str, Any]:
        """List every independently downloadable HUDOC language record.

        English and French are marked as official Court languages. Other
        records may be third-party translations; itemid remains the exact
        version identity, including when a language has multiple records.
        """
        if not (appno or ecli):
            raise ValueError("Provide appno or ecli")
        versions = await main_aio.list_versions(appno=appno, ecli=ecli)
        results = []
        for version in versions:
            row = version.model_dump(mode="json")
            row["html_url"] = (
                "https://hudoc.echr.coe.int/app/conversion/docx/html/body"
                f"?library=ECHR&id={version.itemid}"
            )
            row["docx_url"] = (
                "https://hudoc.echr.coe.int/app/conversion/docx"
                f"?library=ECHR&id={version.itemid}&filename={version.itemid}.docx"
            )
            results.append(row)
        return {"count": len(results), "results": results}

    @server.tool(annotations=_tool_annotations("Get dispositive paragraphs"))
    async def get_dispositive_paragraphs(
        appno: str | None = None,
        itemid: str | None = None,
        language: str | None = None,
    ) -> dict[str, Any]:
        """Return individual operative-part rulings with stable paragraph addresses."""
        from ..text import extract_dispositive_paragraphs

        if not (appno or itemid):
            raise ValueError("Provide appno or itemid")
        case = await main_aio.fetch_case(
            appno=appno,
            itemid=itemid,
            language=language,
            with_text=True,
            rich_sections=True,
        )
        if case is None or case.sections is None:
            return {"found": False}
        rulings = extract_dispositive_paragraphs(case.sections)
        return {
            "found": True,
            "itemid": case.itemid,
            "language": case.language,
            "count": len(rulings),
            "rulings": [value.model_dump(mode="json") for value in rulings],
        }

    @server.tool(annotations=_tool_annotations("Search a local paragraph index"))
    async def search_local_paragraphs(
        query: str,
        database: str | None = None,
        mode: str = "lexical",
        embeddings: str | None = None,
        section: str | None = None,
        language: str | None = None,
        itemid: str | None = None,
        source_component: str | None = None,
        opinion_id: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        candidate_k: int = 100,
        limit: int = 25,
    ) -> dict[str, Any]:
        """Search a local paragraph index in lexical, semantic, or hybrid mode."""
        from .. import config
        from ..retrieval import HybridRetriever

        path = database or str(config.DATA_DIR / "paragraphs.sqlite")
        retriever = HybridRetriever(
            path,
            mode=mode,
            embeddings=embeddings,
            top_k=limit,
            candidate_k=candidate_k,
        )
        rows = retriever.search(
            query,
            section=section,
            language=language,
            itemid=itemid,
            source_component=source_component,
            opinion_id=opinion_id,
            date_from=date_from,
            date_to=date_to,
        )
        return {
            "count": len(rows),
            "database": path,
            "mode": mode,
            "embeddings": embeddings,
            "results": rows,
        }

    @server.tool(annotations=_tool_annotations("List referenced Convention articles"))
    async def list_articles_referenced(
        appno: str | None = None,
        itemid: str | None = None,
    ) -> dict[str, Any]:
        """Return the Convention articles invoked in a HUDOC case."""
        if not (appno or itemid):
            raise ValueError("Provide appno or itemid")
        case = await main_aio.fetch_case(appno=appno, itemid=itemid, with_text=False)
        if case is None:
            return {"found": False}
        return {
            "found": True,
            "itemid": case.itemid,
            "appno": case.appno,
            "articles": case.articles,
        }

    @server.tool(annotations=_tool_annotations("Get source-aware case segments"))
    async def get_case_segments(
        appno: str | None = None,
        itemid: str | None = None,
        sections: list[str] | None = None,
        include_text: bool = True,
        include_spine: bool = False,
    ) -> dict[str, Any]:
        """Fetch canonical sections with auditable segmentation metadata.

        Sections include ``procedure``, ``facts``, ``subject_matter``,
        ``complaints``, ``the_law``, ``court_assessment``, ``operative``,
        ``separate_opinion``, and ``appendix``.

        Pass ``sections=["facts", "operative"]`` to filter to specific
        slices. Set ``include_text=False`` for a compact structure summary.
        Set ``include_spine`` only when the caller can accept a potentially
        large source-order block payload.
        """
        if not (appno or itemid):
            raise ValueError("Provide appno or itemid")
        case = await main_aio.fetch_case(
            appno=appno,
            itemid=itemid,
            with_text=True,
            segment=True,
            rich_sections=True,
        )
        if case is None or case.sections is None:
            return {"found": False}

        all_sections = {
            "procedure": case.sections.procedure,
            "facts": case.sections.facts,
            "subject_matter": case.sections.subject_matter,
            "complaints": case.sections.complaints,
            "the_law": case.sections.the_law,
            "court_assessment": case.sections.court_assessment,
            "operative": case.sections.operative,
            "separate_opinion": case.sections.separate_opinion,
            "appendix": case.sections.appendix,
        }
        section_lengths = {key: len(value or "") for key, value in all_sections.items()}
        if sections:
            wanted = {s for s in sections if s in all_sections}
            payload = {k: v for k, v in all_sections.items() if k in wanted and include_text}
        else:
            payload = all_sections if include_text else {}

        opinions = [
            {
                "opinion_type": o.opinion_type,
                "joint": o.joint,
                "joint_heading": o.joint_heading,
                "authors": o.authors,
                "joined_by": o.joined_by,
                "judges": o.judges,
                "raw_header": o.raw_header,
            }
            for o in case.sections.opinions
        ]
        response: dict[str, Any] = {
            "found": True,
            "itemid": case.itemid,
            "appno": case.appno,
            "language": case.language,
            "sections_present": case.sections.found,
            "segmentation_status": case.sections.status,
            "segmentation_confidence": case.sections.confidence,
            "segmentation_diagnostics": [
                diagnostic.model_dump(mode="json") for diagnostic in case.sections.diagnostics
            ],
            "section_spans": [span.model_dump(mode="json") for span in case.sections.spans],
            "section_lengths": section_lengths,
            "sections": payload,
            # Individual separate opinions (headers only – request the
            # separate_opinion section for their full text).
            "opinions": opinions,
            "opinions_confidence": case.sections.opinions_confidence,
            "opinion_diagnostics": case.sections.opinion_diagnostics,
            "bench": (case.sections.bench.model_dump(mode="json") if case.sections.bench else None),
        }
        if case.sections.spine is not None:
            spine_blocks = case.sections.spine.blocks
            response["spine_summary"] = {
                "block_count": len(spine_blocks),
                "paragraph_count": sum(1 for block in spine_blocks if block.para_id),
                "sections": dict(Counter(block.section or "unknown" for block in spine_blocks)),
                "source_components": dict(
                    Counter(
                        "opinion"
                        if block.opinion_id
                        else "appendix"
                        if block.section == "appendix"
                        else "majority"
                        for block in spine_blocks
                    )
                ),
                "footnote_blocks": sum(1 for block in spine_blocks if block.footnote_id),
                "opinion_blocks": sum(1 for block in spine_blocks if block.opinion_id),
            }
        if include_spine and case.sections.spine is not None:
            response["spine"] = case.sections.spine.model_dump(mode="json")
        return response

    @server.tool(annotations=_tool_annotations("Get case citation occurrences"))
    async def get_case_citations(
        appno: str | None = None,
        itemid: str | None = None,
        max_refs: int = 200,
        resolve: bool = False,
        include_occurrences: bool = False,
        include_target_paragraphs: bool = False,
        citation_scope: str = "scl",
    ) -> dict[str, Any]:
        """Return all case-law citations from a HUDOC case's ``scl`` field.

        With ``resolve=False`` this returns parsed references without network
        expansion. ``resolve=True`` runs the authoritative one-hop resolver
        and includes exact-document statuses and a completeness report.
        ``include_occurrences=True`` also fetches HUDOC HTML and locates each
        reference in its source paragraph without an LLM.
        """
        from ..citations import discover_citation_mentions, parse_scl_mentions

        if not (appno or itemid):
            raise ValueError("Provide appno or itemid")
        if citation_scope not in {"scl", "inclusive"}:
            raise ValueError("citation_scope must be 'scl' or 'inclusive'")
        if include_target_paragraphs and not resolve:
            raise ValueError("include_target_paragraphs requires resolve=True")
        needs_text = (
            include_occurrences or include_target_paragraphs or citation_scope == "inclusive"
        )
        case = await main_aio.fetch_case(
            appno=appno,
            itemid=itemid,
            with_text=needs_text,
            text_format="html" if needs_text else "text",
            rich_sections=needs_text,
        )
        if case is None:
            return {"found": False}
        mentions = parse_scl_mentions(case)[:max_refs]
        if citation_scope == "inclusive":
            discovered = discover_citation_mentions(
                case,
                html=case.text,
                spine=case.sections.spine if case.sections else None,
            ).mentions
            mentions = [*mentions, *discovered][:max_refs]
        resolution_result = None
        if resolve:
            from ..citations import resolve_citations
            from ..main.client import AsyncHudocClient

            async with AsyncHudocClient() as client:
                resolution_result = await resolve_citations(
                    [case], mentions=mentions, client=client, max_mentions=max_refs
                )
            response: dict[str, Any] = {
                "found": True,
                "itemid": case.itemid,
                "appno": case.appno,
                "total_refs": len(resolution_result.resolutions),
                "resolution_report": resolution_result.report.model_dump(mode="json"),
                "citations": [
                    resolution.model_dump(mode="json")
                    for resolution in resolution_result.resolutions
                ],
            }
        else:
            response = {
                "found": True,
                "itemid": case.itemid,
                "appno": case.appno,
                "total_refs": len(mentions),
                "refs_with_appno": sum(1 for mention in mentions if mention.explicit_appnos),
                "citations": [mention.model_dump(mode="json") for mention in mentions],
            }
        if include_occurrences or include_target_paragraphs:
            from ..citations import extract_citation_occurrences

            occurrence_result = extract_citation_occurrences(
                case,
                resolution_result.resolutions if resolution_result else None,
                html=case.text,
                spine=case.sections.spine if case.sections else None,
                scope=citation_scope,
            )
            if include_target_paragraphs:
                from ..citations import resolve_occurrence_paragraphs

                target_ids = sorted(
                    {
                        value.target_itemid
                        for value in occurrence_result.occurrences
                        if value.target_itemid
                        and value.resolution_scope == "document"
                        and value.target_paragraphs
                    }
                )
                target_spines = {}
                target_languages = {}
                missing_targets = []
                for target_itemid in target_ids:
                    target_case = await main_aio.fetch_case(
                        itemid=target_itemid,
                        with_text=True,
                        text_format="html",
                        rich_sections=True,
                    )
                    if (
                        target_case is None
                        or target_case.sections is None
                        or target_case.sections.spine is None
                    ):
                        missing_targets.append(target_itemid)
                    else:
                        target_spines[target_itemid] = target_case.sections.spine
                        if target_case.language:
                            target_languages[target_itemid] = target_case.language
                occurrence_result = resolve_occurrence_paragraphs(
                    occurrence_result,
                    target_spines,
                    target_languages=target_languages,
                )
                occurrence_result.report.target_html_missing = len(missing_targets)
                occurrence_result.diagnostics.extend(
                    {"code": "missing_target_html", "target_itemid": value}
                    for value in missing_targets
                )
            response["occurrence_report"] = occurrence_result.report.model_dump(mode="json")
            response["occurrences"] = [
                value.model_dump(mode="json") for value in occurrence_result.occurrences
            ]
            response["occurrence_diagnostics"] = occurrence_result.diagnostics
            response["inclusive_edges"] = occurrence_result.inclusive_edges
            response["paragraph_edges"] = occurrence_result.paragraph_edges
        return response

    @server.tool(annotations=_tool_annotations("Build a case citation network"))
    async def get_case_citation_network(
        appno: str | None = None,
        itemid: str | None = None,
        max_refs: int = 200,
        citation_scope: str = "inclusive",
        top_targets: int = 40,
    ) -> dict[str, Any]:
        """Return a compact occurrence-weighted citation network.

        The server performs deterministic citation discovery and exact-document
        resolution internally, then aggregates occurrences from the majority,
        appendix, and each identified individual opinion to resolved target
        documents. The response contains a model-sized ``hudoc-graph/v1``
        bundle, summary counts, and the ten most-cited retained targets. It does
        not write files or alter the authoritative SCL graph.
        """
        if not (appno or itemid):
            raise ValueError("Provide appno or itemid")
        if citation_scope not in {"scl", "inclusive"}:
            raise ValueError("citation_scope must be 'scl' or 'inclusive'")
        if not 1 <= top_targets <= 100:
            raise ValueError("top_targets must be between 1 and 100")
        citation_response = await get_case_citations(
            appno=appno,
            itemid=itemid,
            max_refs=max_refs,
            resolve=True,
            include_occurrences=True,
            include_target_paragraphs=False,
            citation_scope=citation_scope,
        )
        if not citation_response.get("found"):
            return {"found": False}
        case = await main_aio.fetch_case(
            itemid=str(citation_response.get("itemid") or itemid or "") or None,
            with_text=False,
        )
        return _citation_network_response(
            citation_response,
            source_title=case.docname if case else None,
            top_targets=top_targets,
        )

    if job_manager is not None:

        @server.tool(
            annotations=_tool_annotations(
                "Create a bounded study job", read_only=False, idempotent=False
            )
        )
        async def create_study_job(spec_path: str) -> dict[str, Any]:
            """Create a bounded job from an allowlisted, path-confined installed study."""
            return job_manager.create(spec_path).model_dump(mode="json")

        @server.tool(annotations=_tool_annotations("Get a study job", open_world=False))
        async def get_study_job(job_id: str) -> dict[str, Any]:
            """Inspect one persistent study job."""
            return job_manager.get(job_id).model_dump(mode="json")

        @server.tool(annotations=_tool_annotations("List study jobs", open_world=False))
        async def list_study_jobs() -> list[dict[str, Any]]:
            """List persistent study jobs newest first."""
            return [job.model_dump(mode="json") for job in job_manager.list_jobs()]

        @server.tool(
            annotations=_tool_annotations(
                "Cancel a study job",
                read_only=False,
                destructive=True,
                idempotent=False,
                open_world=False,
            )
        )
        async def cancel_study_job(job_id: str) -> dict[str, Any]:
            """Request cooperative cancellation of a running study job."""
            return job_manager.cancel(job_id).model_dump(mode="json")

        @server.tool(
            annotations=_tool_annotations(
                "Resume a study job", read_only=False, idempotent=False
            )
        )
        async def resume_study_job(job_id: str) -> dict[str, Any]:
            """Resume a persisted interrupted, partial, failed, or cancelled job."""
            return job_manager.resume(job_id).model_dump(mode="json")

        @server.tool(annotations=_tool_annotations("List study artifacts", open_world=False))
        async def list_study_artifacts(job_id: str) -> list[dict[str, Any]]:
            """List artifact names and sizes without exposing arbitrary files."""
            return job_manager.artifacts(job_id)

    return server


def run(*, job_manager: Any | None = None) -> None:
    """Run the MCP server on stdio."""
    logging.basicConfig(level=logging.INFO)
    (server if job_manager is None else build_server(job_manager=job_manager)).run()


# Module-level instance so the official ``mcp install`` CLI can find the
# server with ``mcp install hudoc_py/mcp/server.py:server``. Building at
# import time is cheap – the tool decorators just register handlers.
server = build_server()
