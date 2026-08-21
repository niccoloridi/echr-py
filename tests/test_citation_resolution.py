"""Authoritative SCL/reporter resolution and artifact tests."""

from __future__ import annotations

import asyncio
import csv
import json

import pandas as pd
import pytest

from hudoc_py.citations import (
    CitationAuthority,
    CitationAuthorityEntry,
    CitationAuthoritySource,
    CitationGraph,
    CitationOverride,
    HistoricalCatalogEntry,
    HistoricalCitationCatalog,
    IncompleteCitationResolutionError,
    ReporterLocator,
    build_historical_catalog,
    discover_citation_mentions,
    load_authority,
    parse_reporter,
    parse_scl_mentions,
    resolve_citations,
    write_resolution_artifacts,
    write_review,
)
from hudoc_py.citations import authority as authority_module
from hudoc_py.citations.authority import authority_entry_from_citation, extract_authority_citations
from hudoc_py.cli import main as cli_main
from hudoc_py.models import Case


def _case(**values) -> Case:
    base = {
        "itemid": "001-source",
        "ecli": "ECLI:CE:ECHR:2005:0512JUD004622199",
        "appno": "46221/99",
        "docname": "CASE OF ÖCALAN v. TURKEY",
        "languageisocode": "ENG",
        "doctype": "HEJUD",
        "kpdate": "2005-05-12",
    }
    base.update(values)
    return Case.model_validate(base)


def _albert_source() -> Case:
    return _case(
        scl=(
            "Albert and Le Compte v. Belgium, judgment of 10 February 1983, Series A no. 58, § 22"
        ),
        sclappnos="7299/75;7496/76",
    )


def _albert_targets() -> list[Case]:
    return [
        _case(
            itemid="001-albert-eng",
            ecli="ECLI:CE:ECHR:1983:0210JUD000729975",
            appno="7299/75;7496/76",
            docname="CASE OF ALBERT AND LE COMPTE v. BELGIUM",
            kpdate="1983-02-10",
        ),
        _case(
            itemid="001-albert-fre",
            ecli="ECLI:CE:ECHR:1983:0210JUD000729975",
            appno="7299/75;7496/76",
            docname="AFFAIRE ALBERT ET LE COMPTE c. BELGIQUE",
            languageisocode="FRE",
            doctype="HFJUD",
            kpdate="1983-02-10",
        ),
        _case(
            itemid="001-albert-50",
            ecli="ECLI:CE:ECHR:1983:1024JUD000729975",
            appno="7299/75;7496/76",
            docname="CASE OF ALBERT AND LE COMPTE v. BELGIUM (ARTICLE 50)",
            kpdate="1983-10-24",
        ),
    ]


def test_reporter_parser_covers_official_families():
    series = parse_reporter("Series A no. 234-B")
    assert series and series.key == "SERIES_A:::234:B:"
    spaced_series = parse_reporter("série A n o 172")
    assert spaced_series and spaced_series.key == "SERIES_A:::172::"
    reports = parse_reporter("Reports of Judgments and Decisions 1996-VI")
    assert reports and reports.family == "reports" and reports.volume == "VI"
    echr = parse_reporter("ECHR 2004-X (extracts)")
    assert echr and echr.extracts is True
    dr = parse_reporter("Decisions and Reports 65, p. 59")
    assert dr and dr.family == "dr" and dr.page == 59
    commission = parse_reporter("Commission Report 31")
    assert commission and commission.family == "commission_report"

    spaced = parse_reporter("Reports of Judgments and Decisions 1996 V")
    assert spaced and spaced.volume == "V"
    spaced_echr = parse_reporter("ECHR 2006 IX")
    assert spaced_echr and spaced_echr.volume == "IX"
    suffixed_dr = parse_reporter("Decisions and Reports (DR) 84-B, p. 106")
    assert suffixed_dr and suffixed_dr.suffix == "B" and suffixed_dr.page == 106
    historical = parse_reporter("decision of the Commission, Reports 27")
    assert historical and historical.family == "commission_collection"
    assert historical.volume == "27"


@pytest.mark.parametrize(
    ("reference", "family", "key_part"),
    [
        ("série\u00a0A n°\u00a0234‑B", "series_a", "234:B"),
        ("Recueil des arrêts et décisions 1996–VI", "reports", "1996:VI"),
        ("CEDH 2004-X (extraits)", "echr", "2004:X"),
        ("Décisions et rapports 65, p. 59", "dr", "65"),
        ("D. R. 65 p 59", "dr", "65"),
    ],
)
def test_reporter_grammar_handles_french_typography_and_ocr_spacing(reference, family, key_part):
    reporter = parse_reporter(reference)
    assert reporter and reporter.family == family
    assert key_part in reporter.key


@pytest.mark.parametrize(
    ("qualifier", "phase"),
    [
        ("(dec.)", "admissibility"),
        ("(Article 50)", "article_50"),
        ("(just satisfaction)", "just_satisfaction"),
        ("(revision)", "revision"),
        ("(friendly settlement)", "friendly_settlement"),
        ("(striking out)", "striking_out"),
    ],
)
def test_procedural_qualifiers_are_document_evidence(qualifier, phase):
    mention = parse_scl_mentions(_case(scl=f"Example v. State {qualifier}, no. 1/99"))[0]
    assert mention.procedural_phase == phase


def test_commission_word_order_and_unpunctuated_decision_are_parsed():
    commission = parse_scl_mentions(
        _case(scl="Twenty-One Persons v. Germany, decision of the Commission, Reports 27")
    )[0]
    assert commission.document_kind == "commission"
    assert commission.procedural_phase == "commission_decision"
    assert commission.reporter and commission.reporter.family == "commission_collection"
    assert (
        parse_scl_mentions(_case(scl="Example v. State (dec), no. 1/99"))[0].procedural_phase
        == "admissibility"
    )


def test_commission_label_is_not_absorbed_into_cited_case_name():
    mention = parse_scl_mentions(
        _case(
            scl=(
                "European Commission of Human Rights, Chrysostomos and "
                "Papachrysostomou v. Turkey, report of 8 July 1993"
            )
        )
    )[0]

    assert mention.cited_name == "Chrysostomos and Papachrysostomou v. Turkey"
    assert mention.respondent == "Turkey"


def test_commission_report_ecli_is_parsed():
    mention = parse_scl_mentions(_case(scl="Example v. State, ECLI:CE:ECHR:1993:0708REP00123456"))[
        0
    ]

    assert mention.explicit_ecli == "ECLI:CE:ECHR:1993:0708REP00123456"


def test_appno_before_historical_date_is_not_absorbed_into_case_name():
    mention = parse_scl_mentions(
        _case(scl=("Example v. State, no. 1234/56, judgment of 1 January 1999, Reports 1999-I"))
    )[0]

    assert mention.cited_name == "Example v. State"
    assert mention.explicit_appnos == ["1234/56"]


def test_trailing_domestic_law_is_not_part_of_scl_identity():
    mention = parse_scl_mentions(
        _case(
            scl=(
                "Barjamaj v. Greece (no. 36657/11, § 17, 2 May 2013). "
                "Law no. 3907/2011 applied thereafter"
            )
        )
    )[0]

    assert mention.explicit_appnos == ["36657/11"]
    assert "3907/2011" not in mention.normalized_ref


def test_protocol_16_request_identifier_is_structured_evidence():
    mention = parse_scl_mentions(
        _case(
            scl=(
                "Advisory opinion concerning an issue [GC], request no. P16‑2018‑001, 10 April 2019"
            )
        )
    )[0]
    assert mention.advisory_request_id == "P16-2018-001"
    assert mention.document_kind == "advisory"


def test_hudoc_itemid_does_not_capture_paragraph_ranges():
    paragraph = parse_scl_mentions(
        _case(scl="Example v. State, no. 1/99, §§ 176-79, 1 January 2000")
    )[0]
    explicit = parse_scl_mentions(_case(scl="Advisory opinion, HUDOC item 003-1339293-1397515"))[0]
    assert paragraph.explicit_itemid is None
    assert explicit.explicit_itemid == "003-1339293-1397515"


def test_mentions_parse_french_date_phase_and_stable_ids():
    case = _case(
        languageisocode="FRE",
        scl="Eckle c. Allemagne (article 50), 21 juin 1983, série A no 65, §§ 1-2",
    )
    first = parse_scl_mentions(case)[0]
    second = parse_scl_mentions(case)[0]
    assert first.mention_id == second.mention_id
    assert first.reference_hash == second.reference_hash
    assert first.target_date.isoformat() == "1983-06-21"
    assert first.procedural_phase == "article_50"
    assert first.respondent == "Allemagne"
    assert first.reporter and first.reporter.number == "65"
    assert first.target_paragraphs == ["1-2"]


def test_mention_preserves_unmodified_reference_but_hashes_normalized_text():
    printed = "Albert\u00a0and Le Compte v. Belgium, Series A no. 58"
    mention = parse_scl_mentions(_case(scl=printed))[0]
    normalized_variant = parse_scl_mentions(
        _case(scl="Albert and  Le Compte v. Belgium, Series A no. 58")
    )[0]
    assert mention.raw_ref == printed
    assert mention.reference_hash == normalized_variant.reference_hash


def test_paragraph_slot_and_real_paragraphs_do_not_change_bibliographic_key():
    base = "Albert and Le Compte v. Belgium, Series A no. 58"
    without_paragraph = parse_scl_mentions(_case(scl=base))[0]
    with_paragraph = parse_scl_mentions(_case(scl=f"{base}, § 22"))[0]
    assert with_paragraph.target_paragraphs == ["22"]
    assert with_paragraph.reference_hash == without_paragraph.reference_hash

    authority_entry = authority_entry_from_citation(
        "Albert and Le Compte v. Belgium, § ..., Series A no. 58"
    )
    assert authority_entry.normalized_citation == without_paragraph.normalized_ref


def test_decision_date_is_not_misread_as_a_second_target_paragraph():
    mention = parse_scl_mentions(_case(scl="Abdu v. Bulgaria, no. 26827/08, § 51, 11 March 2014"))[
        0
    ]
    assert mention.target_paragraphs == ["51"]
    assert mention.target_date.isoformat() == "2014-03-11"


@pytest.mark.parametrize(
    ("reference", "paragraph"),
    [
        ("Example v. State, § 208, 14 September 202", "208"),
        ("Example v. State, § 49, 1er March 2022", "49"),
    ],
)
def test_malformed_or_ordinal_date_is_not_paragraph_data(reference, paragraph):
    mention = parse_scl_mentions(_case(scl=reference))[0]
    assert mention.target_paragraphs == [paragraph]


def test_authority_date_can_appear_before_official_paragraph_slot():
    entry = authority_entry_from_citation(
        "Imbrioscia v. Switzerland, 24 November 1993, § ..., Series A no. 275"
    )
    assert entry.date and entry.date.isoformat() == "1993-11-24"


def test_partial_date_is_parsed_without_mistaking_reporter_year_for_decision_date():
    partial = parse_scl_mentions(_case(scl="Example v. State, February 1996"))[0]
    assert (partial.target_year, partial.target_month, partial.target_day) == (1996, 2, None)
    reporter_only = parse_scl_mentions(_case(scl="Example v. State, ECHR 2004-X"))[0]
    assert reporter_only.target_year is None


def test_historical_english_and_french_name_and_paragraph_forms():
    english = parse_scl_mentions(
        _case(
            scl="Swedish Engine Drivers' Union judgment of 6 February 1976, "
            "Series A no. 20, para. 48"
        )
    )[0]
    french = parse_scl_mentions(
        _case(scl="Arrêt Golder du 21 février 1975, série A no 18, par. 27")
    )[0]

    assert english.cited_name == "Swedish Engine Drivers' Union"
    assert english.target_paragraphs == ["48"]
    assert french.cited_name == "Golder"
    assert french.target_paragraphs == ["27"]


def test_historical_scl_name_stops_before_unpunctuated_judgment_date():
    mention = parse_scl_mentions(
        _case(
            scl=(
                "Assenov and Others v. Bulgaria judgment of 28 October 1998, "
                "Reports 1998, §§ 144-150, § 162, § 163"
            )
        )
    )[0]

    assert mention.cited_name == "Assenov and Others v. Bulgaria"
    assert mention.respondent == "Bulgaria"
    assert mention.target_date.isoformat() == "1998-10-28"


