from __future__ import annotations

from hudoc_py.citations import resolve_occurrence_paragraphs
from hudoc_py.citations.models import (
    CitationOccurrence,
    CitationOccurrenceResult,
    CitationSourceInvocation,
)
from hudoc_py.text import build_spine_from_html


def _occurrence(identifier: str, labels: list[str]) -> CitationOccurrence:
    return CitationOccurrence(
        occurrence_id=identifier,
        mention_id=f"mention-{identifier}",
        source_itemid="001-source",
        source_block_id=f"source-{identifier}",
        source_para_id="8",
        source_para_num=8,
        block_start=0,
        block_end=5,
        document_start=0,
        document_end=5,
        raw_text="Case",
        source_context="Case",
        finder="full_name",
        target_node_id="item:001-target",
        target_itemid="001-target",
        target_paragraphs=labels,
        resolution_scope="document",
    )


def test_maps_each_occurrence_pinpoint_independently_to_target_paragraphs():
    target = build_spine_from_html(
        "<p>40. Forty.</p><p>41. Forty-one.</p><p>42. Forty-two.</p>",
        document_id="001-target",
    )
    result = CitationOccurrenceResult(
        occurrences=[_occurrence("unpinned", []), _occurrence("pinned", ["42"])]
    )

    mapped = resolve_occurrence_paragraphs(result, {"001-target": target})

    assert mapped.occurrences[0].paragraph_resolution_status == "not_requested"
    assert mapped.occurrences[0].target_paragraph_resolutions == []
    assert mapped.occurrences[1].paragraph_resolution_status == "resolved"
    assert mapped.occurrences[1].target_paragraph_resolutions[0].target_para_nums == [42]
    assert [edge["target_para_num"] for edge in mapped.paragraph_edges] == [42]


def test_target_pinpoint_groups_physical_continuations_as_one_legal_paragraph():
    target = build_spine_from_html(
        """
        <p>79. The target paragraph starts here.</p>
        <p>Its decisive reasoning continues in a second physical block.</p>
        <p>80. The next legal paragraph.</p>
        """,
        document_id="001-target",
    )

    mapped = resolve_occurrence_paragraphs(
        CitationOccurrenceResult(occurrences=[_occurrence("continuation", ["79"])]),
        {"001-target": target},
    )

    resolution = mapped.occurrences[0].target_paragraph_resolutions[0]
    assert resolution.status == "exact"
    assert resolution.target_para_ids == ["79"]
    assert resolution.target_para_nums == [79]
    assert resolution.target_block_ids == ["b000001"]
    assert resolution.target_block_groups == [["b000001", "b000002"]]
    assert len(mapped.paragraph_edges) == 1
    edge = mapped.paragraph_edges[0]
    assert edge["target_block_id"] == "b000001"
    assert edge["target_block_ids"] == ["b000001", "b000002"]
    assert edge["target_text"] == (
        "79. The target paragraph starts here.\n\n"
        "Its decisive reasoning continues in a second physical block."
    )


def test_continuation_blocks_do_not_make_a_repeated_number_ambiguous_by_themselves():
    target = build_spine_from_html(
        "<p>10. First.</p><p>Continuation of first.</p><p>11. Next.</p>",
        document_id="001-target",
    )

    mapped = resolve_occurrence_paragraphs(
        CitationOccurrenceResult(occurrences=[_occurrence("ten", ["10"])]),
        {"001-target": target},
    )

    resolution = mapped.occurrences[0].target_paragraph_resolutions[0]
    assert resolution.status == "exact"
    assert resolution.target_block_groups == [["b000001", "b000002"]]


def test_majority_and_source_opinion_may_cite_the_same_target_paragraph():
    target = build_spine_from_html(
        "<p>42. The cited judgment paragraph.</p>", document_id="001-target"
    )
    majority = _occurrence("majority", ["42"])
    opinion = _occurrence("opinion", ["42"])
    opinion.source_component = "opinion"
    opinion.source_opinion_id = "opinion:source:dissent:1"

    mapped = resolve_occurrence_paragraphs(
        CitationOccurrenceResult(occurrences=[majority, opinion]),
        {"001-target": target},
    )

    assert len(mapped.paragraph_edges) == 2
    assert {edge["source_component"] for edge in mapped.paragraph_edges} == {
        "majority",
        "opinion",
    }
    assert {edge["target_para_num"] for edge in mapped.paragraph_edges} == {42}


