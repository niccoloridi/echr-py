from __future__ import annotations

import asyncio

import pytest

import hudoc_py.citations.occurrences as occurrence_module
from hudoc_py.citations import (
    discover_citation_mentions,
    extract_citation_occurrences,
    load_resolutions,
    parse_scl_mentions,
    resolve_citations,
    write_resolution_artifacts,
)
from hudoc_py.citations.models import (
    CitationCandidate,
    CitationOccurrence,
    CitationResolution,
    CitationResolutionReport,
    CitationResolutionResult,
)
from hudoc_py.models import Case, DocumentBlock, DocumentSpine


def _resolved(case: Case, targets: list[tuple[str, str]]) -> list[CitationResolution]:
    mentions = parse_scl_mentions(case)
    return [
        CitationResolution(
            mention=mention,
            status="resolved_metadata",
            method="fixture",
            target=CitationCandidate(
                node_id=node_id,
                itemid=f"001-target-{index}",
                docname=title,
                appnos=mention.explicit_appnos,
            ),
        )
        for index, (mention, (node_id, title)) in enumerate(
            zip(mentions, targets, strict=True), start=1
        )
    ]


def test_locates_full_and_short_form_with_source_and_target_paragraphs():
    case = Case(
        itemid="001-source",
        languageisocode="ENG",
        scl=(
            "Soering v. the United Kingdom, 7 July 1989, § 88, Series A no. 161;"
            "Öcalan v. Turkey [GC], no. 46221/99, §§ 10-12 and 15, ECHR 2005-IV"
        ),
    )
    resolutions = _resolved(
        case,
        [("ecli:soering", "Soering v. the United Kingdom"), ("ecli:ocalan", "Öcalan v. Turkey")],
    )
    html = """
    <p>1. The Court relied on Soering v. the United Kingdom, 7 July 1989,
    § 88, Series A no. 161 and on Öcalan v. Turkey [GC], no. 46221/99,
    §§ 10-12 and 15, ECHR 2005-IV.</p>
    <p>2. It later followed <em>Soering</em>, § 42.</p>
    """

    result = extract_citation_occurrences(case, resolutions, html=html)

    assert result.report.scl_mentions == 2
    assert result.report.located_mentions == 2
    assert result.report.occurrences == 3
    short = next(value for value in result.occurrences if value.raw_text == "Soering")
    assert short.source_para_id == "2"
    assert short.source_para_num == 2
    assert short.italic is True
    assert short.target_node_id == "ecli:soering"
    assert short.target_paragraphs == ["42"]
    assert short.source_context == "2. It later followed Soering, § 42."
    assert short.evidence["resolution_method"] == "fixture"
    ocalan = next(value for value in result.occurrences if "Öcalan v." in value.raw_text)
    assert ocalan.source_para_id == "1"
    assert ocalan.target_paragraphs == ["10-12", "15"]
    assert html is not None  # keeps this fixture visibly HTML-backed


def test_citation_in_unnumbered_continuation_uses_owning_legal_paragraph():
    case = Case(
        itemid="001-nikolova-style",
        languageisocode="ENG",
        scl="Hood v. the United Kingdom [GC], no. 27267/95, ECHR 1999-I",
    )
    html = """
    <p>79. The Court begins its assessment in this physical block.</p>
    <p>That conclusion also follows from <em>Hood v. the United Kingdom</em>,
    cited above, § 60.</p>
    <p>80. The Court therefore finds a violation.</p>
    """

    result = extract_citation_occurrences(
        case,
        _resolved(case, [("ecli:hood", "Hood v. the United Kingdom")]),
        html=html,
    )

    occurrence = next(
        value for value in result.occurrences if value.raw_text == "Hood v. the United Kingdom"
    )
    assert occurrence.source_block_id == "b000002"
    assert occurrence.source_para_id == "79"
    assert occurrence.source_para_num == 79
    assert occurrence.source_context.startswith("That conclusion")
    assert occurrence.target_paragraphs == ["60"]


def test_inline_spacing_and_covered_discovery_resolutions_preserve_repeated_cites():
    case = Case(
        itemid="001-source",
        languageisocode="ENG",
        scl=(
            "Ukraine v. Russia (re Crimea) (dec.) [GC], nos. 20958/14 "
            "and 38334/18, 16 December 2020"
        ),
    )
    scl_resolution = _resolved(
        case,
        [("ecli:crimea", "Ukraine v. Russia (re Crimea)")],
    )[0]
    html = """
    <p>1. See Ukraine v. Russia (<em>re Crimea</em>) (dec.) [GC],
    nos. 20958/14 and 38334/18, 16 December 2020.</p>
    <p>2. The Court followed <em>Ukraine v. Russia
    (<span>re Crimea</span>)</em>, cited above, § 257.</p>
    <p>3. It again followed <em>Ukraine v. Russia (re Crimea)</em>,
    cited above, § 383.</p>
    """
    discovery = discover_citation_mentions(case, html=html)
    duplicate = next(
        mention
        for mention in discovery.mentions
        if mention.explicit_appnos and mention.origin == "text_discovery"
    )
    unresolved_duplicate = CitationResolution(
        mention=duplicate,
        status="unresolved_reference",
        method="fixture unresolved duplicate",
    )

    result = extract_citation_occurrences(
        case,
        [scl_resolution, unresolved_duplicate],
        html=html,
        scope="inclusive",
    )

    crimea = [value for value in result.occurrences if "Ukraine v. Russia" in value.raw_text]
    assert len(crimea) == 3
    assert [value.target_paragraphs for value in crimea] == [[], ["257"], ["383"]]
    assert all(value.target_node_id == "ecli:crimea" for value in crimea)
    assert not any(
        value.get("code") == "ambiguous_alias"
        and "crimea" in str(value.get("raw_text", "")).casefold()
        for value in result.diagnostics
    )


def test_cached_v1_spine_spacing_inside_delimiters_remains_searchable():
    case = Case(
        itemid="001-source",
        languageisocode="ENG",
        scl="Ukraine v. Russia (re Crimea), nos. 20958/14 and 38334/18",
        text=(
            "THE LAW\n\n"
            "1. See Ukraine v. Russia ( re Crimea ), cited above, § 257.\n"
            "2. Ukraine v. Russia re Crimea is deliberately not delimited."
        ),
    )

    result = extract_citation_occurrences(
        case,
        _resolved(case, [("ecli:crimea", "Ukraine v. Russia (re Crimea)")]),
    )

    crimea = [value for value in result.occurrences if "Crimea" in value.raw_text]
    assert [value.raw_text for value in crimea] == ["Ukraine v. Russia ( re Crimea )"]
    assert crimea[0].target_paragraphs == ["257"]


def test_cached_v1_alias_spacing_matches_canonical_spine_text():
    pattern = occurrence_module._pattern("Ukraine v. Russia ( re Crimea )")

    match = pattern.search("See Ukraine v. Russia (re Crimea), cited above.")
    assert match is not None
    assert match.group() == "Ukraine v. Russia (re Crimea)"


def test_coverage_does_not_fold_unresolved_decision_into_resolved_judgment():
    case = Case(
        itemid="001-source",
        languageisocode="ENG",
        scl="Example v. France, no. 12345/67, judgment of 2 January 2020",
        text=("THE LAW\n\n1. Example v. France (dec.), no. 12345/67, 3 January 2020."),
    )
    scl_resolution = _resolved(
        case,
        [("ecli:example-judgment", "Example v. France")],
    )[0]
    scl_resolution.target.document_kind = "judgment"
    scl_resolution.target.procedural_phase = "merits"
    discovery = discover_citation_mentions(case)
    discovered = next(
        mention for mention in discovery.mentions if mention.origin == "text_discovery"
    )
    unresolved_decision = CitationResolution(
        mention=discovered,
        status="unresolved_reference",
        method="fixture unresolved decision",
    )

    result = extract_citation_occurrences(
        case,
        [scl_resolution, unresolved_decision],
        scope="inclusive",
    )

    decision = next(value for value in result.occurrences if "(dec.)" in value.raw_text)
    assert decision.scl_coverage == "not_covered"
    assert decision.target_node_id is None


def test_alias_token_prefilter_is_identical_to_exhaustive_scan(monkeypatch):
    case = Case(
        itemid="001-source",
        languageisocode="ENG",
        scl="Šilih v. Slovenia, no. 71463/01, § 20",
        text=(
            "THE LAW\n1. Šilih v. Slovenia, no. 71463/01, § 20.\n"
            "2. The Court later followed Šilih, § 42.\n"
            "3. An unrelated paragraph with 71463-like prose."
        ),
    )
    resolutions = _resolved(case, [("ecli:silih", "Šilih v. Slovenia")])

    filtered = extract_citation_occurrences(case, resolutions)
    monkeypatch.setattr(occurrence_module, "_anchor_token", lambda _value: None)
    exhaustive = extract_citation_occurrences(case, resolutions)

    assert filtered.model_dump(mode="json") == exhaustive.model_dump(mode="json")


def test_printed_loci_are_identical_with_resolved_or_unresolved_targets():
    case = Case(
        itemid="001-source",
        languageisocode="ENG",
        scl="Šilih v. Slovenia, no. 71463/01, § 20",
        text=(
            "THE LAW\n1. Šilih v. Slovenia, no. 71463/01, § 20.\n"
            "2. The Court later followed Šilih, § 42."
        ),
    )
    resolved = _resolved(case, [("ecli:silih", "Šilih v. Slovenia")])[0]
    unresolved = resolved.model_copy(
        deep=True,
        update={
            "status": "unresolved_reference",
            "method": "fixture unresolved",
            "target": None,
            "candidates": [resolved.target],
        },
    )

    with_target = extract_citation_occurrences(case, [resolved], scope="inclusive")
    without_target = extract_citation_occurrences(case, [unresolved], scope="inclusive")

    assert {value.locus_id for value in with_target.occurrences} == {
        value.locus_id for value in without_target.occurrences
    }
    assert [
        (value.locus_id, value.raw_text, value.block_start, value.block_end)
        for value in with_target.occurrences
    ] == [
        (value.locus_id, value.raw_text, value.block_start, value.block_end)
        for value in without_target.occurrences
    ]


@pytest.mark.parametrize(
    ("authority", "printed"),
    [
        ("YILDIRIM", "Yıldırım"),
        ("SILIH", "SİLİH"),
        ("SILIH", "ſILIH"),
        ("KIRK", "KIRK"),
    ],
)
def test_alias_prefilter_preserves_unicode_ignorecase_equivalents(monkeypatch, authority, printed):
    case = Case(
        itemid="001-source",
        languageisocode="ENG",
        scl=f"{authority} v. Turkey, no. 12345/67",
        text=f"THE LAW\n1. See {printed} v. Turkey, no. 12345/67.",
    )
    resolutions = _resolved(case, [(f"ecli:{authority.lower()}", f"{authority} v. Turkey")])

    filtered = extract_citation_occurrences(case, resolutions)
    monkeypatch.setattr(occurrence_module, "_anchor_token", lambda _value: None)
    exhaustive = extract_citation_occurrences(case, resolutions)

    assert len(filtered.occurrences) == 1
    assert filtered.model_dump(mode="json") == exhaustive.model_dump(mode="json")