def test_same_date_and_reporter_cannot_promote_a_different_printed_title():
    source = _case(
        scl=("Assenov and Others v. Bulgaria judgment of 28 October 1998, Reports 1998, §§ 144-150")
    )
    authority = CitationAuthority(
        source_url="test",
        entries=[
            authority_entry_from_citation(
                "Assenov and Others v. Bulgaria, 28 October 1998, § ..., Reports 1998-VIII"
            ),
            authority_entry_from_citation(
                "Pérez de Rada Cavanilles v. Spain, 28 October 1998, § ..., Reports 1998-VIII"
            ),
        ],
    )
    assenov = _case(
        itemid="001-58261",
        ecli="ECLI:CE:ECHR:1998:1028JUD002476094",
        appno="24760/94",
        docname="CASE OF ASSENOV AND OTHERS v. BULGARIA",
        kpdate="1998-10-28",
    )
    perez = _case(
        itemid="001-58260",
        ecli="ECLI:CE:ECHR:1998:1028JUD002809095",
        appno="28090/95",
        docname="CASE OF PÉREZ DE RADA CAVANILLES v. SPAIN",
        kpdate="1998-10-28",
    )

    resolution = asyncio.run(
        resolve_citations([source], authority=authority, catalog=[perez, assenov])
    ).resolutions[0]

    assert resolution.target is not None
    assert resolution.target.itemid == "001-58261"
    assert resolution.target.appnos == ["24760/94"]
    assert all(candidate.itemid != "001-58260" for candidate in resolution.candidates)


def test_exact_application_number_with_conflicting_title_is_not_a_document_target():
    source = _case(scl="Assenov and Others v. Bulgaria, no. 24760/94, 28 October 1998")
    wrong = _case(
        itemid="001-wrong",
        ecli="ECLI:CE:ECHR:1998:1028JUD002476094",
        appno="24760/94",
        docname="CASE OF AN ENTIRELY DIFFERENT APPLICANT v. BULGARIA",
        kpdate="1998-10-28",
    )

    resolution = asyncio.run(
        resolve_citations(
            [source],
            authority=CitationAuthority(source_url="test", entries=[]),
            catalog=[wrong],
        )
    ).resolutions[0]

    assert resolution.target is None
    assert resolution.status == "unresolved_reference"
    assert resolution.candidates[0].conflicting_evidence == ["different title"]


def test_official_short_form_never_promotes_unprinted_procedural_metadata():
    source = _case(
        scl="",
        text="THE LAW\n\n1. Stummer, cited above, § 88.",
    )
    mention = discover_citation_mentions(source).mentions[0]
    target = _case(
        itemid="001-stummer",
        ecli="ECLI:CE:ECHR:2011:0707JUD003745202",
        appno="37452/02",
        docname="CASE OF STUMMER v. AUSTRIA",
        kpdate="2011-07-07",
    )

    resolution = asyncio.run(
        resolve_citations(
            [source],
            mentions=[mention],
            authority=CitationAuthority(source_url="test", entries=[]),
            catalog=[target],
        )
    ).resolutions[0]

    assert resolution.target is None
    assert resolution.status == "unresolved_reference"
    assert [candidate.itemid for candidate in resolution.candidates] == ["001-stummer"]
    assert "does not select a procedural document" in resolution.method


def test_reporter_authority_selects_merits_not_article_50():
    result = asyncio.run(resolve_citations([_albert_source()], catalog=_albert_targets()))
    resolution = result.resolutions[0]
    assert resolution.status == "resolved_authority"
    assert resolution.target and resolution.target.ecli.endswith("0210JUD000729975")
    assert result.report.complete
    assert len(result.nodes) == 2  # source + canonical target, not bilingual duplicate


def test_official_echr_reporter_form_selects_judgment_over_decision():
    source = _case(scl="A. v. the United Kingdom, no. 35373/97, ECHR 2002-X")
    decision = _case(
        itemid="001-a-dec",
        ecli="ECLI:CE:ECHR:2002:0305DEC003537397",
        appno="35373/97",
        docname="A. v. THE UNITED KINGDOM",
        doctype="HEDEC",
        kpdate="2002-03-05",
        documentcollectionid="CASELAW;DECISIONS;CHAMBER;ENG",
    )
    judgment = _case(
        itemid="001-a-jud",
        ecli="ECLI:CE:ECHR:2002:1217JUD003537397",
        appno="35373/97",
        docname="CASE OF A. v. THE UNITED KINGDOM",
        kpdate="2002-12-17",
    )
    authority = CitationAuthority(
        source_url="test",
        entries=[authority_entry_from_citation(source.scl)],
    )
    result = asyncio.run(
        resolve_citations([source], authority=authority, catalog=[decision, judgment])
    )
    resolution = result.resolutions[0]
    assert resolution.status == "resolved_authority"
    assert resolution.target and resolution.target.ecli == judgment.ecli


def test_historical_parenthetical_decision_matches_commission_record():
    source = _case(scl="Olcina Portilla v. Spain (dec.), no. 31474/96, 14 October 1996")
    target = _case(
        itemid="001-olcina",
        ecli="ECLI:CE:ECHR:1996:1014DEC003147496",
        appno="31474/96",
        docname="OLCINA PORTILLA v. SPAIN",
        doctype="HEDEC",
        kpdate="1996-10-14",
        documentcollectionid="CASELAW;DECISIONS;DECCOMMISSION;ENG",
    )
    result = asyncio.run(
        resolve_citations(
            [source],
            catalog=[target],
            authority=CitationAuthority(source_url="test", entries=[]),
        )
    )
    assert result.resolutions[0].status == "resolved_metadata"


def test_reviewed_dr_concordance_resolves_commission_report_despite_index_date():
    source = _case(
        scl=(
            "Temeltasch v. Switzerland, no 9116/80, report of the Commission du "
            "5 May 1982, Decisions and Reports (DR) 31, p. 130, § 73"
        ),
        kpdate="2026-01-01",
    )
    report = _case(
        itemid="001-95708",
        ecli="ECLI:CE:ECHR:1983:0305REP000911680",
        appno="9116/80",
        docname="TEMELTASCH v. SWITZERLAND",
        doctype="HEREP",
        kpdate="1983-03-05",
        documentcollectionid="CASELAW;REPORTS;ENG",
    )
    resolution = asyncio.run(
        resolve_citations(
            [source],
            catalog=[report],
            authority=CitationAuthority(source_url="test", entries=[]),
        )
    ).resolutions[0]
    assert resolution.status == "resolved_authority"
    assert resolution.target and resolution.target.ecli == report.ecli


def test_appno_alone_does_not_choose_between_procedural_documents():
    source = _case(scl="Albert and Le Compte v. Belgium, nos. 7299/75 and 7496/76")
    result = asyncio.run(
        resolve_citations(
            [source],
            catalog=_albert_targets(),
            authority=CitationAuthority(source_url="test", source_sha256="test", entries=[]),
        )
    )
    assert result.resolutions[0].status == "ambiguous_document"
    assert result.report.complete is False


def test_reporter_only_title_and_date_ignores_unordered_scl_pool_and_clin():
    source = _case(
        scl="Imbrioscia v. Switzerland, 24 November 1993, Series A no. 275",
        sclappnos="24724/94;24888/94",
    )
    judgment = _case(
        itemid="001-imbrioscia",
        ecli="ECLI:CE:ECHR:1993:1124JUD001397288",
        appno="13972/88",
        docname="CASE OF IMBRIOSCIA v. SWITZERLAND",
        kpdate="1993-11-24",
        documentcollectionid="CASELAW;JUDGMENTS;CHAMBER;ENG",
    )
    summary = _case(
        itemid="002-imbrioscia-summary",
        ecli=None,
        appno="13972/88",
        docname="Imbrioscia v. Switzerland",
        doctype="CLIN",
        kpdate="1993-11-24",
        documentcollectionid="CASELAW;CLIN;ENG",
    )
    result = asyncio.run(
        resolve_citations(
            [source],
            catalog=[judgment, summary],
            authority=CitationAuthority(source_url="test", entries=[]),
        )
    )
    resolution = result.resolutions[0]
    assert resolution.status == "resolved_authority"
    assert resolution.method == "packaged historical reporter catalog"
    assert resolution.target and resolution.target.ecli == judgment.ecli


def test_fuzzy_title_alone_never_creates_an_edge():
    source = _case(scl="Alber and Le Compt v. Belgium")
    result = asyncio.run(resolve_citations([source], catalog=_albert_targets()))
    assert result.resolutions[0].status == "unresolved_reference"
    assert result.edges == []


@pytest.mark.parametrize(
    ("source_scl", "target_values", "method"),
    [
        (
            "Wagner and J.M.W.L. v. Luxembourg, no. 76248/01, 28 June 2007",
            {
                "appno": "76240/01",
                "docname": "CASE OF WAGNER AND J.M.W.L. v. LUXEMBOURG",
                "kpdate": "2007-06-28",
            },
            "printed application number conflicts with HUDOC metadata",
        ),
        (
            "Maslarova v. Bulgaria, no. 26966/10, 29 January 2019",
            {
                "appno": "26966/10",
                "docname": "CASE OF MASLAROVA v. BULGARIA",
                "kpdate": "2019-01-31",
            },
            "printed date conflicts with HUDOC metadata",
        ),
        (
            "Savvaidou v. Greece (dec.), no. 58715/15, 31 January 2023",
            {
                "appno": "58715/15",
                "docname": "CASE OF SAVVAIDOU v. GREECE",
                "kpdate": "2023-01-31",
            },
            "printed document kind conflicts with HUDOC metadata",
        ),
    ],
)
def test_unresolved_references_report_the_actual_conflict(source_scl, target_values, method):
    target_values = {
        "ecli": "ECLI:CE:ECHR:2000:0101JUD000000100",
        **target_values,
    }
    result = asyncio.run(
        resolve_citations(
            [_case(scl=source_scl, kpdate="2026-01-01")],
            catalog=[_case(itemid="001-target", **target_values)],
            authority=CitationAuthority(source_url="test", entries=[]),
        )
    )
    resolution = result.resolutions[0]
    assert resolution.status == "unresolved_reference"
    assert resolution.method == method


def test_future_dated_candidate_is_rejected():
    source = _case(
        kpdate="1980-01-01",
        scl="Albert and Le Compte v. Belgium, 10 February 1983, nos. 7299/75 and 7496/76",
    )
    result = asyncio.run(resolve_citations([source], catalog=_albert_targets()))
    assert not result.resolutions[0].resolved
    assert result.edges == []


def test_explicit_identifier_cannot_resolve_the_source_document_or_a_future_document():
    source = _case(scl=None, kpdate="2005-05-12")
    self_mention = parse_scl_mentions(
        source.model_copy(update={"scl": f"Öcalan v. Turkey, {source.ecli}"})
    )[0]
    future = _case(
        itemid="001-future",
        ecli="ECLI:CE:ECHR:2030:0101JUD000000100",
        appno="1/00",
        docname="CASE OF FUTURE v. STATE",
        kpdate="2030-01-01",
    )
    future_mention = self_mention.model_copy(
        update={
            "mention_id": "future",
            "explicit_ecli": future.ecli,
            "cited_name": "Future v. State",
        }
    )

    self_result = asyncio.run(
        resolve_citations([source], mentions=[self_mention], catalog=[source])
    ).resolutions[0]
    future_result = asyncio.run(
        resolve_citations([source], mentions=[future_mention], catalog=[future])
    ).resolutions[0]

    assert not self_result.resolved
    assert "identity or chronology" in self_result.method
    assert not future_result.resolved
    assert "after source" in future_result.method


def test_conflicting_printed_ecli_and_itemid_fail_closed():
    ecli_target = _albert_targets()[0]
    item_target = _case(
        itemid="001-other",
        ecli="ECLI:CE:ECHR:1984:0101JUD000000100",
        appno="1/80",
        docname="CASE OF OTHER v. STATE",
        kpdate="1984-01-01",
    )
    source = _case(scl=f"Albert and Le Compte v. Belgium, {ecli_target.ecli}")
    mention = parse_scl_mentions(source)[0].model_copy(
        update={"explicit_itemid": item_target.itemid}
    )
    resolution = asyncio.run(
        resolve_citations([source], mentions=[mention], catalog=[ecli_target, item_target])
    ).resolutions[0]

    assert not resolution.resolved
    assert resolution.method == "printed ECLI and HUDOC item ID identify different documents"
    assert {candidate.itemid for candidate in resolution.candidates} == {
        ecli_target.itemid,
        item_target.itemid,
    }


def test_grouped_application_numbers_require_complete_candidate_coverage():
    source = _case(
        scl="Joined v. State, nos. 100/00 and 200/00, 1 January 2001",
        kpdate="2026-01-01",
    )
    partial = _case(
        itemid="001-partial",
        ecli="ECLI:CE:ECHR:2001:0101JUD000010000",
        appno="100/00",
        docname="CASE OF JOINED v. STATE",
        kpdate="2001-01-01",
    )
    resolution = asyncio.run(
        resolve_citations(
            [source],
            catalog=[partial],
            authority=CitationAuthority(source_url="test", entries=[]),
        )
    ).resolutions[0]

    assert not resolution.resolved
    assert "different application number" in resolution.candidates[0].conflicting_evidence


