from __future__ import annotations

import pytest

import hudoc_py.citations.occurrences as occurrence_module
from hudoc_py.citations import (
    discover_citation_mentions,
    extract_citation_occurrences,
    load_resolutions,
    parse_scl_mentions,
    write_resolution_artifacts,
)
from hudoc_py.citations.models import (
    CitationCandidate,
    CitationOccurrence,
    CitationResolution,
    CitationResolutionReport,
    CitationResolutionResult,
)
from hudoc_py.models import Case


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
        value for value in result.occurrences
        if value.raw_text == "Hood v. the United Kingdom"
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
        mention for mention in discovery.mentions
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

    crimea = [
        value for value in result.occurrences
        if "Ukraine v. Russia" in value.raw_text
    ]
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
    assert [value.raw_text for value in crimea] == [
        "Ukraine v. Russia ( re Crimea )"
    ]
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
        text=(
            "THE LAW\n\n"
            "1. Example v. France (dec.), no. 12345/67, 3 January 2020."
        ),
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

    decision = next(
        value for value in result.occurrences if "(dec.)" in value.raw_text
    )
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


@pytest.mark.parametrize(
    ("authority", "printed"),
    [
        ("YILDIRIM", "Yıldırım"),
        ("SILIH", "SİLİH"),
        ("SILIH", "ſILIH"),
        ("KIRK", "KIRK"),
    ],
)
def test_alias_prefilter_preserves_unicode_ignorecase_equivalents(
    monkeypatch, authority, printed
):
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
        value for value in discovery.preliminary_occurrences
        if "Soering" in value.raw_text
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
        value for value in discovery.preliminary_occurrences
        if "Soering" in value.raw_text
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
        scl=(
            "Assenov and Others v. Bulgaria judgment of 28 October 1998, "
            "Reports 1998-VIII"
        ),
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
        text=(
            "THE LAW\n\n1. See Stănciulescu v. Romania (no. 2), "
            "cited above, § 44."
        ),
    )

    result = extract_citation_occurrences(case)

    assert [(value.raw_text, value.target_paragraphs) for value in result.occurrences] == [
        ("Stănciulescu v. Romania (no. 2)", ["44"])
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
        value.get("code") == "overlapping_distinct_authorities"
        for value in result.diagnostics
    )


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

    result = extract_citation_occurrences(
        case, resolutions, scope="inclusive"
    )
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
    assert discovery.mentions[0].discovery_evidence["method"] == (
        "historical_name_date_reporter"
    )
    assert discovery.preliminary_occurrences[0].target_paragraphs == ["68"]


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
            "THE LAW\n76. See Hood v. the United Kingdom [GC], no. 27267/95, "
            "§§ 84-87, ECHR 1999-I."
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
    assert [(value.source_para_id, value.source_opinion_ordinal) for value in occurrence.source_invocations] == [
        ("52", 2)
    ]


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
        text=(
            "THE LAW\n\n75. See Georgia v. Russia (II) (dec.), "
            "no. 38263/08, 13 December 2011."
        ),
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


def test_occurrence_v1_rows_remain_readable_and_serialization_is_stable():
    legacy = CitationOccurrence.model_validate({
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
    })
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
    assert result.mentions[0].explicit_ecli
    assert all("ECHR 1999-I" not in value.raw_ref for value in result.mentions)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "THE LAW\nA v. France, no. 11111/11, and B v. France, "
            "no. 22222/22, § 42.",
            {"A v. France": [], "B v. France": ["42"]},
        ),
        (
            "THE LAW\nA v. France, no. 11111/11, § 17, and B v. France, "
            "no. 22222/22.",
            {"A v. France": ["17"], "B v. France": []},
        ),
        (
            "THE LAW\nA v. France, no. 11111/11, § 17; B v. France, "
            "no. 22222/22, §§ 42-43.",
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
    text = (
        "THE LAW\nA v. France, no. 11111/11. "
        "The complaint falls under Article 5 § 4."
    )
    discovery = discover_citation_mentions(Case(itemid="001-source", text=text))

    assert discovery.preliminary_occurrences[0].target_paragraphs == []