def test_plain_short_form_without_cue_or_prior_anchor_is_rejected():
    case = Case(itemid="001-source", scl="Soering v. the United Kingdom, 7 July 1989")
    resolutions = _resolved(case, [("ecli:soering", "Soering v. the United Kingdom")])
    text = """1. Soering attended the meeting.

2. Soering v. the United Kingdom, 7 July 1989 was cited."""

    result = extract_citation_occurrences(case, resolutions, spine=None, html=None)
    assert result.report.occurrences == 0

    case.text = text
    result = extract_citation_occurrences(case, resolutions)
    assert len(result.occurrences) == 1
    assert result.occurrences[0].source_para_id == "2"


def test_text_discovery_preserves_source_footnote_identity():
    case = Case(itemid="001-source", languageisocode="ENG")
    html = """
    <p>THE LAW</p>
    <p>10. The Court follows the established rule<a href="#_ftn1">[1]</a>.</p>
    <div id="_ftn1"><p>[1] See Soering v. the United Kingdom,
    no. 14038/88, § 88.</p></div>
    """
    discovery = discover_citation_mentions(case, html=html)
    occurrence = next(
        value for value in discovery.preliminary_occurrences if "Soering" in value.raw_text
    )
    mention = next(
        value for value in discovery.mentions if value.mention_id == occurrence.mention_id
    )
    assert occurrence.source_footnote_id == "ftn1"
    assert mention.source_footnote_id == "ftn1"
    assert occurrence.source_invoking_para_ids == ["10"]
    assert mention.source_invoking_para_ids == ["10"]
    assert occurrence.source_section == "the_law"
    assert occurrence.source_component == "majority"
    assert len(occurrence.source_invocations) == 1
    assert occurrence.source_invocations[0].source_para_id == "10"
    assert occurrence.source_invocations[0].source_component == "majority"


def test_footnote_invocation_from_continuation_uses_owning_legal_paragraph():
    case = Case(itemid="001-source", languageisocode="ENG")
    html = """
    <p>THE LAW</p>
    <p>79. The Court begins its assessment.</p>
    <p>The reasoning continues here<a href="#_ftn1">[1]</a>.</p>
    <p>80. The next paragraph.</p>
    <div id="_ftn1"><p>[1] See Soering v. the United Kingdom,
    no. 14038/88, § 88.</p></div>
    """

    discovery = discover_citation_mentions(case, html=html)
    occurrence = next(
        value for value in discovery.preliminary_occurrences if "Soering" in value.raw_text
    )

    assert occurrence.source_invoking_block_ids == ["b000003"]
    assert occurrence.source_invoking_para_ids == ["79"]
    assert len(occurrence.source_invocations) == 1
    invocation = occurrence.source_invocations[0]
    assert invocation.source_block_id == "b000003"
    assert invocation.source_para_id == "79"
    assert invocation.source_para_num == 79
    assert occurrence.source_component == "majority"


def test_resolution_never_changes_detected_loci_or_occurrence_ids():
    case = Case(
        itemid="001-source",
        languageisocode="ENG",
        scl=("Assenov and Others v. Bulgaria judgment of 28 October 1998, Reports 1998-VIII"),
        text=(
            "THE LAW\n\n42. Assenov and Others v. Bulgaria (judgment of "
            "28 October 1998, Reports 1998-VIII).\n\n"
            "50. The Assenov and Others judgment cited above was followed."
        ),
    )
    unresolved = extract_citation_occurrences(case, scope="inclusive")
    resolved = extract_citation_occurrences(
        case,
        _resolved(case, [("ecli:assenov", "Assenov and Others v. Bulgaria")]),
        scope="inclusive",
    )

    def address(value):
        return (
            value.source_block_id,
            value.block_start,
            value.block_end,
            value.raw_text,
            value.locus_id,
            value.occurrence_id,
        )

    assert [address(value) for value in unresolved.occurrences] == [
        address(value) for value in resolved.occurrences
    ]
    assert all(value.schema_version == "citation-occurrence/v3" for value in resolved.occurrences)


def test_accent_insensitive_alias_retains_exact_printed_span():
    case = Case(
        itemid="001-source",
        scl="Stanciulescu v. Romania (no. 2), no. 14621/06",
        text=("THE LAW\n\n1. See Stănciulescu v. Romania (no. 2), cited above, § 44."),
    )

    result = extract_citation_occurrences(case)

    assert [(value.raw_text, value.target_paragraphs) for value in result.occurrences] == [
        ("Stănciulescu v. Romania (no. 2)", ["44"])
    ]


def test_name_alias_accepts_space_hyphen_orthography_without_changing_raw_span():
    case = Case(
        itemid="001-source",
        scl="Al Skeini and Others v. the United Kingdom, no. 55721/07",
        text="THE LAW\n\n1. Al-Skeini and Others, cited above, § 138.",
    )

    result = extract_citation_occurrences(case)

    assert [(value.raw_text, value.target_paragraphs) for value in result.occurrences] == [
        ("Al-Skeini and Others", ["138"])
    ]


def test_name_alias_accepts_replacement_character_for_broken_legacy_hyphen():
    case = Case(
        itemid="001-source",
        scl="Al-Skeini and Others v. the United Kingdom, no. 55721/07",
        text="THE LAW\n\n1. Al�Skeini and Others, cited above, § 138.",
    )

    result = extract_citation_occurrences(case)

    assert [(value.raw_text, value.target_paragraphs) for value in result.occurrences] == [
        ("Al�Skeini and Others", ["138"])
    ]


def test_parenthesised_appno_envelope_stops_before_following_authority():
    case = Case(
        itemid="001-source",
        scl=(
            "Ilgar Mammadov v. Azerbaijan, no. 15172/13, 29 May 2019;"
            "Kavala v. Türkiye, no. 28749/18, 10 December 2019"
        ),
        text=(
            "THE LAW\n\n146. See Ilgar Mammadov v. Azerbaijan "
            "(no. 15172/13, 29 May 2019), which the Kavala judgment followed."
        ),
    )

    discovery = discover_citation_mentions(case)
    ilgar = next(value for value in discovery.mentions if "Ilgar" in (value.cited_name or ""))
    result = extract_citation_occurrences(case, scope="inclusive")

    assert ilgar.raw_ref.endswith("29 May 2019)")
    assert "Kavala" not in ilgar.raw_ref
    assert any(value.raw_text.startswith("Ilgar Mammadov") for value in result.occurrences)
    assert not any(
        value.get("code") == "overlapping_distinct_authorities" for value in result.diagnostics
    )


def test_parenthesised_see_envelope_does_not_swallow_a_later_back_reference():
    result = extract_citation_occurrences(
        Case(itemid="001-source"),
        html=(
            "<p>1. The rule did not apply (see Rasmussen v. Poland, "
            "no. 38886/05, § 71, 28 April 2009) where the conditions changed "
            "(see Richardson, cited above, § 17).</p>"
        ),
        scope="inclusive",
    )

    assert [(value.raw_text, value.target_paragraphs) for value in result.occurrences] == [
        ("Rasmussen v. Poland, no. 38886/05, § 71, 28 April 2009)", ["71"]),
        ("Richardson, cited above, § 17)", ["17"]),
    ]


def test_grouped_procedural_documents_share_locus_and_own_pinpoints():
    case = Case(
        itemid="001-source",
        text=(
            "THE LAW\n\n1. The Loizidou judgments "
            "(preliminary objections and merits), at § 64 and § 56 respectively."
        ),
    )

    discovery = discover_citation_mentions(case)
    occurrences = discovery.preliminary_occurrences

    assert [value.group_ordinal for value in occurrences] == [1, 2]
    assert {value.group_size for value in occurrences} == {2}
    assert len({value.locus_id for value in occurrences}) == 1
    assert len({value.citation_group_id for value in occurrences}) == 1
    assert [value.target_paragraphs for value in occurrences] == [["64"], ["56"]]
    assert [value.raw_text for value in occurrences] == [occurrences[0].raw_text] * 2


def test_grouped_procedural_rows_receive_distinct_resolved_documents():
    case = Case(
        itemid="001-source",
        scl=(
            "Loizidou v. Turkey judgment (preliminary objections), 23 March 1995;"
            "Loizidou v. Turkey judgment (merits), 18 December 1996"
        ),
        text=(
            "THE LAW\n\n1. The Loizidou judgments "
            "(preliminary objections and merits), at § 64 and § 56 respectively."
        ),
    )
    resolutions = _resolved(
        case,
        [
            ("ecli:loizidou-preliminary", "Loizidou v. Turkey"),
            ("ecli:loizidou-merits", "Loizidou v. Turkey"),
        ],
    )
    resolutions[0].target.procedural_phase = "preliminary_objections"
    resolutions[1].target.procedural_phase = "merits"

    result = extract_citation_occurrences(case, resolutions, scope="inclusive")
    grouped = [value for value in result.occurrences if value.group_size == 2]

    assert len(grouped) == 2
    assert [value.group_ordinal for value in grouped] == [1, 2]
    assert [value.target_node_id for value in grouped] == [
        "ecli:loizidou-preliminary",
        "ecli:loizidou-merits",
    ]
    assert [value.target_paragraphs for value in grouped] == [["64"], ["56"]]
    assert len({value.locus_id for value in grouped}) == 1


def test_name_date_series_a_without_judgment_word_is_one_strong_envelope():
    case = Case(
        itemid="001-source",
        text=(
            "THE LAW\n\n1. See Engel and Others v. the Netherlands, "
            "8 June 1976, § 68, Series A no. 22."
        ),
    )

    discovery = discover_citation_mentions(case)

    assert len(discovery.mentions) == 1
    assert discovery.mentions[0].cited_name == "Engel and Others v. the Netherlands"
    assert discovery.mentions[0].discovery_evidence["method"] == ("historical_name_date_reporter")
    assert discovery.preliminary_occurrences[0].target_paragraphs == ["68"]


def test_historical_multi_initial_party_is_not_treated_as_reporter_noise():
    discovery = discover_citation_mentions(
        Case(
            itemid="001-source",
            text=(
                "THE LAW\n\n1. See the X, Y and Z v. the United Kingdom "
                "judgment of 22 April 1997, Reports 1997-II."
            ),
        )
    )

    assert [value.cited_name for value in discovery.mentions] == [
        "X, Y and Z v. the United Kingdom"
    ]


def test_ambiguous_local_short_form_is_reported_not_guessed():
    case = Case(
        itemid="001-source",
        scl="Smith v. France, no. 11111/11; Smith v. Germany, no. 22222/22",
    )
    resolutions = _resolved(
        case,
        [("ecli:smith-fr", "Smith v. France"), ("ecli:smith-de", "Smith v. Germany")],
    )
    html = "<p>1. The Court followed <em>Smith</em>, § 9.</p>"

    result = extract_citation_occurrences(case, resolutions, html=html)

    assert result.occurrences == []
    assert result.report.ambiguous_hits == 1
    assert result.diagnostics[0]["code"] == "ambiguous_alias"