def test_missing_asserted_date_does_not_promote_an_appno_match():
    source = _case(
        scl="Example v. State, no. 100/00, 1 January 2001",
        kpdate="2026-01-01",
    )
    target = _case(
        itemid="001-undated",
        ecli="ECLI:CE:ECHR:2001:0101JUD000010000",
        appno="100/00",
        docname="CASE OF EXAMPLE v. STATE",
        kpdate=None,
    )
    resolution = asyncio.run(
        resolve_citations(
            [source],
            catalog=[target],
            authority=CitationAuthority(source_url="test", entries=[]),
        )
    ).resolutions[0]

    assert not resolution.resolved
    assert "different date" in resolution.candidates[0].conflicting_evidence


def test_reporter_conflict_is_fatal_even_when_title_and_date_agree():
    source = _case(
        scl="Example v. State, 1 January 2001, Series A no. 100",
        kpdate="2026-01-01",
    )
    target = _case(
        itemid="001-reporter-conflict",
        ecli="ECLI:CE:ECHR:2001:0101JUD000010000",
        appno="100/00",
        docname="CASE OF EXAMPLE v. STATE",
        kpdate="2001-01-01",
        casecitation="Series A no. 101",
    )
    empty_historical = HistoricalCitationCatalog(
        source_url="test", content_sha256="test", entries=[]
    )
    resolution = asyncio.run(
        resolve_citations(
            [source],
            catalog=[target],
            authority=CitationAuthority(source_url="test", entries=[]),
            historical_catalog=empty_historical,
        )
    ).resolutions[0]

    assert not resolution.resolved
    assert "different reporter locator" in resolution.candidates[0].conflicting_evidence


def test_exact_hudoc_published_by_matches_printed_echr_reporter():
    source = _case(
        scl="Example v. State, no. 100/00, ECHR 2001-II (extracts)",
        kpdate="2026-01-01",
    )
    target = _case(
        itemid="001-published-by",
        ecli="ECLI:CE:ECHR:2001:0101JUD000010000",
        appno="100/00",
        docname="CASE OF EXAMPLE v. STATE",
        kpdate="2001-01-01",
        publishedby="Reports of Judgments and Decisions 2001-II (extracts)",
    )
    resolution = asyncio.run(
        resolve_citations(
            [source],
            catalog=[target],
            authority=CitationAuthority(source_url="test", entries=[]),
        )
    ).resolutions[0]

    assert resolution.status == "resolved_metadata"
    assert resolution.target and resolution.target.itemid == target.itemid
    assert "exact reporter locator" in resolution.target.positive_evidence


@pytest.mark.parametrize(
    "published_by",
    [
        "Reports of Judgments and Decisions 2001-III (extracts)",
        "Reports of Judgments and Decisions 2001-II",
    ],
)
def test_hudoc_published_by_requires_exact_volume_and_extracts(published_by):
    source = _case(
        scl="Example v. State, no. 100/00, ECHR 2001-II (extracts)",
        kpdate="2026-01-01",
    )
    target = _case(
        itemid="001-published-by-conflict",
        ecli="ECLI:CE:ECHR:2001:0101JUD000010000",
        appno="100/00",
        docname="CASE OF EXAMPLE v. STATE",
        kpdate="2001-01-01",
        publishedby=published_by,
    )
    resolution = asyncio.run(
        resolve_citations(
            [source],
            catalog=[target],
            authority=CitationAuthority(source_url="test", entries=[]),
        )
    ).resolutions[0]

    assert not resolution.resolved
    assert "different reporter locator" in resolution.candidates[0].conflicting_evidence


def test_exact_hudoc_publication_does_not_override_kind_or_ambiguity():
    source = _case(
        scl="Example v. State (dec.), no. 100/00, ECHR 2001-II",
        kpdate="2026-01-01",
    )
    targets = [
        _case(
            itemid=f"001-published-{ordinal}",
            ecli=f"ECLI:CE:ECHR:2001:0{ordinal}01JUD000010000",
            appno="100/00",
            docname="CASE OF EXAMPLE v. STATE",
            kpdate=f"2001-0{ordinal}-01",
            publishedby="Reports of Judgments and Decisions 2001-II",
        )
        for ordinal in (1, 2)
    ]
    resolution = asyncio.run(
        resolve_citations(
            [source],
            catalog=targets,
            authority=CitationAuthority(source_url="test", entries=[]),
        )
    ).resolutions[0]

    assert not resolution.resolved
    assert all(
        "different document kind" in candidate.conflicting_evidence
        for candidate in resolution.candidates
    )


def test_non_unique_reporter_volume_does_not_resolve_without_corroboration():
    reporter = ReporterLocator(family="echr", year=2004, volume="X", raw="ECHR 2004-X")
    authority = CitationAuthority(
        source_url="test",
        source_sha256="test",
        entries=[
            CitationAuthorityEntry(
                entry_id="a",
                citation="A v. State, ECHR 2004-X",
                normalized_citation="a v. state, echr 2004-x",
                title="A v. State",
                normalized_title="A STATE",
                reporter=reporter,
            ),
            CitationAuthorityEntry(
                entry_id="b",
                citation="B v. State, ECHR 2004-X",
                normalized_citation="b v. state, echr 2004-x",
                title="B v. State",
                normalized_title="B STATE",
                reporter=reporter,
            ),
        ],
    )
    source = _case(scl="ECHR 2004-X")
    result = asyncio.run(resolve_citations([source], authority=authority, catalog=[]))
    assert result.resolutions[0].status == "unresolved_reference"
    assert result.report.complete is False


def test_same_reporter_volume_cannot_override_explicit_appno():
    reporter = ReporterLocator(family="echr", year=2003, volume="I", raw="ECHR 2003-I")
    authority = CitationAuthority(
        source_url="test",
        entries=[
            CitationAuthorityEntry(
                entry_id="one",
                citation="Cordova v. Italy (no. 1), no. 40877/98, ECHR 2003-I",
                normalized_citation="cordova v. italy (no. 1), no. 40877/98, echr 2003-i",
                title="Cordova v. Italy",
                normalized_title="CORDOVA ITALY",
                appnos=["40877/98"],
                reporter=reporter,
            ),
            CitationAuthorityEntry(
                entry_id="two",
                citation="Cordova v. Italy (no. 2), no. 45649/99, ECHR 2003-I",
                normalized_citation="cordova v. italy (no. 2), no. 45649/99, echr 2003-i",
                title="Cordova v. Italy",
                normalized_title="CORDOVA ITALY",
                appnos=["45649/99"],
                reporter=reporter,
            ),
        ],
    )
    wrong = _case(
        itemid="001-cordova-1",
        ecli="ECLI:CE:ECHR:2003:0130JUD004087798",
        appno="40877/98",
        docname="CASE OF CORDOVA v. ITALY (No. 1)",
        kpdate="2003-01-30",
    )
    right = _case(
        itemid="001-cordova-2",
        ecli="ECLI:CE:ECHR:2003:0130JUD004564999",
        appno="45649/99",
        docname="CASE OF CORDOVA v. ITALY (No. 2)",
        kpdate="2003-01-30",
    )
    source = _case(scl="Cordova v. Italy (no. 2), no. 45649/99, ECHR 2003-I")
    result = asyncio.run(resolve_citations([source], authority=authority, catalog=[wrong, right]))
    resolution = result.resolutions[0]
    assert resolution.resolved
    assert resolution.target and resolution.target.ecli == right.ecli


def test_direct_authority_id_does_not_bypass_printed_or_authority_conflicts():
    target = _case(
        itemid="001-authority-target",
        ecli="ECLI:CE:ECHR:2001:0102JUD000010000",
        appno="999/00",
        docname="CASE OF EXAMPLE v. STATE",
        kpdate="2001-01-02",
    )
    source = _case(
        scl="Example v. State, no. 100/00, 1 January 2001",
        kpdate="2026-01-01",
    )
    mention = parse_scl_mentions(source)[0]
    authority = CitationAuthority(
        source_url="test",
        entries=[
            CitationAuthorityEntry(
                entry_id="conflicting-direct-id",
                citation=source.scl,
                normalized_citation=mention.normalized_ref,
                title="Example v. State",
                normalized_title="EXAMPLE STATE",
                appnos=["100/00"],
                date="2001-01-01",
                document_kind="judgment",
                target_ecli=target.ecli,
                target_itemid=target.itemid,
            )
        ],
    )
    resolution = asyncio.run(
        resolve_citations([source], authority=authority, catalog=[target])
    ).resolutions[0]

    assert not resolution.resolved
    assert "ECLI date" in resolution.method


def test_official_authority_can_supply_checked_target_when_hudoc_metadata_is_absent():
    source = _case(
        scl="Example v. State, no. 100/00, judgment of 1 January 2001",
        kpdate="2026-01-01",
    )
    authority = CitationAuthority(
        source_url="test",
        entries=[
            CitationAuthorityEntry(
                entry_id="official-example",
                # Deliberately not an exact normalized citation: this exercises
                # the complete-appno plus compatible printed-evidence path.
                citation="Example v. State, no. 100/00, 1 January 2001",
                normalized_citation="example v. state, no. 100/00, 1 january 2001",
                title="Example v. State",
                normalized_title="EXAMPLE STATE",
                appnos=["100/00"],
                date="2001-01-01",
                document_kind="judgment",
                procedural_phase="merits",
                target_ecli="ECLI:CE:ECHR:2001:0101JUD000010000",
                target_itemid="001-official-example",
            )
        ],
    )

    resolution = asyncio.run(
        resolve_citations([source], authority=authority, catalog=[])
    ).resolutions[0]

    assert resolution.status == "resolved_authority"
    assert resolution.target
    assert resolution.target.itemid == "001-official-example"
    assert resolution.target.appnos == ["100/00"]


@pytest.mark.parametrize(
    ("scl", "authority_values"),
    [
        (
            "Different v. State, no. 100/00, judgment of 1 January 2001",
            {},
        ),
        (
            "Example v. State, no. 100/00, judgment of 2 January 2001",
            {},
        ),
        (
            "Example v. State, nos. 100/00 and 200/00, judgment of 1 January 2001",
            {},
        ),
        (
            "Example v. State, no. 100/00, decision of 1 January 2001",
            {},
        ),
    ],
)
def test_official_authority_fails_closed_on_incompatible_printed_evidence(scl, authority_values):
    source = _case(scl=scl, kpdate="2026-01-01")
    values = {
        "entry_id": "official-example",
        "citation": "Example v. State, no. 100/00, 1 January 2001",
        "normalized_citation": "example v. state, no. 100/00, 1 january 2001",
        "title": "Example v. State",
        "normalized_title": "EXAMPLE STATE",
        "appnos": ["100/00"],
        "date": "2001-01-01",
        "document_kind": "judgment",
        "procedural_phase": "merits",
        "target_ecli": "ECLI:CE:ECHR:2001:0101JUD000010000",
        "target_itemid": "001-official-example",
    }
    values.update(authority_values)
    authority = CitationAuthority(source_url="test", entries=[CitationAuthorityEntry(**values)])

    resolution = asyncio.run(
        resolve_citations([source], authority=authority, catalog=[])
    ).resolutions[0]

    assert not resolution.resolved


def test_duplicate_exact_authority_rows_cannot_select_the_first_document():
    source = _case(
        scl="Example v. State, no. 100/00, judgment of 1 January 2001",
        kpdate="2026-01-01",
    )
    mention = parse_scl_mentions(source)[0]
    entries = [
        CitationAuthorityEntry(
            entry_id=f"official-example-{ordinal}",
            citation=source.scl,
            normalized_citation=mention.normalized_ref,
            title="Example v. State",
            normalized_title="EXAMPLE STATE",
            appnos=["100/00"],
            date="2001-01-01",
            document_kind="judgment",
            procedural_phase="merits",
            target_ecli=None,
            target_itemid=f"001-official-example-{ordinal}",
        )
        for ordinal in range(1, 3)
    ]

    resolution = asyncio.run(
        resolve_citations([source], authority=CitationAuthority(source_url="test", entries=entries))
    ).resolutions[0]

    assert resolution.status == "ambiguous_document"
    assert len(resolution.candidates) == 2


def test_active_authority_target_is_not_hidden_by_unavailable_language_sibling():
    source = _case(
        scl="Example v. State, no. 100/00, judgment of 1 January 2001",
        kpdate="2026-01-01",
    )
    mention = parse_scl_mentions(source)[0]
    common = {
        "citation": source.scl,
        "normalized_citation": mention.normalized_ref,
        "title": "Example v. State",
        "normalized_title": "EXAMPLE STATE",
        "appnos": ["100/00"],
        "date": "2001-01-01",
        "document_kind": "judgment",
        "procedural_phase": "merits",
    }
    authority = CitationAuthority(
        source_url="test",
        entries=[
            CitationAuthorityEntry(
                entry_id="official-example-unavailable",
                target_unavailable=True,
                coverage_note="French version unavailable",
                **common,
            ),
            CitationAuthorityEntry(
                entry_id="official-example-active",
                target_ecli="ECLI:CE:ECHR:2001:0101JUD000010000",
                target_itemid="001-official-example",
                **common,
            ),
        ],
    )

    resolution = asyncio.run(
        resolve_citations([source], authority=authority, catalog=[])
    ).resolutions[0]

    assert resolution.status == "resolved_authority"
    assert resolution.target and resolution.target.itemid == "001-official-example"