def test_footnote_edges_retain_identity_invoking_paragraph_and_target_context():
    target = build_spine_from_html(
        "<h2>THE LAW</h2><p>42. The cited judgment paragraph.</p>",
        document_id="001-target",
    )
    target.blocks[-1].section = "the_law"
    occurrence = _occurrence("footnote", ["42"])
    occurrence.source_footnote_id = "ftn2"
    occurrence.source_invoking_block_ids = ["source-paragraph-17"]
    occurrence.source_invoking_para_ids = ["17"]

    mapped = resolve_occurrence_paragraphs(
        CitationOccurrenceResult(occurrences=[occurrence]), {"001-target": target}
    )

    edge = mapped.paragraph_edges[0]
    assert edge["source_footnote_id"] == "ftn2"
    assert edge["source_invoking_block_ids"] == ["source-paragraph-17"]
    assert edge["source_invoking_para_ids"] == ["17"]
    assert edge["target_section"] == "the_law"
    assert edge["target_text"] == "42. The cited judgment paragraph."


def test_footnote_paragraph_edges_expand_each_structured_invocation():
    target = build_spine_from_html(
        "<p>42. The cited judgment paragraph.</p>", document_id="001-target"
    )
    occurrence = _occurrence("footnote-multiple", ["42"])
    occurrence.source_footnote_id = "ftn2"
    occurrence.source_invocations = [
        CitationSourceInvocation(
            source_block_id="majority-17",
            source_para_id="17",
            source_para_num=17,
            source_section="the_law",
            source_component="majority",
        ),
        CitationSourceInvocation(
            source_block_id="opinion-9",
            source_para_id="9-2",
            source_para_num=9,
            source_section="separate_opinion",
            source_component="opinion",
            source_opinion_id="opinion:2",
            source_opinion_ordinal=2,
            source_opinion_authors=["SMITH"],
        ),
    ]

    mapped = resolve_occurrence_paragraphs(
        CitationOccurrenceResult(occurrences=[occurrence]), {"001-target": target}
    )

    assert len(mapped.paragraph_edges) == 2
    assert {edge["source_block_id"] for edge in mapped.paragraph_edges} == {
        "majority-17",
        "opinion-9",
    }
    assert {edge["source_component"] for edge in mapped.paragraph_edges} == {
        "majority",
        "opinion",
    }
    assert all(
        edge["citation_source_block_id"] == "source-footnote-multiple"
        for edge in mapped.paragraph_edges
    )


def test_expands_full_and_abbreviated_ranges_without_cross_citation_leakage():
    target = build_spine_from_html(
        "".join(f"<p>{number}. Text.</p>" for number in range(139, 142)),
        document_id="001-target",
    )
    result = CitationOccurrenceResult(
        occurrences=[_occurrence("first", ["139-41"]), _occurrence("second", [])]
    )

    mapped = resolve_occurrence_paragraphs(result, {"001-target": target})

    resolution = mapped.occurrences[0].target_paragraph_resolutions[0]
    assert resolution.status == "range"
    assert resolution.target_para_nums == [139, 140, 141]
    assert mapped.occurrences[1].paragraph_resolution_status == "not_requested"


def test_reports_partial_ambiguous_and_unavailable_instead_of_guessing():
    target = build_spine_from_html(
        "<p>10. First.</p><p>10. Repeated.</p><p>11. Present.</p>",
        document_id="001-target",
    )
    exact = CitationOccurrenceResult(occurrences=[_occurrence("partial", ["11-12"])])
    ambiguous = CitationOccurrenceResult(occurrences=[_occurrence("ambiguous", ["10"])])
    unavailable_occurrence = _occurrence("unavailable", ["11"])
    unavailable_occurrence.resolution_scope = "application"

    assert resolve_occurrence_paragraphs(
        exact, {"001-target": target}
    ).occurrences[0].paragraph_resolution_status == "partial"
    assert resolve_occurrence_paragraphs(
        ambiguous, {"001-target": target}
    ).occurrences[0].paragraph_resolution_status == "ambiguous"
    unavailable = resolve_occurrence_paragraphs(
        CitationOccurrenceResult(occurrences=[unavailable_occurrence]),
        {"001-target": target},
    )
    assert unavailable.occurrences[0].paragraph_resolution_status == "unavailable"
    assert unavailable.paragraph_edges == []