def test_unresolved_scl_entry_can_still_be_located():
    case = Case(
        itemid="001-source",
        languageisocode="FRE",
        scl="Dupont c. France, no. 12345/12, § 7",
    )
    html = "<p>1. Voir Dupont c. France, no. 12345/12, § 7.</p>"

    result = extract_citation_occurrences(case, html=html)

    assert result.report.located_mentions == 1
    assert result.occurrences[0].target_node_id is None
    assert result.occurrences[0].resolved is False
    assert result.occurrences[0].source_language == "FRE"


def test_strong_anchor_absent_from_scl_is_preserved_as_unmatched_diagnostic():
    case = Case(
        itemid="001-source",
        scl="Soering v. the United Kingdom, no. 14038/88",
    )
    html = (
        "<p>1. See Soering v. the United Kingdom, no. 14038/88.</p>"
        "<p>2. See Unknown v. France, no. 99999/99, § 4.</p>"
    )

    result = extract_citation_occurrences(case, html=html)
    unmatched = [item for item in result.diagnostics if item["code"] == "unmatched_candidate"]

    assert result.report.unmatched_candidates >= 1
    assert any(item["raw_text"] == "99999/99" for item in unmatched)
    assert all(item["raw_text"] != "14038/88" for item in unmatched)
    assert all(value.raw_text != "99999/99" for value in result.occurrences)


def test_identifier_reporter_and_language_variants_are_strong_anchors():
    case = Case(
        itemid="001-source",
        scl=(
            "Soering v. the United Kingdom, no. 14038/88, 7 July 1989, "
            "Series A no. 161, ECLI:CE:ECHR:1989:0707JUD001403888"
        ),
    )
    html = """
    <p>1. See Soering v. the United Kingdom, 7 July 1989.</p>
    <p>2. The authority is no. 14038/88.</p>
    <p>3. See ECLI:CE:ECHR:1989:0707JUD001403888.</p>
    <p>4. The relevant report is Series A no. 161.</p>
    <p>5. Compare Soering c. the United Kingdom.</p>
    """

    result = extract_citation_occurrences(case, html=html)

    assert {value.finder for value in result.occurrences} == {
        "name_date",
        "party_variant",
        "application_number",
        "ecli",
        "reporter",
    }


def test_prior_strong_anchor_in_same_paragraph_enables_plain_short_form():
    case = Case(itemid="001-source", scl="Soering v. the United Kingdom, 7 July 1989")
    html = "<p>1. Soering v. the United Kingdom was followed; Soering agreed.</p>"

    result = extract_citation_occurrences(case, html=html)

    assert [value.raw_text for value in result.occurrences] == [
        "Soering v. the United Kingdom",
        "Soering",
    ]


def test_unlocated_mentions_are_diagnostic_and_serialization_is_stable():
    case = Case(
        itemid="001-source",
        scl="Soering v. the United Kingdom, 7 July 1989",
        text="1. No authority is cited here.\n\n1. Nor here.",
    )

    first = extract_citation_occurrences(case)
    second = extract_citation_occurrences(case)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.report.unlocated_mentions == 1
    assert first.diagnostics[0]["code"] == "unlocated_mention"


def test_resolution_artifacts_rehydrate_for_locator(tmp_path):
    case = Case(itemid="001-source", scl="Soering v. the United Kingdom, no. 14038/88")
    resolution = _resolved(case, [("ecli:soering", "Soering v. the United Kingdom")])[0]
    target = resolution.target
    assert target is not None
    result = CitationResolutionResult(
        resolutions=[resolution],
        targets=[target],
        nodes=[target.model_dump(mode="json")],
        edges=[],
        report=CitationResolutionReport(
            source_documents=1,
            mentions=1,
            resolved=1,
            target_documents=1,
        ),
    )
    write_resolution_artifacts(result, tmp_path)

    loaded = load_resolutions(tmp_path)

    assert len(loaded) == 1
    assert loaded[0].mention.explicit_appnos == ["14038/88"]
    assert loaded[0].target is not None
    assert loaded[0].target.node_id == "ecli:soering"


def test_english_short_form_and_multi_citation_paragraph():
    case = Case(
        itemid="synthetic-en-citations",
        languageisocode="ENG",
        text=(
            "THE LAW\n\n76. The Court refers to Bozano (cited above), § 54, "
            "and Wassink v. the Netherlands, § 24."
        ),
        scl=(
            "Bozano v. France, 18 December 1986, § 54, Series A no. 111;"
            "Wassink v. the Netherlands, 27 September 1990, § 24, "
            "Series A no. 185-A"
        ),
    )

    result = extract_citation_occurrences(case)
    paragraph = [value for value in result.occurrences if value.source_para_id == "76"]

    assert [(value.raw_text, value.target_paragraphs) for value in paragraph] == [
        ("Bozano", ["54"]),
        ("Wassink v. the Netherlands", ["24"]),
    ]
    assert paragraph[0].finder == "short_form"


def test_french_short_form_and_multi_citation_paragraph():
    case = Case(
        itemid="synthetic-fr-citations",
        languageisocode="FRE",
        text=(
            "EN DROIT\n\n83. La Cour se réfère à Bozano (précité), § 54, "
            "et Wassink c. Pays-Bas, § 24."
        ),
        scl=(
            "Bozano c. France, 18 décembre 1986, § 54, série A no. 111;"
            "Wassink c. Pays-Bas, 27 septembre 1990, § 24, série A no. 185-A"
        ),
    )

    result = extract_citation_occurrences(case)
    paragraph = [value for value in result.occurrences if value.source_para_id == "83"]

    assert [(value.raw_text, value.target_paragraphs) for value in paragraph] == [
        ("Bozano", ["54"]),
        ("Wassink c. Pays-Bas", ["24"]),
    ]
    assert paragraph[0].finder == "short_form"


def test_inclusive_discovery_recovers_name_appno_envelopes_and_rejects_namespaces():
    case = Case(
        itemid="001-source",
        appno=["999/99"],
        text=(
            "THE LAW\n101. See Magee [v. the United Kingdom, no. 28135/95], "
            "§§ 44-45; Antwi and Others v. Norway, no. 26940/10, 14 February 2012. "
            "Directive 2006/24/EC and Case C-293/12 are also mentioned."
        ),
    )

    result = discover_citation_mentions(case)

    assert [(value.cited_name, value.explicit_appnos) for value in result.mentions] == [
        ("Magee v. the United Kingdom", ["28135/95"]),
        ("Antwi and Others v. Norway", ["26940/10"]),
    ]
    assert {value["raw_text"] for value in result.diagnostics} == {"2006/24", "293/12"}
    assert {value["code"] for value in result.diagnostics} == {"external_identifier"}


@pytest.mark.parametrize(
    ("name", "appno"),
    [
        ("Šimšić v. Bosnia and Herzegovina", "51552/10"),
        ("Šilih v. Slovenia", "71463/01"),
    ],
)
def test_discovery_accepts_unicode_uppercase_case_names(name, appno):
    case = Case(itemid="001-source", text=f"THE LAW\n11. See {name}, no. {appno}, § 20.")

    result = discover_citation_mentions(case)

    assert [(mention.cited_name, mention.explicit_appnos) for mention in result.mentions] == [
        (name, [appno])
    ]


def test_discovery_rejects_lowercase_prose_around_application_shaped_number():
    case = Case(
        itemid="001-source",
        text="THE LAW\nThe applicant sent version v. against draft, no. 12345/67.",
    )

    result = discover_citation_mentions(case)

    assert result.mentions == []
    assert any(value["code"] == "unanchored_application_number" for value in result.diagnostics)


def test_inclusive_occurrence_provenance_and_exact_document_edge():
    case = Case(
        itemid="001-source",
        text="THE LAW\n101. Brennan v. the United Kingdom, no. 39846/98, § 38.",
    )
    mention = discover_citation_mentions(case).mentions[0]
    resolution = CitationResolution(
        mention=mention,
        status="resolved_metadata",
        method="appno plus corroborating metadata",
        target=CitationCandidate(
            node_id="ecli:brennan",
            itemid="001-brennan",
            docname="Brennan v. the United Kingdom",
            appnos=["39846/98"],
        ),
        candidates=[],
    )

    result = extract_citation_occurrences(case, [resolution], scope="inclusive")

    assert len(result.occurrences) == 1
    occurrence = result.occurrences[0]
    assert occurrence.scl_coverage == "not_covered"
    assert occurrence.resolution_scope == "document"
    assert occurrence.target_paragraphs == ["38"]
    assert result.inclusive_edges[0]["text_only_occurrence_count"] == 1


def test_pinpoint_does_not_swallow_following_judgment_date_day():
    case = Case(
        itemid="001-source",
        text=(
            "THE LAW\nSee Gevorgyan and Others v. Armenia (dec.), "
            "no. 66535/10, § 32, 14 January 2020."
        ),
    )
    result = discover_citation_mentions(case)
    assert result.preliminary_occurrences[0].target_paragraphs == ["32"]


def test_identity_coverage_uses_scl_name_when_scl_omits_appno():
    case = Case(
        itemid="001-source",
        scl="Hood v. the United Kingdom judgment of 18 February 1999, §§ 84-87",
        text=(
            "THE LAW\n76. See Hood v. the United Kingdom [GC], no. 27267/95, §§ 84-87, ECHR 1999-I."
        ),
    )

    result = extract_citation_occurrences(case, scope="inclusive")

    hood = next(value for value in result.occurrences if "Hood" in value.raw_text)
    assert hood.scl_coverage == "covered"
    assert hood.scl_mention_ids


def test_lowercase_ordinary_word_does_not_become_short_form():
    case = Case(
        itemid="001-source",
        scl="Weeks v. the United Kingdom, Series A no. 114",
        text="THE LAW\nThe parties were given three weeks to respond.",
    )

    result = extract_citation_occurrences(case)

    assert result.occurrences == []


def test_official_fallback_rejects_lowercase_and_legal_word_aliases():
    result = extract_citation_occurrences(
        Case(
            itemid="001-source",
            text=(
                "THE LAW\nThe law, cited above, remained unchanged. "
                "Rights judgment and another decision are ordinary prose."
            ),
        ),
        scope="inclusive",
    )

    assert result.occurrences == []


def test_source_application_and_current_party_aliases_are_not_citations():
    case = Case(
        itemid="001-ocalan-final",
        appno=["46221/99"],
        kpdate="2005-05-12",
        scl=("Öcalan v. Turkey, no. 46221/99, judgment of 12 March 2003"),
        text=(
            "PROCEDURE\n\n"
            "1. The case originated in an application (no. 46221/99) "
            "lodged by Mr Abdullah Öcalan."
        ),
    )
    resolution = _resolved(case, [("ecli:ocalan-chamber", "Öcalan v. Turkey")])[0]
    assert resolution.target is not None
    resolution.target.appnos = ["46221/99"]

    unresolved = extract_citation_occurrences(case)
    result = extract_citation_occurrences(case, [resolution])

    assert unresolved.occurrences == []
    assert result.occurrences == []
    assert {
        (value.source_block_id, value.block_start, value.block_end)
        for value in unresolved.occurrences
    } == {
        (value.source_block_id, value.block_start, value.block_end) for value in result.occurrences
    }


