"""Resolve printed citation pinpoints against cited-document spines."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Mapping
from typing import Literal

from ..models.common import DocumentBlock, DocumentSpine
from .models import (
    CitationOccurrence,
    CitationOccurrenceResult,
    CitationParagraphResolution,
)

_RANGE_RE = re.compile(r"^\s*(\d{1,4})\s*[-–\u2014]\s*(\d{1,4})\s*$")
_NUMBER_RE = re.compile(r"^\s*(\d{1,4})\s*$")

# Paragraph hydration is deliberately downstream of citation detection and
# document resolution. Keeping the exclusion list here makes the contract
# executable: these are the only occurrence fields this module may change.
_PARAGRAPH_HYDRATION_FIELDS = frozenset(
    {"target_paragraph_resolutions", "paragraph_resolution_status"}
)


def _detection_identity(occurrence: CitationOccurrence) -> dict[str, object]:
    """Return every occurrence field that paragraph hydration must preserve."""
    return {
        key: value
        for key, value in occurrence.model_dump(mode="json").items()
        if key not in _PARAGRAPH_HYDRATION_FIELDS
    }


def _numbers(label: str) -> tuple[list[int], bool] | None:
    """Expand an exact/range label, including conventional ``139-41`` shorthand."""
    exact = _NUMBER_RE.fullmatch(label)
    if exact:
        return [int(exact.group(1))], False
    match = _RANGE_RE.fullmatch(label)
    if not match:
        return None
    start, printed_end = int(match.group(1)), match.group(2)
    if len(printed_end) < len(str(start)):
        prefix = str(start)[: len(str(start)) - len(printed_end)]
        end = int(prefix + printed_end)
        if end < start:
            end += 10 ** len(printed_end)
    else:
        end = int(printed_end)
    if end < start or end - start > 250:
        return None
    return list(range(start, end + 1)), True


def _paragraph_index(spine: DocumentSpine) -> dict[int, list[list[DocumentBlock]]]:
    groups: dict[str, list[DocumentBlock]] = {}
    for block in spine.blocks:
        # A citation to a judgment paragraph addresses the judgment proper,
        # not a same-numbered paragraph in an appended separate opinion.
        # Opinion-specific target citations can later opt into an opinion ID;
        # absent that explicit evidence, majority is the conservative scope.
        legal_para_num = (
            block.legal_para_num
            if block.legal_para_id is not None
            else block.para_num
        )
        legal_para_id = block.legal_para_id or block.para_id
        if (
            legal_para_num is not None
            and legal_para_id is not None
            and block.type != "heading"
            and block.opinion_id is None
            and block.section != "appendix"
        ):
            groups.setdefault(legal_para_id, []).append(block)
    index: dict[int, list[list[DocumentBlock]]] = defaultdict(list)
    for blocks in groups.values():
        first = blocks[0]
        number = (
            first.legal_para_num
            if first.legal_para_id is not None
            else first.para_num
        )
        if number is not None:
            index[number].append(blocks)
    return dict(index)


def _target_spine(
    occurrence: CitationOccurrence,
    spines: Mapping[str, DocumentSpine],
) -> DocumentSpine | None:
    for key in (occurrence.target_itemid, occurrence.target_node_id, occurrence.target_ecli):
        if key and key in spines:
            return spines[key]
    return None


def _resolve_label(
    occurrence: CitationOccurrence,
    label: str,
    spine: DocumentSpine | None,
    *,
    target_language: str | None,
) -> CitationParagraphResolution:
    if occurrence.resolution_scope != "document" or spine is None:
        return CitationParagraphResolution(
            printed_label=label,
            status="unavailable",
            target_itemid=occurrence.target_itemid,
            target_ecli=occurrence.target_ecli,
            target_language=target_language,
        )
    parsed = _numbers(label)
    if parsed is None:
        return CitationParagraphResolution(
            printed_label=label,
            status="missing",
            target_itemid=occurrence.target_itemid,
            target_ecli=occurrence.target_ecli,
            target_language=target_language,
            evidence={"reason": "unsupported_label"},
        )
    numbers, is_range = parsed
    index = _paragraph_index(spine)
    selected: list[list[DocumentBlock]] = []
    absent: list[int] = []
    duplicates: list[int] = []
    for number in numbers:
        matches = index.get(number, [])
        if not matches:
            absent.append(number)
        elif len(matches) > 1:
            duplicates.append(number)
        else:
            selected.append(matches[0])
    if duplicates:
        status: Literal["exact", "range", "partial", "ambiguous", "missing"] = "ambiguous"
    elif absent and selected:
        status = "partial"
    elif absent:
        status = "missing"
    else:
        status = "range" if is_range else "exact"
    return CitationParagraphResolution(
        printed_label=label,
        status=status,
        target_itemid=occurrence.target_itemid,
        target_ecli=occurrence.target_ecli,
        target_language=target_language,
        target_block_ids=[blocks[0].block_id for blocks in selected],
        target_block_groups=[
            [block.block_id for block in blocks]
            for blocks in selected
        ],
        target_para_ids=[
            blocks[0].legal_para_id or blocks[0].para_id or blocks[0].block_id
            for blocks in selected
        ],
        target_para_nums=[
            para_num
            for blocks in selected
            if (
                para_num := (
                    blocks[0].legal_para_num
                    if blocks[0].legal_para_id is not None
                    else blocks[0].para_num
                )
            ) is not None
        ],
        target_sections=list(dict.fromkeys(
            block.section
            for blocks in selected
            for block in blocks
            if block.section
        )),
        evidence={
            "requested_numbers": numbers,
            "missing_numbers": absent,
            "duplicate_numbers": duplicates,
            "target_spine_schema": spine.schema_version,
        },
    )


def _overall_status(
    resolutions: list[CitationParagraphResolution],
) -> Literal[
    "not_requested", "resolved", "partial", "ambiguous", "missing", "unavailable"
]:
    statuses = {value.status for value in resolutions}
    if not statuses:
        return "not_requested"
    if "ambiguous" in statuses:
        return "ambiguous"
    if "partial" in statuses or ({"exact", "range"} & statuses and "missing" in statuses):
        return "partial"
    if statuses <= {"exact", "range"}:
        return "resolved"
    if statuses == {"unavailable"}:
        return "unavailable"
    if "missing" in statuses:
        return "missing"
    return "unavailable"


def _sort_number(value: object) -> int:
    return int(value) if isinstance(value, (int, str)) and value else -1


def resolve_occurrence_paragraphs(
    result: CitationOccurrenceResult,
    target_spines: Mapping[str, DocumentSpine],
    *,
    target_languages: Mapping[str, str] | None = None,
    target_checksums: Mapping[str, str] | None = None,
) -> CitationOccurrenceResult:
    """Map every occurrence's independently owned pinpoints to target blocks.

    Document-level citation resolution is a prerequisite. Application-level
    and unresolved targets are retained as ``unavailable`` and never create a
    paragraph edge.
    """
    languages = target_languages or {}
    checksums = target_checksums or {}
    updated: list[CitationOccurrence] = []
    edges: list[dict[str, object]] = []
    counts: dict[str, int] = defaultdict(int)
    pinpoint_occurrences = 0
    for occurrence in result.occurrences:
        spine = _target_spine(occurrence, target_spines)
        target_key = occurrence.target_itemid or occurrence.target_node_id or ""
        language = languages.get(target_key)
        status: Literal[
            "not_requested",
            "resolved",
            "partial",
            "ambiguous",
            "missing",
            "unavailable",
        ]
        # A partial/offline hydration pass must not erase a mapping produced by
        # an earlier pass. Missing target HTML is absence of new evidence, not
        # evidence that the document-level target (or its paragraph mapping) is
        # wrong.
        if spine is None and occurrence.target_paragraph_resolutions:
            resolutions = occurrence.target_paragraph_resolutions
            status = occurrence.paragraph_resolution_status
        else:
            resolutions = [
                _resolve_label(occurrence, label, spine, target_language=language)
                for label in occurrence.target_paragraphs
            ]
            status = _overall_status(resolutions)
        if occurrence.target_paragraphs:
            pinpoint_occurrences += 1
            counts[status] += 1
        identity = _detection_identity(occurrence)
        updated_occurrence = occurrence.model_copy(
            update={
                "target_paragraph_resolutions": resolutions,
                "paragraph_resolution_status": status,
            }
        )
        if _detection_identity(updated_occurrence) != identity:  # pragma: no cover
            raise AssertionError("paragraph hydration changed citation detection identity")
        updated.append(updated_occurrence)
        target_blocks = {block.block_id: block for block in spine.blocks} if spine else {}
        source_rows: list[dict[str, object]] = [{}]
        if occurrence.source_footnote_id and occurrence.source_invocations:
            source_rows = [
                {
                    "citation_source_block_id": occurrence.source_block_id,
                    "source_block_id": invocation.source_block_id,
                    "source_para_id": invocation.source_para_id,
                    "source_para_num": invocation.source_para_num,
                    "source_section": invocation.source_section,
                    "source_component": invocation.source_component,
                    "source_opinion_id": invocation.source_opinion_id,
                    "source_opinion_ordinal": invocation.source_opinion_ordinal,
                    "source_opinion_type": invocation.source_opinion_type,
                    "source_opinion_authors": invocation.source_opinion_authors,
                    "source_opinion_joined_by": invocation.source_opinion_joined_by,
                    "source_invocation_ordinal": ordinal,
                }
                for ordinal, invocation in enumerate(occurrence.source_invocations, 1)
            ]
        for resolution in resolutions:
            for group_ordinal, (block_id, para_id, para_num) in enumerate(zip(
                resolution.target_block_ids,
                resolution.target_para_ids,
                resolution.target_para_nums,
                strict=False,
            )):
                target_block = target_blocks.get(block_id)
                target_block_ids = (
                    resolution.target_block_groups[group_ordinal]
                    if group_ordinal < len(resolution.target_block_groups)
                    else [block_id]
                )
                target_text = "\n\n".join(
                    target_blocks[value].text
                    for value in target_block_ids
                    if value in target_blocks
                )
                for source_row in source_rows:
                    source_address = str(
                        source_row.get("source_block_id", occurrence.source_block_id)
                    )
                    edge_id = hashlib.sha256(
                        f"{occurrence.occurrence_id}|{source_address}|{target_key}|{block_id}|"
                        f"{resolution.printed_label}".encode()
                    ).hexdigest()
                    row: dict[str, object] = {
                        "paragraph_edge_id": edge_id,
                        "occurrence_id": occurrence.occurrence_id,
                        "locus_id": occurrence.locus_id,
                        "citation_group_id": occurrence.citation_group_id,
                        "mention_id": occurrence.mention_id,
                        "source_itemid": occurrence.source_itemid,
                        "source_block_id": occurrence.source_block_id,
                        "source_para_id": occurrence.source_para_id,
                        "source_para_num": occurrence.source_para_num,
                        "source_component": occurrence.source_component,
                        "source_opinion_id": occurrence.source_opinion_id,
                        "source_footnote_id": occurrence.source_footnote_id,
                        "source_invoking_block_ids": occurrence.source_invoking_block_ids,
                        "source_invoking_para_ids": occurrence.source_invoking_para_ids,
                        "target_node_id": occurrence.target_node_id,
                        "target_itemid": occurrence.target_itemid,
                        "target_ecli": occurrence.target_ecli,
                        "target_block_id": block_id,
                        "target_block_ids": target_block_ids,
                        "target_para_id": para_id,
                        "target_para_num": para_num,
                        "target_section": target_block.section if target_block else None,
                        "target_text": target_text or (
                            target_block.text if target_block else None
                        ),
                        "target_language": language,
                        "printed_pinpoint": resolution.printed_label,
                        "mapping_status": resolution.status,
                        "target_html_sha256": checksums.get(target_key),
                    }
                    row.update(source_row)
                    edges.append(row)
    report = result.report.model_copy(
        update={
            "pinpoint_occurrences": pinpoint_occurrences,
            "paragraph_resolved_occurrences": counts["resolved"],
            "paragraph_partial_occurrences": counts["partial"],
            "paragraph_ambiguous_occurrences": counts["ambiguous"],
            "paragraph_missing_occurrences": counts["missing"],
            "paragraph_unavailable_occurrences": counts["unavailable"],
        }
    )
    return result.model_copy(
        update={
            "occurrences": updated,
            "paragraph_edges": sorted(
                edges,
                key=lambda row: (
                    str(row["source_itemid"]),
                    _sort_number(row["source_para_num"]),
                    str(row["occurrence_id"]),
                    _sort_number(row["target_para_num"]),
                ),
            ),
            "report": report,
        }
    )