def test_authority_appno_and_title_without_printed_document_selector_stays_application_scope():
    source = _case(scl="Example v. State, no. 100/00", kpdate="2026-01-01")
    mention = parse_scl_mentions(source)[0]
    authority = CitationAuthority(
        source_url="test",
        entries=[
            CitationAuthorityEntry(
                entry_id="official-example",
                citation=source.scl,
                normalized_citation=mention.normalized_ref,
                title="Example v. State",
                normalized_title="EXAMPLE STATE",
                appnos=["100/00"],
                date="2001-01-01",
                document_kind="judgment",
                procedural_phase="merits",
                target_ecli="ECLI:CE:ECHR:2001:0101JUD000010000",
                target_itemid="001-official-example",
            )
        ],
    )

    resolution = asyncio.run(
        resolve_citations([source], authority=authority, catalog=[])
    ).resolutions[0]

    assert not resolution.resolved


def test_authority_conflicting_ecli_and_itemid_are_not_synthesized_together():
    source = _case(
        scl="Example v. State, no. 100/00, judgment of 1 January 2001",
        kpdate="2026-01-01",
    )
    mention = parse_scl_mentions(source)[0]
    first = _case(
        itemid="001-first",
        ecli="ECLI:CE:ECHR:2001:0101JUD000010000",
        appno="100/00",
        docname="CASE OF EXAMPLE v. STATE",
        kpdate="2001-01-01",
    )
    second = first.model_copy(
        update={
            "itemid": "001-second",
            "ecli": "ECLI:CE:ECHR:2001:0101JUD000020000",
        }
    )
    authority = CitationAuthority(
        source_url="test",
        entries=[
            CitationAuthorityEntry(
                entry_id="conflicting-identifiers",
                citation=source.scl,
                normalized_citation=mention.normalized_ref,
                title="Example v. State",
                normalized_title="EXAMPLE STATE",
                appnos=["100/00"],
                date="2001-01-01",
                document_kind="judgment",
                procedural_phase="merits",
                target_ecli=first.ecli,
                target_itemid=second.itemid,
            )
        ],
    )

    resolution = asyncio.run(
        resolve_citations([source], authority=authority, catalog=[first, second])
    ).resolutions[0]

    assert not resolution.resolved
    assert {candidate.itemid for candidate in resolution.candidates} == {
        first.itemid,
        second.itemid,
    }


@pytest.mark.parametrize(
    "entry_values",
    [
        {
            "date": "2002-02-02",
            "target_ecli": "ECLI:CE:ECHR:2001:0101JUD000010000",
        },
        {
            "document_kind": "decision",
            "procedural_phase": "admissibility",
            "target_ecli": "ECLI:CE:ECHR:2001:0101JUD000010000",
        },
    ],
)
def test_synthetic_authority_target_validates_ecli_date_and_kind(entry_values):
    source = _case(
        scl="Example v. State, no. 100/00, judgment of 1 January 2001",
        kpdate="2026-01-01",
    )
    mention = parse_scl_mentions(source)[0]
    values = {
        "entry_id": "invalid-ecli-metadata",
        "citation": source.scl,
        "normalized_citation": mention.normalized_ref,
        "title": "Example v. State",
        "normalized_title": "EXAMPLE STATE",
        "appnos": ["100/00"],
        "date": "2001-01-01",
        "document_kind": "judgment",
        "procedural_phase": "merits",
        "target_ecli": "ECLI:CE:ECHR:2001:0101JUD000010000",
        "target_itemid": "001-official-example",
    }
    values.update(entry_values)
    authority = CitationAuthority(source_url="test", entries=[CitationAuthorityEntry(**values)])

    resolution = asyncio.run(
        resolve_citations([source], authority=authority, catalog=[])
    ).resolutions[0]

    assert not resolution.resolved
    assert "ECLI" in resolution.method


def test_synthetic_authority_target_docname_must_match_authority_title():
    source = _case(
        scl="Example v. State, no. 100/00, judgment of 1 January 2001",
        kpdate="2026-01-01",
    )
    mention = parse_scl_mentions(source)[0]
    authority = CitationAuthority(
        source_url="test",
        entries=[
            CitationAuthorityEntry(
                entry_id="conflicting-target-title",
                citation=source.scl,
                normalized_citation=mention.normalized_ref,
                title="Example v. State",
                normalized_title="EXAMPLE STATE",
                appnos=["100/00"],
                date="2001-01-01",
                document_kind="judgment",
                procedural_phase="merits",
                target_ecli="ECLI:CE:ECHR:2001:0101JUD000010000",
                target_itemid="001-official-example",
                target_docname="ENTIRELY DIFFERENT v. OTHER",
            )
        ],
    )

    resolution = asyncio.run(
        resolve_citations([source], authority=authority, catalog=[])
    ).resolutions[0]

    assert not resolution.resolved
    assert "different title" in resolution.candidates[0].conflicting_evidence


def test_synthetic_authority_target_cannot_self_cite_by_itemid():
    source = _case(
        itemid="001-source-only",
        ecli=None,
        scl="Example v. State, no. 100/00, judgment of 1 January 2001",
        kpdate="2026-01-01",
    )
    mention = parse_scl_mentions(source)[0]
    authority = CitationAuthority(
        source_url="test",
        entries=[
            CitationAuthorityEntry(
                entry_id="self-target",
                citation=source.scl,
                normalized_citation=mention.normalized_ref,
                title="Example v. State",
                normalized_title="EXAMPLE STATE",
                appnos=["100/00"],
                date="2001-01-01",
                document_kind="judgment",
                procedural_phase="merits",
                target_ecli="ECLI:CE:ECHR:2001:0101JUD000010000",
                target_itemid=source.itemid,
            )
        ],
    )

    resolution = asyncio.run(
        resolve_citations([source], authority=authority, catalog=[])
    ).resolutions[0]

    assert not resolution.resolved
    assert "exact source document self-edge" in resolution.candidates[0].conflicting_evidence


def test_printed_reporter_requires_target_reporter_metadata():
    source = _case(
        scl="Example v. State, no. 100/00, 1 January 2001, ECHR 2001-I",
        kpdate="2026-01-01",
    )
    target = _case(
        itemid="001-no-reporter",
        ecli="ECLI:CE:ECHR:2001:0101JUD000010000",
        appno="100/00",
        docname="CASE OF EXAMPLE v. STATE",
        kpdate="2001-01-01",
        casecitation=None,
    )

    resolution = asyncio.run(
        resolve_citations(
            [source],
            catalog=[target],
            authority=CitationAuthority(source_url="test", entries=[]),
        )
    ).resolutions[0]

    assert not resolution.resolved
    assert "different reporter locator" in resolution.candidates[0].conflicting_evidence


def test_synthetic_authority_ecli_cannot_hide_future_date_when_row_date_is_missing():
    source = _case(
        scl="Future v. State, no. 100/00, judgment, ECHR 2030-I",
        kpdate="2020-01-01",
    )
    mention = parse_scl_mentions(source)[0]
    reporter = parse_reporter("ECHR 2030-I")
    authority = CitationAuthority(
        source_url="test",
        entries=[
            CitationAuthorityEntry(
                entry_id="future-ecli",
                citation=source.scl,
                normalized_citation=mention.normalized_ref,
                title="Future v. State",
                normalized_title="FUTURE STATE",
                appnos=["100/00"],
                document_kind="judgment",
                procedural_phase="merits",
                reporter=reporter,
                target_ecli="ECLI:CE:ECHR:2030:0101JUD000010000",
                target_itemid="001-future",
            )
        ],
    )

    resolution = asyncio.run(
        resolve_citations([source], authority=authority, catalog=[])
    ).resolutions[0]

    assert not resolution.resolved
    assert "after source" in resolution.method


def test_synthetic_authority_ecli_application_must_match_authority_appno():
    source = _case(
        scl="Example v. State, no. 100/00, judgment of 1 January 2001",
        kpdate="2026-01-01",
    )
    mention = parse_scl_mentions(source)[0]
    authority = CitationAuthority(
        source_url="test",
        entries=[
            CitationAuthorityEntry(
                entry_id="wrong-ecli-appno",
                citation=source.scl,
                normalized_citation=mention.normalized_ref,
                title="Example v. State",
                normalized_title="EXAMPLE STATE",
                appnos=["100/00"],
                date="2001-01-01",
                document_kind="judgment",
                procedural_phase="merits",
                target_ecli="ECLI:CE:ECHR:2001:0101JUD000020000",
                target_itemid="001-wrong-appno",
            )
        ],
    )

    resolution = asyncio.run(
        resolve_citations([source], authority=authority, catalog=[])
    ).resolutions[0]

    assert not resolution.resolved
    assert "application" in resolution.method


@pytest.mark.parametrize(
    "target_docname",
    [
        "CASE OF EXAMPLE v. STATE (No. 2)",
        "CASE OF EXAMPLE v. STATE (ARTICLE 50)",
    ],
)
def test_synthetic_authority_target_title_qualifiers_are_identity_bearing(target_docname):
    source = _case(
        scl="Example v. State (no. 1), no. 100/00, judgment of 1 January 2001",
        kpdate="2026-01-01",
    )
    mention = parse_scl_mentions(source)[0]
    authority = CitationAuthority(
        source_url="test",
        entries=[
            CitationAuthorityEntry(
                entry_id="wrong-qualified-title",
                citation=source.scl,
                normalized_citation=mention.normalized_ref,
                title="Example v. State (no. 1)",
                normalized_title="EXAMPLE STATE NO 1",
                appnos=["100/00"],
                date="2001-01-01",
                document_kind="judgment",
                procedural_phase="merits",
                target_ecli="ECLI:CE:ECHR:2001:0101JUD000010000",
                target_itemid="001-wrong-title",
                target_docname=target_docname,
            )
        ],
    )

    resolution = asyncio.run(
        resolve_citations([source], authority=authority, catalog=[])
    ).resolutions[0]

    assert not resolution.resolved
    assert "different title" in resolution.candidates[0].conflicting_evidence


def test_cross_language_source_sibling_abstains_when_source_has_no_ecli():
    source = _case(
        itemid="001-source-eng",
        ecli=None,
        appno="100/00",
        docname="CASE OF EXAMPLE v. STATE",
        scl="Example v. State, no. 100/00, judgment of 1 January 2001",
        kpdate="2001-01-01",
    )
    mention = parse_scl_mentions(source)[0]
    authority = CitationAuthority(
        source_url="test",
        entries=[
            CitationAuthorityEntry(
                entry_id="language-sibling",
                citation=source.scl,
                normalized_citation=mention.normalized_ref,
                title="Example v. State",
                normalized_title="EXAMPLE STATE",
                appnos=["100/00"],
                date="2001-01-01",
                document_kind="judgment",
                procedural_phase="merits",
                target_ecli="ECLI:CE:ECHR:2001:0101JUD000010000",
                target_itemid="001-source-fre",
                target_docname="CASE OF EXAMPLE v. STATE",
            )
        ],
    )

    resolution = asyncio.run(
        resolve_citations([source], authority=authority, catalog=[])
    ).resolutions[0]

    assert not resolution.resolved
    assert any(
        "language sibling" in value for value in resolution.candidates[0].conflicting_evidence
    )


def test_synthetic_advisory_ecli_kind_is_accepted():
    source = _case(
        scl=("Advisory opinion concerning an issue [GC], request no. P16-2018-001, 10 April 2019"),
        kpdate="2026-01-01",
    )
    mention = parse_scl_mentions(source)[0]
    authority = CitationAuthority(
        source_url="test",
        entries=[
            CitationAuthorityEntry(
                entry_id="official-advisory",
                citation=source.scl,
                normalized_citation=mention.normalized_ref,
                title="Advisory opinion concerning an issue",
                normalized_title="ADVISORY OPINION CONCERNING AN ISSUE",
                date="2019-04-10",
                document_kind="advisory",
                procedural_phase="advisory_opinion",
                grand_chamber=True,
                target_ecli="ECLI:CE:ECHR:2019:0410ADV000000001",
                target_itemid="003-advisory",
            )
        ],
    )

    resolution = asyncio.run(
        resolve_citations([source], authority=authority, catalog=[])
    ).resolutions[0]

    assert resolution.status == "resolved_authority"