def test_same_application_prior_document_requires_name_date_and_phase_cue():
    case = Case(
        itemid="001-molla-sali-article-41",
        appno=["20452/14"],
        kpdate="2020-06-18",
        text=(
            "JOINT OPINION\n\n"
            "3. The conclusion departs from the principal judgment "
            "(Molla Sali v. Greece [GC], no. 20452/14, 19 December 2018)."
        ),
    )

    result = discover_citation_mentions(case)

    assert len(result.mentions) == 1
    assert result.mentions[0].cited_name == "Molla Sali v. Greece"
    assert result.mentions[0].target_date.isoformat() == "2018-12-19"
    assert result.mentions[0].discovery_evidence["method"] == ("same_application_prior_document")


def test_editorial_bracket_before_connector_keeps_the_exact_printed_locus():
    case = Case(
        itemid="001-source",
        text=("THE LAW\n\n1. See Öcalan ([v. Turkey [GC], no. 46221/99], § 91[, ECHR 2005-IV])."),
    )

    result = discover_citation_mentions(case)

    assert len(result.mentions) == 1
    assert result.mentions[0].cited_name == "Öcalan v. Turkey"
    assert result.mentions[0].explicit_appnos == ["46221/99"]
    assert result.mentions[0].raw_ref.startswith("Öcalan ([v. Turkey")


def test_following_former_state_authority_splits_the_preceding_envelope():
    case = Case(
        itemid="001-source",
        text=(
            "THE LAW\n\n"
            "1. See Sabri Güneş v. Turkey [GC], no. 27396/06, § 54, "
            "29 June 2012, and El-Masri v. the former Yugoslav Republic of "
            "Macedonia [GC], no. 39630/09, § 136, ECHR 2012."
        ),
    )

    result = discover_citation_mentions(case)

    by_appno = {value.explicit_appnos[0]: value for value in result.mentions}
    assert set(by_appno) == {"27396/06", "39630/09"}
    assert "El-Masri" not in by_appno["27396/06"].raw_ref
    assert by_appno["27396/06"].target_paragraphs == ["54"]
    assert by_appno["39630/09"].target_paragraphs == ["136"]


def test_application_envelope_does_not_swallow_preceding_short_citations():
    case = Case(
        itemid="001-source",
        scl=(
            "Gross v. Switzerland [GC], no. 67810/10, ECHR 2014;"
            "Gevorgyan and Others v. Armenia (dec.), no. 66535/10, 14 January 2020;"
            "Safaryan v. Armenia (dec.), no. 16346/10, 14 January 2020"
        ),
        text=(
            "THE LAW\n\n1. See Gross, cited above; Gevorgyan and Others, "
            "cited above; and Safaryan v. Armenia (dec.), no. 16346/10, § 24, "
            "14 January 2020."
        ),
    )

    result = extract_citation_occurrences(case, scope="inclusive")

    assert [value.raw_text for value in result.occurrences] == [
        "Gross",
        "Gevorgyan and Others",
        "Safaryan v. Armenia (dec.), no. 16346/10, § 24, 14 January 2020.",
    ]


def test_long_quoted_applicant_with_parenthetical_is_not_truncated():
    title = (
        "“Orthodox Ohrid Archdiocese (Greek-Orthodox Ohrid Archdiocese of the "
        "Peć Patriarchy)” v. the former Yugoslav Republic of Macedonia"
    )
    case = Case(
        itemid="001-source",
        text=(
            "THE LAW\n\n1. See Genov, cited above, § 43; "
            f"{title}, no. 3532/07, § 111, 16 November 2017. "
            f"2. The Court followed {title.split(' v. ')[0]}, cited above, § 111."
        ),
    )

    result = extract_citation_occurrences(case, scope="inclusive")

    values = [value for value in result.occurrences if "Orthodox Ohrid" in value.raw_text]
    assert len(values) == 2
    assert values[0].raw_text.startswith(title)
    assert values[0].target_paragraphs == ["111"]
    assert values[1].target_paragraphs == ["111"]


def test_exact_appno_authority_establishes_applicant_only_short_forms():
    case = Case(
        itemid="001-source",
        scl=("Guðmundur Andri Ástráðsson [GC], no. 26374/18, §§ 223 and 229, 1 December 2020"),
        text=(
            "THE LAW\n\n"
            "1. See Guðmundur Andri Ástráðsson ([GC], no. 26374/18, §§ 223 "
            "and 229, 1 December 2020).\n\n"
            "2. Guðmundur Andri Ástráðsson, cited above, § 295."
        ),
    )

    result = extract_citation_occurrences(case, scope="inclusive")

    values = [value for value in result.occurrences if "Guðmundur" in value.raw_text]
    assert [value.raw_text for value in values] == [
        "Guðmundur Andri Ástráðsson ([GC], no. 26374/18, §§ 223 and 229, 1 December 2020)",
        "Guðmundur Andri Ástráðsson",
    ]
    discovery = next(value for value in result.mentions if value.origin == "text_discovery")
    assert discovery.discovery_evidence["name_selection"] == "authority_printed_applicant"
    assert values[1].target_paragraphs == ["295"]


def test_established_applicant_alias_owns_an_attached_pilot_judgment_cue():
    case = Case(
        itemid="001-source",
        scl=("Yuriy Nikolayevich Ivanov v. Ukraine, no. 40450/04, §§ 89-90, 15 October 2009"),
        text=(
            "THE LAW\n\n"
            "1. In Yuriy Nikolayevich Ivanov (cited above, §§ 89-90), the Court "
            "identified the structural problem.\n\n"
            "2. The matter was resolved in the Ivanov pilot judgment."
        ),
    )

    result = extract_citation_occurrences(case, scope="inclusive")

    assert [value.raw_text for value in result.occurrences] == [
        "Yuriy Nikolayevich Ivanov",
        "Ivanov pilot judgment",
    ]


def test_full_name_future_echr_reporter_with_malformed_number_is_still_a_locus():
    result = extract_citation_occurrences(
        Case(
            itemid="001-source",
            text=(
                "THE LAW\n\n1. See Al-Adsani v. the United Kingdom, [GC], "
                "no. 35763, § 60, to be reported in ECHR 2001."
            ),
        ),
        scope="inclusive",
    )

    assert len(result.occurrences) == 1
    occurrence = result.occurrences[0]
    assert occurrence.raw_text == (
        "Al-Adsani v. the United Kingdom, [GC], no. 35763, § 60, to be reported in ECHR 2001"
    )
    assert occurrence.target_paragraphs == ["60"]
    assert occurrence.resolution_scope == "unresolved"


def test_official_back_reference_recovers_locus_but_abstains_on_ambiguous_case():
    case = Case(
        itemid="001-source",
        text=(
            "THE LAW\n\n1. See Mocanu and Others, cited above, § 261; "
            "and Stummer, cited above, § 88."
        ),
    )

    result = extract_citation_occurrences(case, scope="inclusive")

    mocanu, stummer = result.occurrences
    assert mocanu.finder == "authority_ambiguous_short_back_reference"
    assert mocanu.target_appnos == []
    assert mocanu.resolution_scope == "unresolved"
    assert mocanu.target_paragraphs == ["261"]
    assert stummer.finder == "authority_unique_short_back_reference"
    assert stummer.target_paragraphs == ["88"]
    assert stummer.evidence["discovery_evidence"]["printed_cited_name"] == "Stummer"


def test_three_letter_authority_short_form_requires_explicit_back_reference():
    case = Case(
        itemid="001-source",
        text="THE LAW\n\n1. Çam, cited above, § 66. A cam was installed nearby.",
    )

    result = extract_citation_occurrences(case, scope="inclusive")

    assert [(value.raw_text, value.finder) for value in result.occurrences] == [
        ("Çam, cited above, § 66", "authority_unique_short_back_reference")
    ]


def test_official_back_references_preserve_longest_alias_parentheses_and_grouped_pins():
    case = Case(
        itemid="001-source",
        text=(
            "THE LAW\n\n"
            "1. Sabri Güneş, cited above, § 54; Folgerø, cited above, § 84; "
            "and Banković and Others (cited above, § 66).\n\n"
            "2. See Gaygusuz, § 42; Andrejeva, § 87; and Ribać, § 53, "
            "all cited above.\n\n"
            "3. Ibrahimbeyov and Others, §§ 56-59, and Kanevska, § 49, "
            "both cited above."
        ),
    )

    result = extract_citation_occurrences(case, scope="inclusive")

    assert [(value.raw_text, value.target_paragraphs) for value in result.occurrences] == [
        ("Sabri Güneş, cited above, § 54", ["54"]),
        ("Folgerø, cited above, § 84", ["84"]),
        ("Banković and Others (cited above, § 66)", ["66"]),
        ("Gaygusuz, § 42", ["42"]),
        ("Andrejeva, § 87", ["87"]),
        ("Ribać, § 53", ["53"]),
        ("Ibrahimbeyov and Others, §§ 56-59", ["56-59"]),
        ("Kanevska, § 49", ["49"]),
    ]
    assert all(value.resolution_scope == "unresolved" for value in result.occurrences)


def test_explicit_back_reference_without_known_authority_is_an_unresolved_locus():
    result = extract_citation_occurrences(
        Case(itemid="001-source"),
        html=(
            "<p>1. See <em>Bellet, Huertas and Vialatte</em>, cited above; "
            "Article 8, cited above, is not a case.</p>"
        ),
        scope="inclusive",
    )

    assert [value.raw_text for value in result.occurrences] == [
        "Bellet, Huertas and Vialatte, cited above"
    ]
    assert result.occurrences[0].finder == "explicit_unresolved_back_reference"
    assert result.occurrences[0].resolution_scope == "unresolved"


def test_authority_formatted_fallback_requires_an_isolated_multiword_alias():
    result = extract_citation_occurrences(
        Case(itemid="001-source"),
        html=(
            "<p>1. <em>Scozzari and Giunta</em> supplied the principle. "
            "The words <em>merits</em> and <em>human rights</em> are prose.</p>"
        ),
        scope="inclusive",
    )

    assert [value.raw_text for value in result.occurrences] == ["Scozzari and Giunta"]
    assert result.occurrences[0].finder == "authority_unique_formatted_short"


def test_four_letter_short_form_needs_an_established_authority():
    case = Case(
        itemid="001-source",
        scl="Glor v. Switzerland, no. 13444/04, 30 April 2009",
        text=(
            "THE LAW\n\n"
            "1. Glor v. Switzerland supplied the governing test; Glor, § 57, "
            "then applied it.\n\n"
            "2. The word glor is deliberately lowercase prose."
        ),
    )

    result = extract_citation_occurrences(case)

    assert [value.raw_text for value in result.occurrences] == [
        "Glor v. Switzerland",
        "Glor",
    ]


