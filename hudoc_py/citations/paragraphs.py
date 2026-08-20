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


def _paragraph_index(spine: DocumentSpine) -> dict[int, list[DocumentBlock]]:
    index: dict[int, list[DocumentBlock]] = defaultdict(list)
    for block in spine.blocks:
        # A citation to a judgment paragraph addresses the judgment proper,
        # not a same-numbered paragraph in an appended separate opinion.
        # Opinion-specific target citations can later opt into an opinion ID;
        # absent that explicit evidence, majority is the conservative scope.
        if (
            block.para_num is not None
            and block.type != "heading"
            and block.opinion_id is None
            and block.section != "appendix"
        ):
            index[block.para_num].append(block)
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
    selected: list[DocumentBlock] = []
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
        target_block_ids=[block.block_id for block in selected],
        target_para_ids=[block.para_id or block.block_id for block in selected],
        target_para_nums=[block.para_num for block in selected if block.para_num is not None],
        target_sections=list(dict.fromkeys(block.section for block in selected if block.section)),
        evidence={
            "requested_numbers": numbers,
            "missing_numbers": absent,
            "duplicate_numbers": duplicates,
            "target_spine_schema": spine.schema_version,
        },
    )


def _overall_status(resolutions: list[CitationParagraphResolution]) -> str:
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
        resolutions = [
            _resolve_label(occurrence, label, spine, target_language=language)
            for label in occurrence.target_paragraphs
        ]
        status = _overall_status(resolutions)
        if occurrence.target_paragraphs:
            pinpoint_occurrences += 1
            counts[status] += 1
        updated_occurrence = occurrence.model_copy(
            update={
                "target_paragraph_resolutions": resolutions,
                "paragraph_resolution_status": status,
            }
        )
        updated.append(updated_occurrence)
        target_blocks = {block.block_id: block for block in spine.blocks} if spine else {}
        for resolution in resolutions:
            for block_id, para_id, para_num in zip(
                resolution.target_block_ids,
                resolution.target_para_ids,
                resolution.target_para_nums,
                strict=False,
            ):
                target_block = target_blocks.get(block_id)
                edge_id = hashlib.sha256(
                    f"{occurrence.occurrence_id}|{target_key}|{block_id}|"
                    f"{resolution.printed_label}".encode()
                ).hexdigest()
                edges.append(
                    {
                        "paragraph_edge_id": edge_id,
                        "occurrence_id": occurrence.occurrence_id,
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
                        "target_para_id": para_id,
                        "target_para_num": para_num,
                        "target_section": target_block.section if target_block else None,
                        "target_text": target_block.text if target_block else None,
                        "target_language": language,
                        "printed_pinpoint": resolution.printed_label,
                        "mapping_status": resolution.status,
                        "target_html_sha256": checksums.get(target_key),
                    }
                )
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