@pytest.mark.parametrize(
    ("printed_title", "target_title"),
    [
        ("Example v. State No. 1", "CASE OF EXAMPLE v. STATE No. 2"),
        (
            "Example v. State (revision no. 2)",
            "CASE OF EXAMPLE v. STATE (revision no. 3)",
        ),
    ],
)
def test_all_numbered_case_qualifiers_are_hard_identity_evidence(printed_title, target_title):
    source = _case(
        scl=f"{printed_title}, no. 100/00, judgment of 1 January 2001",
        kpdate="2026-01-01",
    )
    mention = parse_scl_mentions(source)[0]
    authority = CitationAuthority(
        source_url="test",
        entries=[
            CitationAuthorityEntry(
                entry_id="number-conflict",
                citation=source.scl,
                normalized_citation=mention.normalized_ref,
                title=printed_title,
                normalized_title="EXAMPLE STATE",
                appnos=["100/00"],
                date="2001-01-01",
                document_kind="judgment",
                procedural_phase=mention.procedural_phase,
                target_ecli="ECLI:CE:ECHR:2001:0101JUD000010000",
                target_itemid="001-number-conflict",
                target_docname=target_title,
            )
        ],
    )

    resolution = asyncio.run(
        resolve_citations([source], authority=authority, catalog=[])
    ).resolutions[0]

    assert not resolution.resolved
    assert "different title" in resolution.candidates[0].conflicting_evidence


def test_future_printed_reporter_year_fails_without_target_date_or_ecli():
    source = _case(
        scl="Example v. State, no. 100/00, judgment, ECHR 2030-I",
        kpdate="2020-01-01",
    )
    mention = parse_scl_mentions(source)[0]
    authority = CitationAuthority(
        source_url="test",
        entries=[
            CitationAuthorityEntry(
                entry_id="future-itemid-only",
                citation=source.scl,
                normalized_citation=mention.normalized_ref,
                title="Example v. State",
                normalized_title="EXAMPLE STATE",
                appnos=["100/00"],
                document_kind="judgment",
                procedural_phase="merits",
                reporter=parse_reporter("ECHR 2030-I"),
                target_itemid="001-future",
            )
        ],
    )

    resolution = asyncio.run(
        resolve_citations([source], authority=authority, catalog=[])
    ).resolutions[0]

    assert not resolution.resolved
    assert "reporter year is after" in resolution.method


def test_explicit_future_ecli_fails_even_when_catalog_date_is_missing():
    ecli = "ECLI:CE:ECHR:2030:0101JUD000020000"
    source = _case(
        scl=f"Future v. State, {ecli}",
        kpdate="2020-01-01",
    )
    target = _case(
        itemid="001-future",
        ecli=ecli,
        appno="200/00",
        docname="CASE OF FUTURE v. STATE",
        kpdate=None,
    )

    resolution = asyncio.run(resolve_citations([source], catalog=[target])).resolutions[0]

    assert not resolution.resolved
    assert "ECLI date is after" in resolution.method


def test_explicit_ecli_hydration_blocks_undated_cross_language_self_sibling():
    ecli = "ECLI:CE:ECHR:2001:0101JUD000010000"
    source = _case(
        itemid="001-source-eng",
        ecli=None,
        appno="100/00",
        docname="CASE OF EXAMPLE v. STATE",
        scl=f"Example v. State, {ecli}",
        kpdate="2001-01-01",
    )
    target = _case(
        itemid="001-source-fre",
        ecli=ecli,
        appno="100/00;200/00",
        docname="CASE OF EXAMPLE v. STATE",
        kpdate=None,
    )

    resolution = asyncio.run(resolve_citations([source], catalog=[target])).resolutions[0]

    assert not resolution.resolved
    assert any(
        "language sibling" in value for value in resolution.candidates[0].conflicting_evidence
    )


@pytest.mark.parametrize(
    ("printed", "ecli"),
    [
        (
            "Example v. State, no. 100/00, Commission decision of 1 January 2001",
            "ECLI:CE:ECHR:2001:0101REP000010000",
        ),
        (
            "Example v. State, no. 100/00, report of the Commission of 1 January 2001",
            "ECLI:CE:ECHR:2001:0101DEC000010000",
        ),
    ],
)
def test_synthetic_authority_ecli_phase_cannot_cross_decision_and_report(printed, ecli):
    source = _case(scl=printed, kpdate="2026-01-01")
    mention = parse_scl_mentions(source)[0]
    authority = CitationAuthority(
        source_url="test",
        entries=[
            CitationAuthorityEntry(
                entry_id="phase-conflict",
                citation=source.scl,
                normalized_citation=mention.normalized_ref,
                title="Example v. State",
                normalized_title="EXAMPLE STATE",
                appnos=["100/00"],
                date="2001-01-01",
                document_kind=mention.document_kind,
                procedural_phase=mention.procedural_phase,
                target_ecli=ecli,
                target_itemid="001-phase-conflict",
            )
        ],
    )

    resolution = asyncio.run(
        resolve_citations([source], authority=authority, catalog=[])
    ).resolutions[0]

    assert not resolution.resolved
    assert "ECLI kind" in resolution.method


def test_interpretation_is_a_distinct_judgment_phase_and_cannot_match_merits():
    source = _case(
        scl=(
            "Hentrich v. France (interpretation), 3 July 1997, "
            "Reports of Judgments and Decisions 1997-IV"
        ),
        kpdate="2026-01-01",
    )
    mention = parse_scl_mentions(source)[0]
    assert mention.procedural_phase == "interpretation"
    assert mention.document_kind == "judgment"
    target = _case(
        itemid="001-hentrich-merits",
        ecli="ECLI:CE:ECHR:1997:0703JUD001368888",
        appno="13688/88",
        docname="CASE OF HENTRICH v. FRANCE",
        kpdate="1997-07-03",
    )

    resolution = asyncio.run(
        resolve_citations(
            [source],
            catalog=[target],
            authority=CitationAuthority(source_url="test", entries=[]),
        )
    ).resolutions[0]

    assert not resolution.resolved


def test_compound_procedural_title_qualifiers_must_match_as_a_complete_set():
    source = _case(
        scl=(
            "The Very Long Applicant Association v. State "
            "(just satisfaction - striking out), no. 100/00, "
            "judgment of 1 January 2001"
        ),
        kpdate="2026-01-01",
    )
    target = _case(
        itemid="001-wrong-compound-phase",
        ecli="ECLI:CE:ECHR:2001:0101JUD000010000",
        appno="100/00",
        docname=(
            "CASE OF THE VERY LONG APPLICANT ASSOCIATION v. STATE "
            "(JUST SATISFACTION - FRIENDLY SETTLEMENT)"
        ),
        kpdate="2001-01-01",
    )

    resolution = asyncio.run(
        resolve_citations(
            [source],
            catalog=[target],
            authority=CitationAuthority(source_url="test", entries=[]),
        )
    ).resolutions[0]

    assert not resolution.resolved
    assert "different title" in resolution.candidates[0].conflicting_evidence


@pytest.mark.parametrize(
    ("printed_title", "target_title"),
    [
        (
            "Times Newspapers Ltd v. State (No. 1 and No. 2)",
            "Times Newspapers Ltd v. State (No. 3 and No. 2)",
        ),
        (
            "Hyde Park and Others v. State (nos. 5 and 6)",
            "Hyde Park and Others v. State (nos. 5 and 7)",
        ),
        (
            "Hyde Park et autres c. État (nos 5 et 6)",
            "Hyde Park et autres c. État (nos 5 et 7)",
        ),
    ],
)
def test_all_joined_case_number_qualifiers_are_identity_bearing(printed_title, target_title):
    source = _case(
        scl=f"{printed_title}, no. 100/00, judgment of 1 January 2001",
        kpdate="2026-01-01",
    )
    target = _case(
        itemid="001-wrong-case-number",
        ecli="ECLI:CE:ECHR:2001:0101JUD000010000",
        appno="100/00",
        docname=f"CASE OF {target_title}",
        kpdate="2001-01-01",
    )

    resolution = asyncio.run(
        resolve_citations(
            [source],
            catalog=[target],
            authority=CitationAuthority(source_url="test", entries=[]),
        )
    ).resolutions[0]

    assert not resolution.resolved
    assert "different title" in resolution.candidates[0].conflicting_evidence


def test_partial_echr_reporter_year_cannot_select_unprinted_volume_or_phase():
    source = _case(
        scl="Example v. State, no. 100/00, ECHR 2001",
        kpdate="2026-01-01",
    )
    authority = CitationAuthority(
        source_url="test",
        entries=[
            CitationAuthorityEntry(
                entry_id="unprinted-volume",
                citation="Example v. State, no. 100/00, 1 January 2001, ECHR 2001-I",
                normalized_citation=("example v. state, no. 100/00, 1 january 2001, echr 2001-i"),
                title="Example v. State",
                normalized_title="EXAMPLE STATE",
                appnos=["100/00"],
                date="2001-01-01",
                document_kind="judgment",
                procedural_phase="merits",
                reporter=parse_reporter("ECHR 2001-I"),
                target_ecli="ECLI:CE:ECHR:2001:0101JUD000010000",
                target_itemid="001-unprinted-volume",
            )
        ],
    )

    resolution = asyncio.run(
        resolve_citations([source], authority=authority, catalog=[])
    ).resolutions[0]

    assert not resolution.resolved


def test_bare_dr_volume_cannot_inherit_an_unprinted_page_from_authority():
    source = _case(
        scl="Example v. State, no. 100/00, D.R. 31",
        kpdate="2026-01-01",
    )
    mention = parse_scl_mentions(source)[0]
    assert mention.reporter and mention.reporter.page is None
    authority = CitationAuthority(
        source_url="test",
        entries=[
            CitationAuthorityEntry(
                entry_id="unprinted-dr-page",
                citation=(
                    "Example v. State, no. 100/00, Commission decision of "
                    "1 January 2001, D.R. 31, p. 130"
                ),
                normalized_citation="example state 100/00 dr 31 130",
                title="Example v. State",
                normalized_title="EXAMPLE STATE",
                appnos=["100/00"],
                date="2001-01-01",
                document_kind="commission",
                procedural_phase="commission_decision",
                reporter=parse_reporter("D.R. 31, p. 130"),
                target_ecli="ECLI:CE:ECHR:2001:0101DEC000010000",
                target_itemid="001-unprinted-dr-page",
            )
        ],
    )

    resolution = asyncio.run(
        resolve_citations([source], authority=authority, catalog=[])
    ).resolutions[0]

    assert not resolution.resolved


def test_explicit_ecli_cannot_override_conflicting_catalog_metadata():
    ecli = "ECLI:CE:ECHR:2001:0101JUD000010000"
    source = _case(
        ecli=None,
        itemid="001-source-language",
        appno="100/00",
        docname="CASE OF EXAMPLE v. STATE",
        scl=f"Example v. State, no. 100/00, {ecli}",
        kpdate="2005-01-01",
    )
    corrupt_target = _case(
        itemid="001-corrupt-target",
        ecli=ecli,
        appno="999/99",
        docname="CASE OF EXAMPLE v. STATE",
        kpdate="2000-01-01",
    )

    resolution = asyncio.run(resolve_citations([source], catalog=[corrupt_target])).resolutions[0]

    assert not resolution.resolved
    assert "candidate date conflicts with ECLI" in resolution.method
    assert "candidate application number conflicts with ECLI" in resolution.method


def test_itemid_only_authority_cannot_bypass_candidate_ecli_metadata_conflict():
    source = _case(
        scl="Example v. State, no. 100/00, judgment of 1 January 2001",
        kpdate="2026-01-01",
    )
    mention = parse_scl_mentions(source)[0]
    corrupt_target = _case(
        itemid="001-itemid-only-corrupt",
        ecli="ECLI:CE:ECHR:2001:0101JUD000020000",
        appno="100/00",
        docname="CASE OF EXAMPLE v. STATE",
        kpdate="2001-01-01",
    )
    authority = CitationAuthority(
        source_url="test",
        entries=[
            CitationAuthorityEntry(
                entry_id="itemid-only",
                citation=source.scl,
                normalized_citation=mention.normalized_ref,
                title="Example v. State",
                normalized_title="EXAMPLE STATE",
                appnos=["100/00"],
                date="2001-01-01",
                document_kind="judgment",
                procedural_phase="merits",
                target_itemid=corrupt_target.itemid,
            )
        ],
    )

    resolution = asyncio.run(
        resolve_citations([source], catalog=[corrupt_target], authority=authority)
    ).resolutions[0]

    assert not resolution.resolved
    assert "candidate identifier metadata conflict" in (
        resolution.candidates[0].conflicting_evidence
    )


def test_historical_catalog_target_ecli_must_agree_with_catalog_identity():
    source = _case(
        scl=("Example v. State, no. 100/00, judgment of 1 January 2001, Series A no. 100"),
        kpdate="2026-01-01",
    )
    mention = parse_scl_mentions(source)[0]
    corrupt_ecli = "ECLI:CE:ECHR:2001:0101JUD000020000"
    corrupt_target = _case(
        itemid="001-corrupt-historical",
        ecli=corrupt_ecli,
        appno="100/00",
        docname="CASE OF EXAMPLE v. STATE",
        kpdate="2001-01-01",
    )
    historical = HistoricalCitationCatalog(
        source_url="test",
        content_sha256="test",
        entries=[
            HistoricalCatalogEntry(
                reporter_key=mention.reporter.key,
                title="Example v. State",
                normalized_title="EXAMPLE STATE",
                appnos=["100/00"],
                date=mention.target_date,
                document_kind="judgment",
                target_ecli=corrupt_ecli,
                target_itemid=corrupt_target.itemid,
            )
        ],
    )

    resolution = asyncio.run(
        resolve_citations(
            [source],
            catalog=[corrupt_target],
            authority=CitationAuthority(source_url="test", entries=[]),
            historical_catalog=historical,
        )
    ).resolutions[0]

    assert not resolution.resolved
    assert resolution.target is None