@pytest.mark.parametrize(
    ("text", "name", "method"),
    [
        (
            "Turgut et autres c. Turquie (no 1411/03, §§ 41-67, 8 juillet 2008)",
            "Turgut et autres c. Turquie",
            "name_application_number",
        ),
        (
            "Wirtschafts-Trend Zeitschriften-Verlags m.b.H. (no. 3) "
            "v. Austria, nos. 66298/01 and 15653/02, 13 December 2005",
            "Wirtschafts-Trend Zeitschriften-Verlags m.b.H. (no. 3) v. Austria",
            "name_application_number",
        ),
        (
            "Parti communiste unifié de Turquie et autres c. Turquie, "
            "30 janvier 1998, § 29, Recueil 1998-I",
            "Parti communiste unifié de Turquie et autres c. Turquie",
            "historical_name_date_reporter",
        ),
        (
            "Colozza [v. Italy, judgment of 12 February 1985, Series A no. 89], pp. 14-15, § 28",
            "Colozza v. Italy",
            "historical_name_date_reporter",
        ),
        (
            "decision of 26 May 1975 concerning the case of Cyprus v. Turkey",
            "Cyprus v. Turkey",
            "historical_reverse_date_name",
        ),
    ],
)
def test_reviewed_french_corporate_and_historical_forms(text, name, method):
    result = discover_citation_mentions(
        Case(itemid="001-source", text=f"THE LAW\n\n1. See {text}.")
    )

    assert any(
        mention.cited_name == name and mention.discovery_evidence["method"] == method
        for mention in result.mentions
    )


def test_reverse_date_name_envelope_owns_following_application_numbers():
    text = (
        "In its decision of 26 May 1975 concerning the case of Cyprus v. "
        "Turkey (nos. 6780/74 and 6950/75, DR 2, p. 136), the Commission "
        "had already taken the same view."
    )

    case = Case(
        itemid="001-source",
        scl=(
            "European Commission of Human Rights, Cyprus v. Turkey, "
            "applications nos. 6780/74 and 6950/75, decisions on "
            "admissibility, 26 May 1975, DR 2, pp. 135-136"
        ),
        text=f"THE LAW\n\n1. {text}",
    )
    result = discover_citation_mentions(case)

    mention = next(
        value for value in result.mentions if set(value.explicit_appnos) == {"6780/74", "6950/75"}
    )
    occurrence = next(
        value for value in result.preliminary_occurrences if value.mention_id == mention.mention_id
    )
    assert mention.cited_name == "Cyprus v. Turkey"
    assert occurrence.raw_text.startswith("decision of 26 May 1975")

    located = extract_citation_occurrences(
        case,
        _resolved(case, [("ecli:cyprus-1975", "Cyprus v. Turkey")]),
        scope="inclusive",
    )
    full = next(value for value in located.occurrences if "26 May 1975" in value.raw_text)
    assert full.raw_text.startswith("decision of 26 May 1975")
    assert not any(value.raw_text == "6780/74" for value in located.occurrences)


def test_discovery_groups_application_numbers_in_one_envelope():
    case = Case(
        itemid="001-source",
        text=(
            "THE LAW\nThe Court referred to Example and Others v. France, "
            "nos. 12345/67 and 23456/78, judgment of 2 January 1990."
        ),
    )

    result = discover_citation_mentions(case)

    assert len(result.mentions) == 1
    assert result.mentions[0].explicit_appnos == ["12345/67", "23456/78"]


@pytest.mark.parametrize(
    ("text", "name"),
    [
        (
            "THE LAW\nSee the Golder v. the United Kingdom judgment of "
            "21 February 1975, Series A no. 18, § 35.",
            "Golder v. the United Kingdom",
        ),
        (
            "EN DROIT\nVoir l’arrêt Golder c. Royaume-Uni du 21 février 1975, "
            "série A no 18, par. 35.",
            "Golder c. Royaume-Uni",
        ),
    ],
)
def test_discovers_historical_name_date_without_application_number(text, name):
    case = Case(itemid="001-source", text=text)

    result = discover_citation_mentions(case)

    assert len(result.mentions) == 1
    assert result.mentions[0].cited_name == name
    assert result.mentions[0].discovery_evidence["method"] == "historical_name_date"


@pytest.mark.parametrize(
    ("text", "name"),
    [
        (
            "THE LAW\n\n42. Assenov and Others v. Bulgaria (judgment of "
            "28 October 1998, Reports of Judgments and Decisions 1998-VIII).",
            "Assenov and Others v. Bulgaria",
        ),
        (
            "THE LAW\n\n79. See the A. v. the United Kingdom judgment of "
            "23 September 1998, Reports 1998-VI, p. 2702, § 37.",
            "A. v. the United Kingdom",
        ),
        (
            "THE LAW\n\n76. See the De Jong, Baljet and Van den Brink v. the "
            "Netherlands judgment of 22 May 1984, Series A no. 77, § 65.",
            "De Jong, Baljet and Van den Brink v. the Netherlands",
        ),
    ],
)
def test_discovers_historical_parentheses_initials_and_multi_party_names(text, name):
    result = discover_citation_mentions(Case(itemid="001-source", text=text))

    assert len(result.mentions) == 1
    assert result.mentions[0].cited_name == name
    assert result.mentions[0].discovery_evidence["method"] == "historical_name_date"


def test_historical_text_discovery_seeds_later_short_forms_without_scl():
    case = Case(
        itemid="001-source",
        text=(
            "THE LAW\n\n"
            "42. Assenov and Others v. Bulgaria (judgment of 28 October 1998, "
            "Reports of Judgments and Decisions 1998-VIII).\n\n"
            "50. The Court followed its Assenov and Others v. Bulgaria judgment.\n\n"
            "58. See the Assenov and Others judgment cited above, §§ 144-50."
        ),
    )

    result = extract_citation_occurrences(case, scope="inclusive")
    assenov = [value for value in result.occurrences if "Assenov" in value.raw_text]

    assert len(assenov) == 3
    assert [value.source_para_id for value in assenov] == ["42", "50", "58"]
    assert assenov[-1].target_paragraphs == ["144-50"]


@pytest.mark.parametrize(
    ("name", "appno"),
    [
        ("Magee v. the United Kingdom", "28135/95"),
        ("Brennan v. the United Kingdom", "39846/98"),
        ("Al-Adsani v. the United Kingdom", "35763/97"),
        ("Selmouni v. France", "25803/94"),
        ("Amann v. Switzerland", "27798/95"),
        ("Rotaru v. Romania", "28341/95"),
        (
            "Telegraaf Media Nederland Landelijke Media B.V. and Others v. the Netherlands",
            "39315/06",
        ),
        ("Antwi and Others v. Norway", "26940/10"),
    ],
)
def test_planned_text_only_majority_regression_envelopes(name, appno):
    result = discover_citation_mentions(
        Case(itemid="001-source", text=f"THE LAW\n101. See {name}, no. {appno}, § 44.")
    )

    assert [(value.cited_name, value.explicit_appnos) for value in result.mentions] == [
        (name, [appno])
    ]
    assert result.preliminary_occurrences[0].target_paragraphs == ["44"]


@pytest.mark.parametrize(
    ("name", "tail", "appnos"),
    [
        (
            "Centro Europa 7 S.r.l. and Di Stefano v. Italy",
            "[GC], no. 38433/09, § 92, ECHR 2012",
            ["38433/09"],
        ),
        (
            "Vomočil and Art 38, a.s. v. the Czech Republic",
            "(dec.), nos. 38817/04 and 1458/07, § 47, 5 March 2013",
            ["38817/04", "1458/07"],
        ),
        (
            "Svoboden Zheleznicharski Sindikat “Promyana” v. Bulgaria",
            "(dec.), no. 5044/04, § 54, 28 May 2013",
            ["5044/04"],
        ),
        (
            "Centre for Legal Resources on behalf of Valentin Câmpeanu v. Romania",
            "[GC], no. 47848/08, § 156, ECHR 2014",
            ["47848/08"],
        ),
    ],
)
def test_long_name_application_envelopes_use_bounded_title_recovery(name, tail, appnos):
    result = discover_citation_mentions(
        Case(
            itemid="001-source",
            text=f"THE LAW\n58. The Court followed (see, among other authorities, {name} {tail}).",
        )
    )

    matching = [
        mention for mention in result.mentions if set(mention.explicit_appnos) == set(appnos)
    ]
    assert len(matching) == 1
    assert matching[0].cited_name == name
    assert matching[0].discovery_evidence["method"] == "name_application_number"


def test_shared_title_is_disambiguated_by_printed_application_number():
    case = Case(
        itemid="001-source",
        scl=(
            "Example v. France, no. 11111/11, 1 January 2011;"
            "Example v. France, no. 22222/22, 2 February 2022"
        ),
    )
    result = extract_citation_occurrences(
        case,
        _resolved(
            case,
            [("ecli:example-old", "Example v. France"), ("ecli:example-new", "Example v. France")],
        ),
        html="<p>1. See Example v. France, no. 22222/22, 2 February 2022, § 9.</p>",
    )

    named = next(
        value for value in result.occurrences if value.raw_text.startswith("Example v. France")
    )
    assert named.target_node_id == "ecli:example-new"
    assert any(
        value.get("code") == "disambiguated_alias"
        and value.get("method") == "printed_evidence_full_name"
        for value in result.diagnostics
    )


def test_shared_phase_short_form_uses_unique_prior_strong_antecedent():
    case = Case(
        itemid="001-source",
        scl=(
            "Loizidou v. Turkey (preliminary objections), 23 March 1995, Series A no. 310;"
            "Loizidou v. Turkey (merits), 18 December 1996, Reports 1996-VI"
        ),
    )
    result = extract_citation_occurrences(
        case,
        _resolved(
            case,
            [
                ("ecli:loizidou-po", "Loizidou v. Turkey"),
                ("ecli:loizidou-merits", "Loizidou v. Turkey"),
            ],
        ),
        html=(
            "<p>1. Loizidou v. Turkey (merits), 18 December 1996, Reports 1996-VI.</p>"
            "<p>2. The Loizidou judgment cited above, § 56, controls.</p>"
        ),
    )

    short = next(value for value in result.occurrences if value.raw_text == "Loizidou")
    assert short.target_node_id == "ecli:loizidou-merits"
    assert short.target_paragraphs == ["56"]


def test_same_party_prior_judgment_does_not_turn_litigant_mentions_into_citations():
    case = Case(
        itemid="001-source",
        docname="KAVALA v. TÜRKİYE [GC]",
        appno=["28749/18"],
    )

    result = extract_citation_occurrences(
        case,
        html=(
            "<p>1. Kavala v. Turkey judgment of 10 December 2019, § 240.</p>"
            "<p>2. Mr Kavala remained in detention.</p>"
            "<p>3. The Kavala judgment cited above, § 240, was binding.</p>"
        ),
        scope="inclusive",
    )
    kavala = [value for value in result.occurrences if "Kavala" in value.raw_text]

    assert [value.source_para_id for value in kavala] == ["1", "3"]
    assert [value.raw_text for value in kavala] == [
        "Kavala v. Turkey judgment of 10 December 2019",
        "Kavala judgment",
    ]


def test_formatted_full_name_without_number_is_a_first_class_unresolved_locus():
    result = extract_citation_occurrences(
        Case(itemid="001-source"),
        html=(
            "<p>1. The Court followed <em>K. and T. v. Finland</em>, cited above.</p>"
            "<p>2. The domestic judgment in <em>Coventry v. Lawrence</em> differs.</p>"
        ),
        scope="inclusive",
    )

    assert [value.raw_text for value in result.occurrences] == ["K. and T. v. Finland"]
    assert result.occurrences[0].resolution_scope == "unresolved"
    assert result.occurrences[0].finder == "formatted_name_state_parties"


def test_preceding_reporter_does_not_hide_next_formatted_full_name():
    result = extract_citation_occurrences(
        Case(itemid="001-source"),
        html=(
            "<p>1. See <em>D.H. and Others v. the Czech Republic</em> [GC], "
            "no. 57325/00, § 196, ECHR 2007-IV, and "
            "<em>J.D. and A. v. the United Kingdom</em>, cited above, § 89.</p>"
        ),
        scope="inclusive",
    )

    assert [value.raw_text for value in result.occurrences] == [
        "D.H. and Others v. the Czech Republic [GC], no. 57325/00, § 196, ECHR 2007-IV",
        "J.D. and A. v. the United Kingdom",
    ]
    assert result.occurrences[1].target_paragraphs == ["89"]


def test_preceding_reporter_tail_does_not_occupy_next_historical_citation():
    result = extract_citation_occurrences(
        Case(itemid="001-source"),
        html=(
            "<p>1. See Čonka v. Belgium, no. 51564/99, § 38, ECHR 2002-I, "
            "and Chahal v. the United Kingdom, 15 November 1996, § 112, "
            "Reports of Judgments and Decisions 1996-V.</p>"
        ),
        scope="inclusive",
    )

    assert [value.raw_text for value in result.occurrences] == [
        "Čonka v. Belgium, no. 51564/99, § 38, ECHR 2002-I",
        "Chahal v. the United Kingdom, 15 November 1996, § 112, "
        "Reports of Judgments and Decisions 1996-V",
    ]
    assert result.occurrences[1].finder == "historical_name_date_reporter"
    assert result.occurrences[1].target_paragraphs == ["112"]


def test_same_application_phase_title_requires_an_owned_prior_document_cue():
    case = Case(
        itemid="001-source",
        docname="CASE OF UKRAINE v. RUSSIA (RE CRIMEA)",
        # Legacy Parquet readers may preserve HUDOC's array-like value as one
        # string. Citation self/prior-document logic must still canonicalise it.
        appno=["['20958/14' '38334/18']"],
        scl=(
            "Ukraine v. Russia (re Crimea) (dec.) [GC], nos. 20958/14 "
            "and 38334/18, 16 December 2020"
        ),
    )
    result = extract_citation_occurrences(
        case,
        _resolved(case, [("ecli:crimea-decision", "Ukraine v. Russia (re Crimea)")]),
        html=(
            "<p>1. Ukraine v. Russia (re Crimea) (dec.) [GC], nos. 20958/14 "
            "and 38334/18, 16 December 2020.</p>"
            "<p>2. Ukraine v. Russia (re Crimea), cited above, § 257.</p>"
            "<p>3. The current case is Ukraine v. Russia (re Crimea).</p>"
        ),
    )

    crimea = [value for value in result.occurrences if "Ukraine v. Russia" in value.raw_text]
    assert [value.source_para_id for value in crimea] == ["1", "2"]
    assert crimea[1].target_paragraphs == ["257"]


def test_same_application_interstate_roman_title_accepts_prior_document_cue():
    case = Case(
        itemid="001-source",
        docname="GEORGIA v. RUSSIA (II) (JUST SATISFACTION)",
        appno=["38263/08"],
    )

    result = extract_citation_occurrences(
        case,
        html=("<p>1. Georgia v. Russia (II), cited above, §§ 144 and 175, governs the claim.</p>"),
        scope="inclusive",
    )

    assert [(value.raw_text, value.target_paragraphs) for value in result.occurrences] == [
        ("Georgia v. Russia (II)", ["144", "175"])
    ]


def test_inclusive_discovery_attaches_individual_opinion_identity():
    html = """
    <h2>THE LAW</h2><p>1. Majority reasoning.</p>
    <h2>PARTLY DISSENTING OPINION OF JUDGE SMITH</h2>
    <p>2. Magee v. the United Kingdom, no. 28135/95, § 44.</p>
    """
    case = Case(itemid="001-source", text=html, doctype="HEJUD")

    result = extract_citation_occurrences(case, html=html, scope="inclusive")

    occurrence = next(value for value in result.occurrences if "Magee" in value.raw_text)
    assert occurrence.source_component == "opinion"
    assert occurrence.source_opinion_id
    assert occurrence.source_opinion_ordinal == 1
    assert occurrence.source_opinion_authors == ["SMITH"]
    assert result.report.components["opinion"] == 1


def test_tail_footnote_in_second_opinion_keeps_invoking_opinion_identity():
    html = """
    <h2>THE LAW</h2><p>1. Majority reasoning.</p>
    <h2>DISSENTING OPINION OF JUDGE ALPHA</h2><p>1. First opinion.</p>
    <h2>PARTLY DISSENTING OPINION OF JUDGE BETA</h2><p>50. Tail.</p>
    <p>52. Last paragraph<a href="#_ftn7">[7]</a>.</p>
    <div id="_ftn7"><p>[7] See Rantsev v. Cyprus and Russia,
    no. 25965/04, § 197.</p></div>
    """
    result = extract_citation_occurrences(
        Case(itemid="001-source", text=html, doctype="HEJUD"),
        html=html,
        scope="inclusive",
    )

    occurrence = next(value for value in result.occurrences if "Rantsev" in value.raw_text)
    assert occurrence.source_footnote_id == "ftn7"
    assert occurrence.source_component == "opinion"
    assert occurrence.source_opinion_ordinal == 2
    assert occurrence.source_opinion_authors == ["BETA"]
    assert [
        (value.source_para_id, value.source_opinion_ordinal)
        for value in occurrence.source_invocations
    ] == [("52", 2)]


def test_application_level_resolution_retains_candidates_without_edge():
    case = Case(
        itemid="001-source",
        text="THE LAW\nBrennan v. the United Kingdom, no. 39846/98, § 38.",
    )
    mention = discover_citation_mentions(case).mentions[0]
    resolution = CitationResolution(
        mention=mention,
        status="ambiguous_document",
        method="application identified; document ambiguous",
        candidates=[
            CitationCandidate(
                node_id="item:one",
                itemid="001-one",
                docname="Brennan v. the United Kingdom",
                appnos=["39846/98"],
            ),
            CitationCandidate(
                node_id="item:two",
                itemid="001-two",
                docname="Brennan v. the United Kingdom (decision)",
                appnos=["39846/98"],
            ),
        ],
    )

    result = extract_citation_occurrences(case, [resolution], scope="inclusive")

    assert result.occurrences[0].resolution_scope == "application"
    assert len(result.occurrences[0].resolution_candidates) == 2
    assert result.inclusive_edges == []


def test_conflicting_printed_appnos_never_inherit_same_name_scl_target():
    case = Case(
        itemid="001-source",
        scl="Georgia v. Russia (I) [GC], no. 13255/07, ECHR 2014",
        text=("THE LAW\n\n75. See Georgia v. Russia (II) (dec.), no. 38263/08, 13 December 2011."),
    )
    scl_mention = parse_scl_mentions(case)[0]
    discovered = discover_citation_mentions(case).mentions[0]
    resolutions = [
        CitationResolution(
            mention=scl_mention,
            status="resolved_authority",
            method="fixture",
            target=CitationCandidate(
                node_id="itemid:001-georgia-i",
                itemid="001-georgia-i",
                docname="Georgia v. Russia (I)",
                appnos=["13255/07"],
            ),
        ),
        CitationResolution(
            mention=discovered,
            status="resolved_metadata",
            method="fixture",
            target=CitationCandidate(
                node_id="itemid:001-georgia-ii",
                itemid="001-georgia-ii",
                docname="Georgia v. Russia (II)",
                appnos=["38263/08"],
                document_kind="decision",
            ),
        ),
    ]

    result = extract_citation_occurrences(case, resolutions, scope="inclusive")

    assert len(result.occurrences) == 1
    assert result.occurrences[0].raw_text.startswith("Georgia v. Russia (II)")
    assert result.occurrences[0].target_itemid == "001-georgia-ii"
    assert result.occurrences[0].target_appnos == ["38263/08"]
    assert result.occurrences[0].scl_coverage == "not_covered"


def test_numbered_interstate_titles_do_not_match_by_roman_numeral_substring():
    case = Case(
        itemid="001-source",
        scl="Georgia v. Russia (I) [GC], no. 13255/07, ECHR 2014",
        text="THE LAW\n75. Georgia v. Russia (II), cited above, § 114.",
    )

    result = extract_citation_occurrences(case, scope="inclusive")

    assert len(result.occurrences) == 1
    assert result.occurrences[0].raw_text == "Georgia v. Russia (II)"
    assert result.occurrences[0].scl_coverage == "not_covered"


def test_cited_above_prefers_unique_resolved_local_phase_over_other_scl_phase():
    case = Case(
        itemid="001-source",
        scl=("Georgia v. Russia (II) (just satisfaction) [GC], no. 38263/08, 28 April 2023"),
        text=(
            "THE LAW\n1. Georgia v. Russia (II) [GC], no. 38263/08, "
            "21 January 2021.\n2. Georgia v. Russia (II), cited above, § 114."
        ),
    )
    scl_mention = parse_scl_mentions(case)[0]
    full_mention = next(
        value
        for value in discover_citation_mentions(case).mentions
        if value.explicit_appnos and value.target_date and value.target_date.year == 2021
    )
    resolutions = [
        CitationResolution(
            mention=scl_mention,
            status="resolved_metadata",
            method="fixture",
            target=CitationCandidate(
                node_id="itemid:001-just-satisfaction",
                itemid="001-just-satisfaction",
                docname="Georgia v. Russia (II) (just satisfaction)",
                appnos=["38263/08"],
                procedural_phase="just_satisfaction",
            ),
        ),
        CitationResolution(
            mention=full_mention,
            status="resolved_metadata",
            method="fixture",
            target=CitationCandidate(
                node_id="itemid:001-merits",
                itemid="001-merits",
                docname="Georgia v. Russia (II)",
                appnos=["38263/08"],
                procedural_phase="merits",
            ),
        ),
    ]

    result = extract_citation_occurrences(case, resolutions, scope="inclusive")
    short = [
        value
        for value in result.occurrences
        if value.raw_text == "Georgia v. Russia (II)" and value.target_paragraphs == ["114"]
    ]

    assert len(short) == 1
    assert short[0].target_itemid == "001-merits"
    assert short[0].target_paragraphs == ["114"]