def test_printed_grand_chamber_requires_known_or_officially_corroborated_formation():
    source = _case(
        scl="Example v. State [GC], no. 100/00, 1 January 2001",
        kpdate="2026-01-01",
    )
    unknown = _case(
        itemid="001-unknown-formation",
        ecli="ECLI:CE:ECHR:2001:0101JUD000010000",
        appno="100/00",
        docname="CASE OF EXAMPLE v. STATE",
        kpdate="2001-01-01",
        doctypebranch=None,
        documentcollectionid="CASELAW;JUDGMENTS;ENG",
    )
    chamber = unknown.model_copy(
        update={
            "itemid": "001-known-chamber",
            "ecli": "ECLI:CE:ECHR:2001:0101JUD000010001",
            "doctype_branch": "CHAMBER",
            "document_collection_id": "CASELAW;JUDGMENTS;CHAMBER;ENG",
        }
    )
    empty = CitationAuthority(source_url="test", entries=[])

    unknown_result = asyncio.run(
        resolve_citations([source], authority=empty, catalog=[unknown])
    ).resolutions[0]
    chamber_result = asyncio.run(
        resolve_citations([source], authority=empty, catalog=[chamber])
    ).resolutions[0]
    assert not unknown_result.resolved
    assert "missing Grand Chamber metadata" in unknown_result.candidates[0].conflicting_evidence
    assert not chamber_result.resolved
    assert "not Grand Chamber" in chamber_result.candidates[0].conflicting_evidence

    mention = parse_scl_mentions(source)[0]
    authority = CitationAuthority(
        source_url="test",
        entries=[
            CitationAuthorityEntry(
                entry_id="official-gc",
                citation=source.scl,
                normalized_citation=mention.normalized_ref,
                title="Example v. State",
                normalized_title="EXAMPLE STATE",
                appnos=["100/00"],
                date="2001-01-01",
                document_kind="judgment",
                procedural_phase="merits",
                grand_chamber=True,
                target_ecli=unknown.ecli,
                target_itemid=unknown.itemid,
            )
        ],
    )
    corroborated = asyncio.run(
        resolve_citations([source], authority=authority, catalog=[unknown])
    ).resolutions[0]
    assert corroborated.resolved
    assert corroborated.target and corroborated.target.itemid == unknown.itemid


def test_advisory_request_retains_all_same_title_candidates_and_abstains():
    source = _case(
        scl=("Advisory opinion concerning an issue [GC], request no. P16-2018-001, 10 April 2019"),
        kpdate="2026-01-01",
    )
    values = {
        "ecli": None,
        "appno": None,
        "advopidentifier": "P16-2018-001",
        "docname": "ADVISORY OPINION CONCERNING AN ISSUE",
        "doctype": "HEADO",
        "doctypebranch": "GRANDCHAMBER",
        "kpdate": "2019-04-10",
        "documentcollectionid": "CASELAW;ADVISORYOPINIONS;GRANDCHAMBER;ENG",
    }
    first = _case(itemid="003-advisory-one", **values)
    second = _case(itemid="003-advisory-two", **values)
    resolution = asyncio.run(
        resolve_citations(
            [source],
            catalog=[first, second],
            authority=CitationAuthority(source_url="test", entries=[]),
        )
    ).resolutions[0]

    assert resolution.status == "ambiguous_document"
    assert {candidate.itemid for candidate in resolution.candidates} == {
        first.itemid,
        second.itemid,
    }


def test_historical_catalog_retains_conflicting_duplicate_keys_and_resolution_abstains():
    reporter = ReporterLocator(family="series_a", number="100", raw="Series A no. 100")
    authority = CitationAuthority(
        source_url="test",
        entries=[
            CitationAuthorityEntry(
                entry_id=f"duplicate-{ordinal}",
                citation="Example v. State, 1 January 2001, Series A no. 100",
                normalized_citation="example v. state, 1 january 2001, series a no. 100",
                title="Example v. State",
                normalized_title="EXAMPLE STATE",
                date="2001-01-01",
                document_kind="judgment",
                reporter=reporter,
                target_ecli=ecli,
                target_itemid=f"001-duplicate-{ordinal}",
            )
            for ordinal, ecli in enumerate(
                (
                    "ECLI:CE:ECHR:2001:0101JUD000010000",
                    "ECLI:CE:ECHR:2001:0101JUD000020000",
                ),
                start=1,
            )
        ],
    )
    historical = build_historical_catalog(authority=authority)
    assert len(historical.entries) == 2

    source = _case(
        scl="Example v. State, 1 January 2001, Series A no. 100",
        kpdate="2026-01-01",
    )
    targets = [
        _case(
            itemid=entry.target_itemid,
            ecli=entry.target_ecli,
            appno=f"{ordinal}/00",
            docname="CASE OF EXAMPLE v. STATE",
            kpdate="2001-01-01",
        )
        for ordinal, entry in enumerate(historical.entries, start=1)
    ]
    resolution = asyncio.run(
        resolve_citations(
            [source],
            catalog=targets,
            authority=CitationAuthority(source_url="empty", entries=[]),
            historical_catalog=historical,
        )
    ).resolutions[0]
    assert not resolution.resolved


@pytest.mark.parametrize(
    ("scl", "target_values", "expected_conflict"),
    [
        (
            "Example v. State, no. 100/00, 1 January 2001, Series A no. 100",
            {"kpdate": "2001-01-02"},
            "different date",
        ),
        (
            "Example v. State, nos. 100/00 and 200/00, 1 January 2001, Series A no. 100",
            {"appno": "100/00"},
            "different application number",
        ),
        (
            "Example v. State, judgment of 1 January 2001, Series A no. 100",
            {
                "doctype": "HEDEC",
                "documentcollectionid": "CASELAW;DECISIONS;CHAMBER;ENG",
            },
            "different document kind",
        ),
        (
            "Example v. State, judgment of 1 January 2001, Series A no. 100",
            {"docname": "CASE OF EXAMPLE v. STATE (ARTICLE 50)"},
            "different procedural phase",
        ),
        (
            "Example v. State [GC], no. 100/00, 1 January 2001, Series A no. 100",
            {
                "doctypebranch": "CHAMBER",
                "documentcollectionid": "CASELAW;JUDGMENTS;CHAMBER;ENG",
            },
            "not Grand Chamber",
        ),
    ],
)
def test_historical_reporter_never_bypasses_printed_metadata_conflicts(
    scl, target_values, expected_conflict
):
    source = _case(scl=scl, kpdate="2026-01-01")
    mention = parse_scl_mentions(source)[0]
    values = {
        "itemid": "001-historical-target",
        "ecli": "ECLI:CE:ECHR:2001:0101JUD000010000",
        "appno": "100/00;200/00",
        "docname": "CASE OF EXAMPLE v. STATE",
        "kpdate": "2001-01-01",
        **target_values,
    }
    target = _case(**values)
    historical = HistoricalCitationCatalog(
        source_url="test",
        content_sha256="test",
        entries=[
            HistoricalCatalogEntry(
                reporter_key=mention.reporter.key,
                title="Example v. State",
                normalized_title="EXAMPLE STATE",
                appnos=list(mention.explicit_appnos),
                date=mention.target_date,
                document_kind=mention.document_kind,
                target_ecli=target.ecli,
                target_itemid=target.itemid,
            )
        ],
    )
    resolution = asyncio.run(
        resolve_citations(
            [source],
            catalog=[target],
            authority=CitationAuthority(source_url="test", entries=[]),
            historical_catalog=historical,
        )
    ).resolutions[0]

    assert not resolution.resolved
    assert expected_conflict in resolution.candidates[0].conflicting_evidence


@pytest.mark.parametrize("mode", ["self", "future"])
def test_historical_reporter_never_bypasses_self_or_future_conflicts(mode):
    source = _case(scl=None, kpdate="2005-05-12")
    if mode == "self":
        target = source
        cited_date = "12 May 2005"
        title = "Öcalan v. Turkey"
    else:
        target = _case(
            itemid="001-historical-future",
            ecli="ECLI:CE:ECHR:2030:0101JUD000010000",
            appno="100/00",
            docname="CASE OF FUTURE v. STATE",
            kpdate="2030-01-01",
        )
        cited_date = "1 January 2030"
        title = "Future v. State"
    source.scl = f"{title}, {cited_date}, Series A no. 100"
    mention = parse_scl_mentions(source)[0]
    historical = HistoricalCitationCatalog(
        source_url="test",
        content_sha256="test",
        entries=[
            HistoricalCatalogEntry(
                reporter_key=mention.reporter.key,
                title=title,
                normalized_title=title.upper().replace(" v. ", " "),
                date=mention.target_date,
                document_kind="judgment",
                target_ecli=target.ecli,
                target_itemid=target.itemid,
            )
        ],
    )
    resolution = asyncio.run(
        resolve_citations(
            [source],
            catalog=[target],
            authority=CitationAuthority(source_url="test", entries=[]),
            historical_catalog=historical,
        )
    ).resolutions[0]
    assert not resolution.resolved
    conflict = (
        "exact source document self-edge"
        if mode == "self"
        else "target date is after source document"
    )
    assert conflict in resolution.candidates[0].conflicting_evidence


def test_duplicate_itemid_catalog_key_is_ambiguous_not_last_write_wins():
    first = _case(
        itemid="001-duplicate-itemid",
        ecli="ECLI:CE:ECHR:2001:0101JUD000010000",
        appno="100/00",
        docname="CASE OF FIRST v. STATE",
        kpdate="2001-01-01",
    )
    second = first.model_copy(
        update={
            "ecli": "ECLI:CE:ECHR:2001:0101JUD000020000",
            "docname": "CASE OF SECOND v. STATE",
        }
    )
    source = _case(scl="HUDOC item 001-duplicate-itemid")
    mention = parse_scl_mentions(source)[0].model_copy(
        update={"explicit_itemid": "001-duplicate-itemid"}
    )
    resolution = asyncio.run(
        resolve_citations([source], mentions=[mention], catalog=[first, second])
    ).resolutions[0]
    assert resolution.status == "ambiguous_document"
    assert len(resolution.candidates) == 2


def test_explicit_ecli_resolves_exact_document():
    target = _albert_targets()[0]
    source = _case(scl=f"Albert and Le Compte v. Belgium, {target.ecli}")
    result = asyncio.run(resolve_citations([source], catalog=[target]))
    assert result.resolutions[0].status == "resolved_identifier"
    assert result.resolutions[0].target.node_id.startswith("ecli:")


def test_explicit_identifier_absent_from_catalog_is_reported_as_missing_target():
    source = _case(scl="Example v. State, ECLI:CE:ECHR:1999:0101JUD000000199")
    result = asyncio.run(resolve_citations([source], catalog=[]))
    assert result.resolutions[0].status == "target_not_in_hudoc"


def test_placeholder_target_cannot_enter_measurement_graph():
    target = _albert_targets()[0].model_copy(update={"is_placeholder": True})
    source = _case(scl=f"Albert and Le Compte v. Belgium, {target.ecli}")
    result = asyncio.run(resolve_citations([source], catalog=[target]))
    assert result.resolutions[0].status == "target_not_in_hudoc"
    assert result.edges == []


def test_source_specific_override_wins_and_is_audited():
    source = _case(scl="Albert and Le Compte v. Belgium, nos. 7299/75 and 7496/76")
    mention = parse_scl_mentions(source)[0]
    target = _albert_targets()[0]
    override = CitationOverride(
        reference_hash=mention.reference_hash,
        source_ecli=source.ecli,
        target_ecli=target.ecli,
        reviewer_note="Checked against the printed judgment date.",
        reviewed_at="2026-07-16",
    )
    result = asyncio.run(
        resolve_citations([source], catalog=_albert_targets(), overrides=[override])
    )
    resolution = result.resolutions[0]
    assert resolution.status == "resolved_override"
    assert resolution.override_note.startswith("Checked")


def test_invalid_override_target_fails_validation():
    source = _case(scl="Albert and Le Compte v. Belgium, Series A no. 58")
    mention = parse_scl_mentions(source)[0]
    override = CitationOverride(
        reference_hash=mention.reference_hash,
        target_ecli="ECLI:CE:ECHR:1900:0101JUD000000100",
    )
    with pytest.raises(ValueError, match="absent or a placeholder"):
        asyncio.run(resolve_citations([source], overrides=[override]))


def test_source_context_is_retained_for_review():
    source = _case(
        scl="Unknown v. State, Reports 1996-VI",
        text="The Court follows Unknown v. State, Reports 1996-VI, because the facts differ.",
    )
    result = asyncio.run(resolve_citations([source], catalog=[]))
    context = result.resolutions[0].mention.source_context
    assert context and "because the facts differ" in context