def test_occurrence_v1_rows_remain_readable_and_serialization_is_stable():
    legacy = CitationOccurrence.model_validate(
        {
            "schema_version": "citation-occurrence/v1",
            "occurrence_id": "old",
            "mention_id": "mention",
            "source_block_id": "block",
            "block_start": 0,
            "block_end": 6,
            "document_start": 0,
            "document_end": 6,
            "raw_text": "Magee",
            "source_context": "Magee",
            "finder": "full_name",
        }
    )
    assert legacy.source_component == "majority"
    assert legacy.resolution_scope == "unresolved"
    assert legacy.locus_id is None

    case = Case(
        itemid="001-source",
        text="THE LAW\nMagee v. the United Kingdom, no. 28135/95, § 44.",
    )
    first = extract_citation_occurrences(case, scope="inclusive")
    second = extract_citation_occurrences(case, scope="inclusive")
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


@pytest.mark.parametrize(
    ("anchor", "method", "field"),
    [
        (
            "ECLI:CE:ECHR:1975:0221JUD000445170",
            "exact_ecli",
            "explicit_ecli",
        ),
        ("001-57496", "exact_itemid", "explicit_itemid"),
    ],
)
def test_discovers_exact_text_only_document_identifiers(anchor, method, field):
    result = discover_citation_mentions(
        Case(itemid="001-source", text=f"THE LAW\nSee {anchor}, § 35.")
    )

    assert len(result.mentions) == 1
    assert result.mentions[0].discovery_evidence["method"] == method
    assert getattr(result.mentions[0], field) == anchor


def test_unique_series_a_locator_is_exact_but_modern_volume_is_not_an_anchor():
    result = discover_citation_mentions(
        Case(
            itemid="001-source",
            text="THE LAW\nSee Series A no. 18, § 35, and ECHR 1999-I.",
        )
    )

    assert len(result.mentions) == 1
    assert result.mentions[0].discovery_evidence["method"] == "unique_series_a"
    assert result.mentions[0].explicit_ecli is None
    assert result.mentions[0].discovery_evidence["catalog_target_ecli"]
    assert result.mentions[0].discovery_evidence["authority_candidate_titles"]
    assert all("ECHR 1999-I" not in value.raw_ref for value in result.mentions)


def test_unique_official_series_a_locator_is_discovered_without_target_promotion(
    monkeypatch,
):
    from types import SimpleNamespace

    from hudoc_py.citations import catalog

    monkeypatch.setattr(
        catalog,
        "load_historical_catalog",
        lambda: SimpleNamespace(entries=[]),
    )
    result = discover_citation_mentions(
        Case(
            itemid="001-source",
            text=(
                "THE LAW\nSee the Case relating to languages in education in "
                "Belgium, 23 July 1968, §§ 3-4, Series A no. 6."
            ),
        )
    )

    mention = next(value for value in result.mentions if value.reporter)
    assert mention.discovery_evidence["method"] == "unique_official_series_a"
    assert mention.cited_name is None
    assert "education in Belgium" in str(mention.discovery_evidence["authority_candidate_titles"])
    assert mention.explicit_ecli is None
    assert mention.explicit_itemid is None


def test_unique_series_expands_over_compatible_quoted_historical_title_and_date():
    result = extract_citation_occurrences(
        Case(
            itemid="001-source",
            language="ENG",
            text=(
                "The judgment of 23 July 1968 in the Case “relating to certain "
                "aspects of the laws on the use of languages in education in "
                "Belgium” (merits) (Series A no. 6)."
            ),
        ),
        scope="inclusive",
    )

    occurrence = next(value for value in result.occurrences if value.finder == "unique_series_a")
    assert occurrence.raw_text.startswith("The judgment of 23 July 1968")
    assert occurrence.raw_text.endswith("Series A no. 6)")


def test_unique_historical_multiword_title_with_case_cue_is_discovered():
    result = extract_citation_occurrences(
        Case(
            itemid="001-source",
            language="ENG",
            text="The Court followed the Holy Monasteries case.",
        ),
        scope="inclusive",
    )

    occurrence = next(
        value for value in result.occurrences if value.finder == "authority_unique_document_short"
    )
    mention = next(
        value
        for value in result.mentions
        if value.discovery_evidence.get("method") == "authority_unique_document_short"
    )
    assert occurrence.raw_text == "Holy Monasteries case"
    assert mention.explicit_appnos == ["13092/87", "13984/88"]


def test_historical_french_spaced_series_locator_recovers_target_and_pinpoint():
    result = extract_citation_occurrences(
        Case(
            itemid="001-source",
            language="FRE",
            text=(
                "EN DROIT\nVoir l’arrêt Powell et Rayner du 21 février 1990, "
                "série A n o 172, pp. 13-14, par. 29."
            ),
        ),
        scope="inclusive",
    )

    occurrence = next(value for value in result.occurrences if value.finder == "unique_series_a")
    mention = next(
        value
        for value in result.mentions
        if value.discovery_evidence.get("method") == "unique_series_a"
    )
    assert mention.explicit_ecli is None
    assert mention.discovery_evidence["catalog_target_ecli"] == "ECLI:CE:ECHR:1990:0221JUD000931081"
    assert occurrence.raw_text.startswith("l’arrêt Powell et Rayner")
    assert occurrence.target_paragraphs == ["29"]
    assert (
        "powell et rayner"
        in str(occurrence.evidence["discovery_evidence"]["authority_candidate_titles"]).casefold()
    )


def test_french_full_name_uses_french_respondent_state_and_owns_pinpoint():
    result = extract_citation_occurrences(
        Case(
            itemid="001-source",
            language="FRE",
            text=("EN DROIT\nVoir M.R. et L.R. c. Estonie, décision précitée, § 37."),
        ),
        scope="inclusive",
    )

    occurrence = next(value for value in result.occurrences if "M.R." in value.raw_text)
    assert occurrence.raw_text.startswith("M.R. et L.R. c. Estonie")
    assert occurrence.target_paragraphs == ["37"]


def test_scl_spaced_initials_match_compact_printed_initials():
    case = Case(
        itemid="001-source",
        language="FRE",
        text="Voir M.R. et L.R. c. Estonie, décision précitée, § 37.",
        scl="M. R. et L. R. c. Estonie (déc.), n° 13420/12, § 37, 15 mai 2012",
        sclappnos=["13420/12"],
    )

    result = extract_citation_occurrences(case, [], scope="inclusive")

    occurrence = next(
        value for value in result.occurrences if value.raw_text == "M.R. et L.R. c. Estonie"
    )
    assert occurrence.target_paragraphs == ["37"]


def test_unique_three_letter_scl_short_form_requires_and_accepts_citation_cue():
    case = Case(
        itemid="001-source",
        language="FRE",
        text=("M. Weh a présenté ses observations. La Cour a suivi Weh, précité, §§ 53-54."),
        scl="Weh c. Autriche, no 38544/97, 8 avril 2004, §§ 32-56",
        sclappnos=["38544/97"],
    )

    result = extract_citation_occurrences(case, [], scope="inclusive")

    matches = [value for value in result.occurrences if value.raw_text == "Weh"]
    assert len(matches) == 1
    assert matches[0].target_paragraphs == ["53-54"]


def test_corporate_suffix_is_dropped_for_cued_short_form():
    case = Case(
        itemid="001-source",
        language="ENG",
        text="Benet Czech, spol. s.r.o., cited above, § 17.",
        scl=("Benet Czech, spol. s r.o v. the Czech Republic (dec.), no 38333/06, 18 May 2010"),
        sclappnos=["38333/06"],
    )

    result = extract_citation_occurrences(case, [], scope="inclusive")

    occurrence = next(value for value in result.occurrences if value.raw_text == "Benet Czech")
    assert occurrence.target_paragraphs == ["17"]


def test_prior_commission_report_cue_allows_same_application_title_locus():
    case = Case(
        itemid="001-source",
        language="ENG",
        appno=["15318/89"],
        text=(
            "See the Commission's report on the application of "
            "Loizidou v. Turkey, paras. 97, 98 and 101."
        ),
        scl=(
            "Loizidou v. Turkey judgment of 23 March 1995 "
            "(preliminary objections), Series A no. 310"
        ),
        sclappnos=["15318/89"],
    )

    result = extract_citation_occurrences(case, [], scope="inclusive")

    occurrence = next(
        value for value in result.occurrences if value.raw_text == "Loizidou v. Turkey"
    )
    assert occurrence.target_paragraphs == ["97", "98", "101"]


def test_ibid_carries_only_the_unique_immediate_antecedent_and_owns_pinpoint():
    case = Case(
        itemid="001-source",
        language="ENG",
        text=("Example v. France, no. 1234/56, § 12, 1 January 2000. Ibid., § 13."),
    )

    result = extract_citation_occurrences(case, [], scope="inclusive")

    carried = next(value for value in result.occurrences if value.finder == "carry_forward")
    assert carried.raw_text == "Ibid."
    assert carried.target_paragraphs == ["13"]
    assert carried.evidence["antecedent_occurrence_id"]


def test_ibid_abstains_after_two_authorities_at_the_same_preceding_locus():
    case = Case(
        itemid="001-source",
        language="ENG",
        text=(
            "The Loizidou judgments (preliminary objections and merits), "
            "at § 64 and § 56 respectively. Ibid., § 21."
        ),
    )

    result = extract_citation_occurrences(case, [], scope="inclusive")

    assert not any(value.finder == "carry_forward" for value in result.occurrences)
    assert any(value["code"] == "ambiguous_carry_forward" for value in result.diagnostics)


def test_ibid_chain_does_not_jump_to_intervening_weak_short_form():
    case = Case(
        itemid="001-source",
        language="ENG",
        text=(
            "John Murray v. the United Kingdom, no. 18731/91, § 45. "
            "Ibid., § 47. The Court distinguished Funke. Ibid., § 49."
        ),
        scl=(
            "John Murray v. the United Kingdom, no. 18731/91;"
            "Funke v. France, 25 February 1993, Series A no. 256-A"
        ),
        sclappnos=["18731/91"],
    )

    result = extract_citation_occurrences(case, [], scope="inclusive")

    carried = [value for value in result.occurrences if value.finder == "carry_forward"]
    assert [value.target_paragraphs for value in carried] == [["47"], ["49"]]
    assert len({value.mention_id for value in carried}) == 1


def test_footnote_ibid_chain_uses_unique_cued_previous_footnote_authority():
    texts = [
        "[33] Roman Zakharov, cited above, § 231.",
        "[34] Ibid., §§ 175-178.",
        "[35] Ibid., § 31.",
    ]
    starts = [0, len(texts[0]) + 1, len(texts[0]) + len(texts[1]) + 2]
    spine = DocumentSpine(
        document_id="001-source",
        source_format="plain_text",
        blocks=[
            DocumentBlock(
                block_id=f"b00000{index + 1}",
                type="footnote",
                text=text,
                char_start=starts[index],
                char_end=starts[index] + len(text),
                para_id=f"fn-{index + 33}",
                section="separate_opinion",
                footnote_id=f"ftn{index + 33}",
                opinion_id="opinion-1",
                opinion_ordinal=1,
                opinion_type="dissenting",
            )
            for index, text in enumerate(texts)
        ],
    )
    case = Case(
        itemid="001-source",
        language="ENG",
        scl="Roman Zakharov v. Russia [GC], no. 47143/06, ECHR 2015",
        sclappnos=["47143/06"],
    )

    result = extract_citation_occurrences(case, [], spine=spine, scope="inclusive")

    carried = [value for value in result.occurrences if value.finder == "carry_forward"]
    assert [value.target_paragraphs for value in carried] == [["175-178"], ["31"]]
    assert all(value.source_component == "opinion" for value in carried)
    assert all(value.source_opinion_id == "opinion-1" for value in carried)