def test_artifacts_review_and_fail_closed_graph(tmp_path):
    source = _case(scl="Unknown v. State, Reports 1996-VI")
    result = asyncio.run(resolve_citations([source], catalog=[]))
    paths = write_resolution_artifacts(result, tmp_path / "citations")
    assert all(path.exists() for path in paths.values())
    with pytest.raises(IncompleteCitationResolutionError):
        CitationGraph.from_artifacts(tmp_path / "citations")
    graph = CitationGraph.from_artifacts(tmp_path / "citations", require_complete=False)
    with pytest.raises(IncompleteCitationResolutionError):
        graph.metrics_dataframe()

    count, review_html, overrides = write_review(
        tmp_path / "citations", tmp_path / "review.html", tmp_path / "overrides.csv"
    )
    assert count == 1 and "Unknown v. State" in review_html.read_text(encoding="utf-8")
    rows = list(csv.DictReader(overrides.open(encoding="utf-8")))
    assert rows[0]["reference_hash"]


def test_empty_scl_run_writes_valid_complete_artifacts(tmp_path):
    result = asyncio.run(resolve_citations([_case(scl=None)]))
    assert result.report.complete and result.report.mentions == 0
    write_resolution_artifacts(result, tmp_path)
    graph = CitationGraph.from_artifacts(tmp_path)
    assert graph.to_networkx().number_of_edges() == 0
    count, html_path, csv_path = write_review(
        tmp_path, tmp_path / "review.html", tmp_path / "overrides.csv"
    )
    assert count == 0 and html_path.exists() and csv_path.exists()


def test_legacy_graph_has_no_measurement_grade_provenance():
    graph = CitationGraph([_albert_source(), *_albert_targets()])
    graph.resolve()
    with pytest.raises(IncompleteCitationResolutionError, match="no authority"):
        graph.metrics_dataframe()


def test_complete_artifacts_build_ecli_graph(tmp_path):
    result = asyncio.run(resolve_citations([_albert_source()], catalog=_albert_targets()))
    write_resolution_artifacts(result, tmp_path)
    graph = CitationGraph.from_artifacts(tmp_path)
    network = graph.to_networkx()
    assert network.number_of_nodes() == 2
    assert network.number_of_edges() == 1
    edge = next(iter(network.edges(data=True)))[2]
    assert edge["citation_count"] == 1


def test_grouped_appnos_and_repeated_mentions_aggregate_to_one_ecli_edge():
    source = _case(
        scl=(
            "Albert and Le Compte v. Belgium, 10 February 1983, Series A no. 58; "
            "Albert and Le Compte v. Belgium, Series A no. 58"
        ),
        sclappnos="7299/75;7496/76",
    )
    result = asyncio.run(resolve_citations([source], catalog=_albert_targets()))
    assert result.report.complete
    assert len(result.edges) == 1
    assert result.edges[0]["citation_count"] == 2


def test_reporter_only_online_discovery_uses_unique_authority_title():
    class FakeClient:
        def __init__(self):
            self.queries = []

        async def search(self, **kwargs):
            self.queries.append(kwargs)
            if kwargs.get("docname") == "Albert and Le Compte v. Belgium":
                return [_albert_targets()[0]]
            return []

    client = FakeClient()
    source = _case(scl="Series A no. 58")
    result = asyncio.run(resolve_citations([source], client=client))
    assert result.resolutions[0].status == "resolved_authority"
    assert any(query.get("docname") for query in client.queries)


def test_online_lookup_cache_resumes_deterministically(tmp_path):
    class FakeClient:
        def __init__(self):
            self.calls = 0

        async def search(self, **kwargs):
            self.calls += 1
            return [_albert_targets()[0]] if kwargs.get("docname") else []

    source = _case(scl="Series A no. 58")
    cache = tmp_path / "lookup-cache.jsonl"
    first_client = FakeClient()
    first = asyncio.run(resolve_citations([source], client=first_client, cache_path=cache))
    second_client = FakeClient()
    second = asyncio.run(resolve_citations([source], client=second_client, cache_path=cache))
    assert first.report.model_dump() == second.report.model_dump()
    assert first_client.calls == 1
    assert second_client.calls == 0


def test_lookup_cache_is_an_offline_catalog_with_live_resolution_parity(tmp_path):
    class FakeClient:
        async def search(self, **kwargs):
            return [_albert_targets()[0]] if kwargs.get("docname") else []

    source = _case(scl="Series A no. 58")
    cache = tmp_path / "lookup-cache.jsonl"
    live = asyncio.run(resolve_citations([source], client=FakeClient(), cache_path=cache))
    offline = asyncio.run(resolve_citations([source], cache_path=cache))

    assert offline.report.model_dump() == live.report.model_dump()
    assert [value.model_dump(mode="json") for value in offline.resolutions] == [
        value.model_dump(mode="json") for value in live.resolutions
    ]
    assert offline.edges == live.edges


def test_placeholder_source_uses_non_placeholder_ecli_sibling():
    source = _case(scl=None, isplaceholder=True)
    sibling = source.model_copy(
        update={
            "itemid": "001-source-fre",
            "language": "FRE",
            "doctype": "HFJUD",
            "is_placeholder": False,
        }
    )

    class FakeClient:
        async def search(self, **kwargs):
            return [source, sibling] if kwargs.get("ecli") else []

    result = asyncio.run(resolve_citations([source], client=FakeClient()))
    assert result.report.placeholder_nodes == 0
    assert result.report.complete
    assert result.nodes[0]["itemid"] == "001-source-fre"


def test_placeholder_target_fetches_substantive_ecli_sibling():
    source = _case(
        scl="Pais Pires de Lima v. Portugal, no. 70465/12, 12 February 2019",
        kpdate="2026-01-01",
    )
    placeholder = _case(
        itemid="001-pais-eng",
        ecli="ECLI:CE:ECHR:2019:0212JUD007046512",
        appno="70465/12",
        docname="CASE OF PAIS PIRES DE LIMA v. PORTUGAL",
        kpdate="2019-02-12",
        isplaceholder=True,
    )
    sibling = placeholder.model_copy(
        update={
            "itemid": "001-pais-fre",
            "language": "FRE",
            "doctype": "HFJUD",
            "is_placeholder": False,
        }
    )

    class FakeClient:
        async def search(self, **kwargs):
            return [placeholder, sibling] if kwargs.get("ecli") else [placeholder]

    result = asyncio.run(resolve_citations([source], client=FakeClient()))
    resolution = result.resolutions[0]
    assert resolution.resolved
    assert resolution.target and resolution.target.itemid == sibling.itemid
    assert result.report.placeholder_nodes == 0


def test_advisory_request_identifier_drives_online_discovery():
    source = _case(
        scl=("Advisory opinion concerning an issue [GC], request no. P16-2018-001, 10 April 2019"),
        kpdate="2026-01-01",
    )
    target = _case(
        itemid="001-advisory",
        ecli="ECLI:CE:ECHR:2019:0410ADV000000118",
        appno=None,
        advopidentifier="P16-2018-001",
        docname="ADVISORY OPINION CONCERNING AN ISSUE",
        doctype="HEADO",
        doctypebranch="GRANDCHAMBER",
        kpdate="2019-04-10",
        documentcollectionid="CASELAW;ADVISORYOPINIONS;GRANDCHAMBER;ENG",
    )

    class FakeClient:
        def __init__(self):
            self.queries = []

        async def search(self, **kwargs):
            self.queries.append(kwargs)
            return [target] if kwargs.get("advop_identifier") else []

    client = FakeClient()
    result = asyncio.run(resolve_citations([source], client=client))
    assert result.resolutions[0].status == "resolved_metadata"
    assert any(query.get("advop_identifier") for query in client.queries)


def test_advisory_request_collapses_languages_and_excludes_legal_summary():
    source = _case(
        scl=("Advisory opinion concerning an issue [GC], request no. P16-2018-001, 10 April 2019"),
        kpdate="2026-01-01",
    )
    base = {
        "ecli": None,
        "appno": None,
        "advopidentifier": "P16-2018-001",
        "doctype": "ADVPRO16OPENG",
        "doctypebranch": "OPINIONS",
        "kpdate": "2019-04-10",
        "documentcollectionid": "CASELAW;ADVISORYOPINIONS;PROTOCOL16;OPINIONS;ENG",
    }
    english = _case(
        itemid="003-opinion-eng",
        docname="Advisory opinion concerning an issue",
        **base,
    )
    french = _case(
        itemid="003-opinion-fre",
        docname="Avis consultatif concernant une question",
        languageisocode="FRE",
        **base,
    )
    summary = _case(
        itemid="003-summary-eng",
        docname="Legal summary - Advisory opinion concerning an issue",
        **base,
    )
    result = asyncio.run(
        resolve_citations(
            [source],
            catalog=[english, french, summary],
            authority=CitationAuthority(source_url="test", entries=[]),
        )
    )
    resolution = result.resolutions[0]
    assert resolution.status == "resolved_metadata"
    assert resolution.target and resolution.target.itemid == english.itemid
    assert all(candidate.itemid != summary.itemid for candidate in resolution.candidates)


def test_reviewed_authority_can_record_target_as_unavailable(tmp_path):
    source = _case(scl="Historical v. State (dec.), 2001")
    authority = CitationAuthority(
        source_url="test",
        entries=[
            CitationAuthorityEntry(
                entry_id="unavailable",
                entry_source="curated_supplement",
                citation="Historical v. State (dec.), 2001",
                normalized_citation="historical v. state (dec.), 2001",
                title="Historical v. State",
                normalized_title="HISTORICAL STATE",
                target_unavailable=True,
                coverage_note="Registry-only decision not published in HUDOC",
            )
        ],
    )
    result = asyncio.run(resolve_citations([source], authority=authority))
    resolution = result.resolutions[0]
    assert resolution.status == "target_not_in_hudoc"
    assert resolution.method == "Registry-only decision not published in HUDOC"
    assert result.report.documented_exclusions == 1
    assert result.report.review_required == 0
    assert result.report.complete and result.report.completeness == 1.0
    assert result.edges == []

    write_resolution_artifacts(result, tmp_path / "citations")
    count, html_path, csv_path = write_review(
        tmp_path / "citations", tmp_path / "review.html", tmp_path / "overrides.csv"
    )
    assert count == 0
    assert "1 documented HUDOC exclusions require no override" in html_path.read_text(
        encoding="utf-8"
    )
    assert list(csv.DictReader(csv_path.open(encoding="utf-8"))) == []


def test_year_only_title_lookup_is_bounded_to_printed_year():
    source = _case(scl="Paul and Audrey Edwards v. the United Kingdom (dec.), 2001")

    class FakeClient:
        def __init__(self):
            self.queries = []

        async def search(self, **kwargs):
            self.queries.append(kwargs)
            return []

    client = FakeClient()
    asyncio.run(resolve_citations([source], client=client))
    title_query = next(query for query in client.queries if query.get("docname"))
    assert title_query["date_from"] == "2001-01-01"
    assert title_query["date_to"] == "2001-12-31"


def test_hudoc_failure_is_audited_and_does_not_create_an_edge(tmp_path):
    class FailingClient:
        async def search(self, **_kwargs):
            raise RuntimeError("upstream unavailable")

    result = asyncio.run(
        resolve_citations(
            [_case(scl="Example v. State, no. 12345/67")],
            client=FailingClient(),
            cache_path=tmp_path / "lookup-cache.jsonl",
        )
    )
    assert result.report.lookup_errors >= 1
    assert not result.resolutions[0].resolved
    assert result.edges == []
    assert "upstream unavailable" in (tmp_path / "lookup-cache.jsonl").read_text()


def test_authority_text_import_joins_wrapped_lines():
    text = """
    Case-law references
    European Court of Human Rights
    Albert and Le Compte v. Belgium, § ..., 10 February 1983,
    Series A no. 58
    Eckle v. Germany (Article 50), § ..., 21 June 1983, Series A no. 65
    2 / 446
    """
    citations = extract_authority_citations(text)
    assert len(citations) == 2
    assert citations[0].endswith("Series A no. 58")
    entry = authority_entry_from_citation(citations[1])
    assert entry.procedural_phase == "article_50"


def test_authority_parser_excludes_headers_footers_and_handles_edge_titles():
    text = """
    Case-law references
    X v. Croatia, no. 11223/04, § ..., 17 July 2008
    433/449 European Court of Human Rights
    Case “relating to certain aspects of the laws on the use of languages in education in Belgium”
    (merits), 23 July 1968, p. ..., § ..., Series A no. 6
    Associated Newspapers Limited v. the United Kingdom (just satisfaction – friendly settlement), no.
    37398/21, § ..., 25 November 2025
    """
    citations = extract_authority_citations(text)
    assert len(citations) == 3
    assert citations[0] == "X v. Croatia, no. 11223/04, § ..., 17 July 2008"
    assert "European Court of Human Rights" not in " ".join(citations)
    assert citations[1].startswith("Case “relating")
    assert citations[2].endswith("25 November 2025")


def test_authority_import_records_pdf_hash_edition_and_parser_snapshot(tmp_path, monkeypatch):
    pdf = tmp_path / "official.pdf"
    pdf.write_bytes(b"%PDF-fixed-authority-fixture")
    monkeypatch.setattr(
        authority_module,
        "_pdf_text",
        lambda _path: (
            "Updated until 10 July 2026\n"
            "Albert and Le Compte v. Belgium, 10 February 1983, § ...,\n"
            "Series A no. 58\n"
        ),
    )
    authority = authority_module.import_authority_pdf(
        pdf,
        tmp_path / "authority",
        retrieved_at="2026-08-20T00:00:00+00:00",
    )
    assert authority.updated_through == "2026-07-10"
    assert authority.parser_version == "7"
    assert authority.schema_version == "citation-authority/v2"
    assert authority.imported_at == "2026-08-20T00:00:00+00:00"
    assert authority.sources[0].language == "eng"
    assert authority.sources[0].retrieved_at == authority.imported_at
    assert authority.source_sha256 and len(authority.source_sha256) == 64
    assert authority.entries[0].reporter and authority.entries[0].reporter.number == "58"
    assert sum(entry.entry_source == "curated_supplement" for entry in authority.entries) == 6
    saved = json.loads((tmp_path / "authority" / "citation-authority.json").read_text())
    assert saved["coverage"] == "full"
    csv_rows = list(
        csv.DictReader((tmp_path / "authority" / "citation-authority.csv").open(encoding="utf-8"))
    )
    assert csv_rows[0]["reporter_key"] == "SERIES_A:::58::"
    assert csv_rows[0]["authority_coverage"] == "full"
    authority_module.import_authority_pdf(
        pdf,
        tmp_path / "authority-repeat",
        retrieved_at="2026-08-20T00:00:00+00:00",
    )
    assert (tmp_path / "authority" / "citation-authority.json").read_bytes() == (
        tmp_path / "authority-repeat" / "citation-authority.json"
    ).read_bytes()
    assert (tmp_path / "authority" / "citation-authority.csv").read_bytes() == (
        tmp_path / "authority-repeat" / "citation-authority.csv"
    ).read_bytes()


def test_french_authority_import_and_identity_safe_language_merge(tmp_path, monkeypatch):
    english_pdf = tmp_path / "english.pdf"
    french_pdf = tmp_path / "french.pdf"
    english_pdf.write_bytes(b"english")
    french_pdf.write_bytes(b"french")

    def text(path):
        if path == english_pdf:
            return (
                "Updated until: 17 July 2026\n"
                "Example v. France, no. 12345/20, § ..., 17 July 2026\n"
            )
        return (
            "Références de jurisprudence\nCour européenne des droits de l’homme\n"
            "Mis à jour jusqu’au : 17 juillet 2026\n"
            "Example c. France, no. 12345/20, § ..., 17 juillet 2026\n"
            "1/447 Cour européenne des droits de l’homme\n"
        )

    monkeypatch.setattr(authority_module, "_pdf_text", text)
    english = authority_module.import_authority_pdf(english_pdf, tmp_path / "eng", language="eng")
    french = authority_module.import_authority_pdf(french_pdf, tmp_path / "fra", language="fra")
    merged = authority_module.merge_authorities([english, french], tmp_path / "merged")

    assert french.updated_through == "2026-07-17"
    assert french.sources[0].language == "fra"
    official = [entry for entry in merged.entries if entry.entry_source == "official_master"]
    assert {entry.language for entry in official} == {"eng", "fra"}
    assert official[0].entry_id in official[1].equivalent_entry_ids
    assert official[1].entry_id in official[0].equivalent_entry_ids
    assert len(merged.sources) == 2


def test_merge_upgrades_legacy_v1_language_and_source_provenance():
    legacy = CitationAuthority(
        schema_version="citation-authority/v1",
        source_url="https://www.echr.coe.int/documents/d/echr/case_law_references_eng",
        updated_through="2026-07-10",
        source_sha256="a" * 64,
        entries=[
            authority_entry_from_citation("Example v. France, no. 12345/20, § ..., 17 July 2026")
        ],
    )
    french = CitationAuthority(
        schema_version="citation-authority/v2",
        source_url="https://www.echr.coe.int/documents/d/echr/Case_law_references_FRA",
        updated_through="2026-07-17",
        source_sha256="b" * 64,
        sources=[
            CitationAuthoritySource(
                language="fra",
                url="https://www.echr.coe.int/documents/d/echr/Case_law_references_FRA",
                retrieved_at="2026-07-17T00:00:00+00:00",
                updated_through="2026-07-17",
                sha256="b" * 64,
                entry_count=1,
            )
        ],
        entries=[
            authority_entry_from_citation(
                "Example c. France, no. 12345/20, § ..., 17 juillet 2026",
                language="fra",
            )
        ],
    )

    merged = authority_module.merge_authorities([legacy, french])

    assert {source.language for source in merged.sources} == {"eng", "fra"}
    official = [entry for entry in merged.entries if entry.entry_source == "official_master"]
    assert {entry.language for entry in official} == {"eng", "fra"}
    assert official[0].entry_id in official[1].equivalent_entry_ids
    assert official[1].entry_id in official[0].equivalent_entry_ids


def test_merge_never_uses_non_unique_reports_volume_as_identity():
    entries = [
        authority_entry_from_citation(
            "Alpha v. State, 23 September 1998, § ..., Reports 1998-VI",
            language="eng",
        ).model_copy(update={"equivalent_entry_ids": ["stale-link"]}),
        authority_entry_from_citation(
            "Beta v. State, 23 September 1998, § ..., Reports 1998-VI",
            language="eng",
        ),
        authority_entry_from_citation(
            "Alpha c. État, 23 septembre 1998, § ..., Recueil 1998-VI",
            language="fra",
        ),
    ]
    english = CitationAuthority(source_url="eng", entries=entries[:2])
    french = CitationAuthority(source_url="fra", entries=entries[2:])

    merged = authority_module.merge_authorities([english, french])

    assert all(not entry.equivalent_entry_ids for entry in merged.entries)


def test_merge_requires_one_entry_per_language_for_equivalence():
    entries = [
        authority_entry_from_citation(
            "Alpha v. State, no. 12345/20, § ..., 17 July 2026",
            language="eng",
        ),
        authority_entry_from_citation(
            "Alpha (merits) v. State, no. 12345/20, § ..., 17 July 2026",
            language="eng",
        ),
        authority_entry_from_citation(
            "Alpha c. État, no 12345/20, § ..., 17 juillet 2026",
            language="fra",
        ),
    ]
    english = CitationAuthority(source_url="eng", entries=entries[:2])
    french = CitationAuthority(source_url="fra", entries=entries[2:])

    merged = authority_module.merge_authorities([english, french])

    assert all(not entry.equivalent_entry_ids for entry in merged.entries)


def test_merge_deduplicates_supplement_now_present_in_official_edition():
    official = authority_entry_from_citation(
        "Alpha v. State, no. 12345/20, § ..., 17 July 2026",
        language="eng",
    )
    supplement = official.model_copy(
        update={
            "entry_id": "reviewed-alpha",
            "entry_source": "curated_supplement",
            "language": None,
            "target_itemid": "001-target",
        }
    )
    english = CitationAuthority(source_url="eng", entries=[official])
    french = CitationAuthority(source_url="fra", entries=[supplement])

    merged = authority_module.merge_authorities([english, french])

    assert len(merged.entries) == 1
    assert merged.entries[0].entry_source == "official_master"
    assert merged.entries[0].target_itemid == "001-target"


def test_packaged_authority_has_full_provenance_and_supplements():
    authority = load_authority()
    assert authority.source_url.startswith("https://www.echr.coe.int/")
    assert authority.schema_version == "citation-authority/v2"
    assert authority.updated_through == "2026-07-27"
    assert authority.parser_version == "7"
    assert authority.coverage == "full"
    assert authority.source_sha256 and len(authority.source_sha256) == 64
    assert len(authority.sources) == 2
    assert {source.language for source in authority.sources} == {"eng", "fra"}
    assert {source.entry_count for source in authority.sources} == {21_083, 21_078}
    assert len(authority.entries) == 42_167
    assert sum(entry.entry_source == "curated_supplement" for entry in authority.entries) == 6
    linked = [entry for entry in authority.entries if entry.equivalent_entry_ids]
    assert len(linked) == 41_676
    by_id = {entry.entry_id: entry for entry in authority.entries}
    assert all(len(entry.equivalent_entry_ids) == 1 for entry in linked)
    assert all(by_id[entry.equivalent_entry_ids[0]].language != entry.language for entry in linked)


def test_packaged_authority_representative_rows_are_clean_and_structured():
    authority = load_authority()
    by_prefix = {
        prefix: next(entry for entry in authority.entries if entry.citation.startswith(prefix))
        for prefix in (
            "X v. Croatia, no. 11223/04",
            "Sporrong and Lönnroth v. Sweden (Article 50)",
            "A. v. Italy (friendly settlement)",
            "Zwierzyński v. Poland (just satisfaction)",
            "Zwierzyński v. Poland (revision)",
            "Case “relating to certain aspects",
            "Association “21 December 1989”",
        )
    }
    assert by_prefix["X v. Croatia, no. 11223/04"].appnos == ["11223/04"]
    assert (
        by_prefix["Sporrong and Lönnroth v. Sweden (Article 50)"].procedural_phase == "article_50"
    )
    assert by_prefix["A. v. Italy (friendly settlement)"].procedural_phase == "friendly_settlement"
    assert (
        by_prefix["Zwierzyński v. Poland (just satisfaction)"].procedural_phase
        == "just_satisfaction"
    )
    assert by_prefix["Zwierzyński v. Poland (revision)"].procedural_phase == "revision"
    assert by_prefix["Case “relating to certain aspects"].reporter.number == "6"
    assert by_prefix["Association “21 December 1989”"].date.isoformat() == "2011-05-24"
    assert not any(
        "European Court of Human Rights" in entry.citation or "/449" in entry.citation
        for entry in authority.entries
    )
    by_id = {entry.entry_id: entry for entry in authority.entries}
    assert by_id["advisory-competence-2004"].target_itemid == "003-1339293-1397515"
    assert by_id["paul-audrey-edwards-admissibility-2001"].target_unavailable is True
    assert by_id["temeltasch-dr-31-130"].target_ecli == ("ECLI:CE:ECHR:1983:0305REP000911680")


def test_resolution_report_json_contract():
    result = asyncio.run(resolve_citations([_albert_source()], catalog=_albert_targets()))
    payload = json.loads(result.report.model_dump_json())
    assert payload["schema_version"] == "citation-resolution/v1"
    assert payload["complete"] is True
    assert payload["completeness"] == 1.0
    assert payload["authority_coverage"] == "full"
    assert payload["authority_entry_count"] == 42_167
    assert payload["authority_supplement_count"] == 6


def test_cli_resolution_review_and_measurement_metadata(tmp_path):
    source = _case(scl=("Albert and Le Compte v. Belgium, ECLI:CE:ECHR:1983:0210JUD000729975"))
    target = _albert_targets()[0]
    source_path = tmp_path / "sources.parquet"
    catalog_path = tmp_path / "catalog.parquet"
    pd.DataFrame([source.model_dump(mode="json")]).to_parquet(source_path, index=False)
    pd.DataFrame([target.model_dump(mode="json")]).to_parquet(catalog_path, index=False)
    resolution_dir = tmp_path / "citations"
    assert (
        cli_main(
            [
                "citations",
                "resolve",
                "--in",
                str(source_path),
                "--catalog",
                str(catalog_path),
                "--offline",
                "--out",
                str(resolution_dir),
            ]
        )
        == 0
    )

    metrics = tmp_path / "metrics.csv"
    assert (
        cli_main(
            [
                "graph",
                "metrics",
                "--citations",
                str(resolution_dir),
                "--weight",
                "citation_count",
                "--out",
                str(metrics),
            ]
        )
        == 0
    )
    metadata = json.loads((tmp_path / "metrics.csv.metadata.json").read_text())
    assert metadata["measurement_grade"] is True
    assert metadata["weighting"] == "citation_count"

    gexf = tmp_path / "network.gexf"
    assert (
        cli_main(
            [
                "graph",
                "gexf",
                "--citations",
                str(resolution_dir),
                "--weight",
                "citation_count",
                "--out",
                str(gexf),
            ]
        )
        == 0
    )
    assert gexf.read_text(encoding="utf-8").startswith("<?xml")

    html = tmp_path / "review.html"
    csv_path = tmp_path / "overrides.csv"
    assert (
        cli_main(
            [
                "citations",
                "review",
                "--resolution-dir",
                str(resolution_dir),
                "--out",
                str(html),
                "--csv",
                str(csv_path),
            ]
        )
        == 0
    )
    assert html.exists() and csv_path.exists()