def test_ibid_in_next_paragraph_accepts_quiet_prose_and_unique_strong_antecedent():
    first = "85. See Stocké v. Germany, judgment of 19 March 1991, Series A no. 199, § 167."
    second = (
        "86. The Convention permits cooperation between States when rights "
        "are respected (ibid., § 169)."
    )
    spine = DocumentSpine(
        document_id="001-source",
        source_format="plain_text",
        blocks=[
            DocumentBlock(
                block_id="b000001",
                type="paragraph",
                text=first,
                char_start=0,
                char_end=len(first),
                para_id="85",
                para_num=85,
                section="the_law",
            ),
            DocumentBlock(
                block_id="b000002",
                type="paragraph",
                text=second,
                char_start=len(first) + 1,
                char_end=len(first) + 1 + len(second),
                para_id="86",
                para_num=86,
                section="the_law",
            ),
        ],
    )
    case = Case(
        itemid="001-source",
        language="ENG",
        scl=("Stocké v. Germany, judgment of 19 March 1991, Series A no. 199, § 167 and § 169"),
    )

    result = extract_citation_occurrences(case, [], spine=spine, scope="inclusive")

    carried = next(value for value in result.occurrences if value.finder == "carry_forward")
    assert carried.source_para_id == "86"
    assert carried.target_paragraphs == ["169"]


def test_same_paragraph_ibid_can_follow_antecedents_own_reporter_envelope():
    text = (
        "THE LAW\n\n"
        "See Incal v. Turkey, judgment of 9 June 1998, Reports 1998-IV, § 71. "
        "The applicant relied on that conclusion (ibid., § 72). "
        "See Çıraklar v. Turkey, judgment of 28 October 1998, Reports 1998-VII, § 38."
    )
    case = Case(
        itemid="001-source",
        language="ENG",
        text=text,
        scl=(
            "Incal v. Turkey, judgment of 9 June 1998, Reports 1998-IV, § 71 and § 72;"
            "Çıraklar v. Turkey, judgment of 28 October 1998, Reports 1998-VII, § 38"
        ),
    )

    result = extract_citation_occurrences(case, [], scope="inclusive")

    carried = next(value for value in result.occurrences if value.finder == "carry_forward")
    assert carried.raw_text.casefold() == "ibid."
    assert carried.target_paragraphs == ["72"]
    antecedent = next(
        value
        for value in result.occurrences
        if value.occurrence_id == carried.evidence["antecedent_occurrence_id"]
    )
    assert "Incal" in antecedent.raw_text


def test_duplicate_title_only_discovery_does_not_make_numbered_authority_short_form_ambiguous():
    case = Case(
        itemid="001-source",
        language="FRE",
        text=(
            "Glor c. Suisse. La Cour a ensuite confirmé la référence dans "
            "Glor c. Suisse, no 13444/04, CEDH 2009. "
            "Elle applique enfin le raisonnement suivi dans Glor."
        ),
    )

    result = extract_citation_occurrences(case, [], scope="inclusive")

    short_forms = [
        value
        for value in result.occurrences
        if value.raw_text == "Glor" and value.block_start > case.text.rfind("dans ")
    ]
    assert len(short_forms) == 1
    assert not any(
        value.get("code") == "ambiguous_alias"
        and value.get("raw_text") == "Glor"
        and value.get("block_start") == short_forms[0].block_start
        for value in result.diagnostics
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "THE LAW\nA v. France, no. 11111/11, and B v. France, no. 22222/22, § 42.",
            {"A v. France": [], "B v. France": ["42"]},
        ),
        (
            "THE LAW\nA v. France, no. 11111/11, § 17, and B v. France, no. 22222/22.",
            {"A v. France": ["17"], "B v. France": []},
        ),
        (
            "THE LAW\nA v. France, no. 11111/11, § 17; B v. France, no. 22222/22, §§ 42-43.",
            {"A v. France": ["17"], "B v. France": ["42-43"]},
        ),
    ],
)
def test_adjacent_citations_own_only_their_pinpoints(text, expected):
    discovery = discover_citation_mentions(Case(itemid="001-source", text=text))

    assert {
        mention.cited_name: occurrence.target_paragraphs
        for mention, occurrence in zip(
            discovery.mentions, discovery.preliminary_occurrences, strict=True
        )
    } == expected


def test_article_paragraph_is_not_mistaken_for_cited_case_pinpoint():
    text = "THE LAW\nA v. France, no. 11111/11. The complaint falls under Article 5 § 4."
    discovery = discover_citation_mentions(Case(itemid="001-source", text=text))

    assert discovery.preliminary_occurrences[0].target_paragraphs == []


def test_grouped_initials_before_application_number_remain_one_case_name():
    discovery = discover_citation_mentions(
        Case(
            itemid="001-source",
            language="ENG",
            text=("THE LAW\n26. P., C. and S. v. the United Kingdom, no. 56547/00, § 14."),
        )
    )

    mention = next(value for value in discovery.mentions if value.explicit_appnos)
    assert mention.cited_name == "P., C. and S. v. the United Kingdom"
    assert mention.explicit_appnos == ["56547/00"]


def test_local_full_title_shadows_conflicting_official_surname_short_form():
    result = extract_citation_occurrences(
        Case(
            itemid="001-source",
            language="ENG",
            scl=("Yüksel Yalçınkaya v. Türkiye [GC], no. 15669/20, 26 September 2023"),
            sclappnos=["15669/20"],
            text=(
                "THE LAW\n1. Yüksel Yalçınkaya v. Türkiye [GC], no. 15669/20, "
                "26 September 2023.\n"
                "2. Yüksel Yalçınkaya, cited above, § 423."
            ),
        ),
        scope="inclusive",
    )

    short_forms = [value for value in result.occurrences if value.raw_text == "Yüksel Yalçınkaya"]
    assert len(short_forms) == 1
    assert short_forms[0].target_paragraphs == ["423"]
    assert short_forms[0].evidence["target_identity"].startswith("appno:15669/20")


def test_authority_short_form_preserves_explicit_merits_phase_and_pinpoints():
    result = extract_citation_occurrences(
        Case(
            itemid="001-source",
            language="ENG",
            text=(
                "THE LAW\nHoly Synod of the Bulgarian Orthodox Church "
                "(Metropolitan Inokentiy) and Others (merits), cited above, "
                "§§ 120 and 147."
            ),
        ),
        scope="inclusive",
    )

    occurrence = next(
        value
        for value in result.occurrences
        if value.finder == "authority_unique_short_back_reference"
    )
    mention = next(value for value in result.mentions if value.mention_id == occurrence.mention_id)
    assert occurrence.target_paragraphs == ["120", "147"]
    assert mention.procedural_phase == "merits"


def test_human_rights_committee_appnos_are_external_diagnostics():
    discovery = discover_citation_mentions(
        Case(
            itemid="001-source",
            language="ENG",
            text=(
                "THE LAW\nThe Court considered the views adopted by the Human "
                "Rights Committee in Lopez Burgos v. Uruguay and Celiberti v. "
                "Uruguay, nos. 52/1979 and 56/1979."
            ),
        )
    )

    assert not discovery.mentions
    external = [
        value
        for value in discovery.diagnostics
        if value.get("namespace") == "un_human_rights_committee"
    ]
    assert {value["raw_text"] for value in external} == {"52/1979", "56/1979"}


def test_commission_report_and_dr_references_are_classified_without_promotion():
    text = (
        "THE LAW\n"
        "See Chrysostomos and Papachrysostomou v. Turkey; report of the "
        "Commission of 8 July 1993, paras. 143-170.\n"
        "The Commission's decision of 12 March 1990 on the admissibility of "
        "application no. 16137/90, DR 65, p. 330. It also referred to "
        "application no. 17392/90, DR 73, p. 193 and application no. 7547/76, "
        "DR 12, p. 73."
    )
    case = Case(itemid="001-commission-source", appno="15318/89", text=text)

    discovery = discover_citation_mentions(case)
    commission_mentions = [
        mention
        for mention in discovery.mentions
        if mention.discovery_evidence.get("namespace") == "echr_commission"
    ]

    assert len(commission_mentions) == 4
    assert {
        mention.explicit_appnos[0] for mention in commission_mentions if mention.explicit_appnos
    } == {
        "16137/90",
        "17392/90",
        "7547/76",
    }
    report = next(
        mention
        for mention in commission_mentions
        if mention.procedural_phase == "commission_report"
    )
    assert report.target_date and report.target_date.isoformat() == "1993-07-08"
    assert report.cited_name == "Chrysostomos and Papachrysostomou v. Turkey"
    report_occurrence = next(
        occurrence
        for occurrence in discovery.preliminary_occurrences
        if occurrence.mention_id == report.mention_id
    )
    assert report_occurrence.target_paragraphs == ["143-170"]


def test_classified_commission_discovery_never_creates_a_document_edge():
    case = Case(
        itemid="001-commission-source",
        appno="15318/89",
        text=(
            "THE LAW\nThe Commission's decision of 12 March 1990 on the "
            "admissibility of application no. 16137/90, DR 65, p. 330."
        ),
    )
    mention = next(
        value
        for value in discover_citation_mentions(case).mentions
        if value.discovery_evidence.get("namespace") == "echr_commission"
    )

    resolution = asyncio.run(resolve_citations([case], mentions=[mention], catalog=[])).resolutions[
        0
    ]

    assert not resolution.resolved
    assert "Commission reference" in resolution.method


def test_classified_commission_report_merges_into_longer_compatible_scl_locus():
    case = Case(
        itemid="001-commission-source",
        appno="15318/89",
        scl=(
            "European Commission of Human Rights, Chrysostomos and "
            "Papachrysostomou v. Turkey, report of 8 July 1993, p. 16, "
            "paras. 93-95, p. 21, paras. 143-170"
        ),
        text=(
            "THE LAW\nFurthermore, in the case of Chrysostomos and "
            "Papachrysostomou v. Turkey the Commission relied on the relevant "
            "laws (see report of the Commission of 8 July 1993, paras. 143-170)."
        ),
    )

    result = extract_citation_occurrences(case, scope="inclusive")
    report_rows = [
        value
        for value in result.occurrences
        if value.raw_text.startswith("report of the Commission")
    ]

    assert len(report_rows) == 1
    assert report_rows[0].raw_text.endswith("paras. 143-170")
    assert report_rows[0].scl_coverage == "covered"
    assert "commission_report_reference" in report_rows[0].discovery_methods
    assert report_rows[0].evidence["classified_commission_provenance"]["namespace"] == (
        "echr_commission"
    )
