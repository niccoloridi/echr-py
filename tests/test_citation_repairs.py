"""Repairs from the ICJeocities offline evaluation of 0.2.5 (October 2026).

Letters that Unicode decomposition does not fold, encoding-damaged SCL names,
and the pre-1998 house style ("the Golder judgment of ...", "its judgment of
<date> in the Engel and Others case"). Offline throughout.
"""

from __future__ import annotations

import asyncio

import pytest

from hudoc_py.bilingual.ecli import normalize_docname
from hudoc_py.citations import (
    discover_citation_mentions,
    extract_citation_occurrences,
    parse_scl_mentions,
    resolve_citations,
)
from hudoc_py.citations.models import CitationCandidate, CitationResolution
from hudoc_py.citations.occurrences import _key, scl_name_encoding_damaged
from hudoc_py.models import Case
from hudoc_py.text.fold import fold_letters
from hudoc_py.text.judges import _fold as judge_fold


@pytest.mark.parametrize(
    ("printed", "metadata", "key"),
    [
        ("Kudła v. Poland", "KUDLA v. POLAND", "kudla v poland"),
        ("Ølgaard", "OLGAARD", "olgaard"),
        ("Öneryıldız v. Turkey", "ONERYILDIZ v. TURKEY", "oneryildiz v turkey"),
        ("Đurović", "DUROVIC", "durovic"),
        ("Þór", "THOR", "thor"),
        ("Æsir", "AESIR", "aesir"),
        ("Straße", "STRASSE", "strasse"),
    ],
)
def test_identity_keys_fold_letters_that_nfkd_leaves_intact(printed, metadata, key):
    assert _key(printed) == _key(metadata) == key
    assert normalize_docname(printed) == normalize_docname(metadata)
    assert fold_letters(printed) == fold_letters(metadata).casefold()


def test_resolver_title_index_meets_hudoc_spelling():
    assert normalize_docname("Kudła v. Poland") == normalize_docname("CASE OF KUDLA v. POLAND")


def test_judge_roster_fold_is_unchanged_by_the_shared_rule():
    assert judge_fold("Zupančič") == "ZUPANCIC"
    assert judge_fold("Thór Vilhjálmsson") == "THOR VILHJALMSSON"
    assert judge_fold("Mijović") == "MIJOVIC"


@pytest.mark.parametrize(
    ("name", "damaged"),
    [
        ("Kudÿa v. Poland", True),
        ("Grzinÿiÿ v. Slovenia", True),
        ("CafÃ© v. France", True),
        ("Name� v. State", True),
        ("Kudła v. Poland", False),
        ("L'Haÿ-les-Roses v. France", False),
    ],
)
def test_scl_encoding_damage_detector(name, damaged):
    assert scl_name_encoding_damaged(name) is damaged


def test_printed_name_replaces_an_encoding_damaged_scl_name_for_later_short_forms():
    case = Case(
        itemid="001-100413",
        languageisocode="ENG",
        scl="Kudÿa v. Poland ([GC], no. 30210/96, §§ 152, 158, ECHR 2000-XI",
    )
    html = (
        "<p>10. See Kudła v. Poland [GC], no. 30210/96, § 152, ECHR 2000‑XI.</p>"
        "<p>11. The remedy must be effective (Kudła v. Poland [GC], cited above, § 158).</p>"
    )
    mention = parse_scl_mentions(case)[0]
    resolution = CitationResolution(
        mention=mention,
        status="resolved_metadata",
        method="fixture",
        target=CitationCandidate(node_id="ecli:kudla", itemid="001-58920"),
    )
    result = extract_citation_occurrences(case, [resolution], html=html, scope="inclusive")
    located = [o for o in result.occurrences if "Kud" in o.raw_text]
    assert len(located) == 2, [o.raw_text for o in result.occurrences]
    assert {o.target_itemid for o in located} == {"001-58920"}
    assert any(o.source_para_id == "11" for o in located)
    damaged = [d for d in result.diagnostics if d.get("code") == "scl_name_encoding_damaged"]
    assert damaged and damaged[0]["scl_name"] == "Kudÿa v. Poland"
    assert damaged[0]["printed_name"] == "Kudła v. Poland"
    assert not [
        d
        for d in result.diagnostics
        if d.get("code") == "unmatched_candidate" and "Kud" in str(d.get("raw_text"))
    ]


def _source(html: str) -> Case:
    return Case(
        itemid="001-source",
        ecli="ECLI:CE:ECHR:2000:0101JUD000000100",
        docname="CASE OF SOURCE v. STATE",
        text=html,
        scl="",
        kp_date="2000-01-01",
        doctype="HEJUD",
    )


def _catalog_case(itemid, ecli, docname, appnos, date, respondent):
    return Case(
        itemid=itemid,
        ecli=ecli,
        docname=docname,
        appno=appnos,
        kp_date=date,
        doctype="HEJUD",
        doctype_branch="CHAMBER",
        respondent=[respondent],
        languageisocode="ENG",
    )


def _resolve(html, catalog):
    source = _source(html)
    mentions = discover_citation_mentions(source, html=html).mentions
    return asyncio.run(resolve_citations([source], mentions=mentions, catalog=catalog)).resolutions


GOLDER = _catalog_case(
    "001-57496", "ECLI:CE:ECHR:1975:0221JUD000445170",
    "CASE OF GOLDER v. THE UNITED KINGDOM", ["4451/70"], "1975-02-21", "GBR",
)
KLASS = _catalog_case(
    "001-57510", "ECLI:CE:ECHR:1978:0906JUD000502971",
    "CASE OF KLASS AND OTHERS v. GERMANY", ["5029/71"], "1978-09-06", "DEU",
)
ENGEL_APPS = ["5100/71", "5101/71", "5102/71", "5354/72", "5370/72"]
ENGEL_MERITS = _catalog_case(
    "001-57479", "ECLI:CE:ECHR:1976:0608JUD000510071",
    "CASE OF ENGEL AND OTHERS v. THE NETHERLANDS", ENGEL_APPS, "1976-06-08", "NLD",
)
ENGEL_ARTICLE_50 = _catalog_case(
    "001-57480", "ECLI:CE:ECHR:1976:1123JUD000510071",
    "CASE OF ENGEL AND OTHERS v. THE NETHERLANDS (ARTICLE 50)", ENGEL_APPS, "1976-11-23", "NLD",
)


def test_applicant_only_series_a_citations_resolve_through_the_historical_catalog():
    html = (
        "<p>12. See the Golder judgment of 21 February 1975, Series A no. 18, p. 13, § 28.</p>"
        "<p>13. See the Klass and Others judgment of 6 September 1978, Series A no. 28, § 42.</p>"
    )
    resolved = {r.mention.raw_ref[:6]: r for r in _resolve(html, [GOLDER, KLASS])}
    assert resolved["Golder"].target.itemid == "001-57496"
    assert resolved["Klass "].target.itemid == "001-57510"
    assert resolved["Golder"].method == "packaged historical reporter catalog"


def test_applicant_only_citation_with_a_wrong_date_stays_unresolved():
    html = "<p>14. Contrast the Golder judgment of 22 February 1975, Series A no. 18.</p>"
    assert all(r.target is None for r in _resolve(html, [GOLDER]))


def test_printed_judgment_date_selects_one_document_under_an_authority_short_form():
    html = (
        "<p>14. In its judgment of 8 June 1976 in the Engel and Others case "
        "(Series A no. 22, § 82) the Court held.</p>"
        "<p>15. Contrast its judgment of 9 June 1976 in the Engel and Others case.</p>"
        "<p>16. As the Engel and Others judgment shows.</p>"
    )
    by_text = {r.mention.raw_ref: r for r in _resolve(html, [ENGEL_MERITS, ENGEL_ARTICLE_50])}
    merits = by_text["judgment of 8 June 1976 in the Engel and Others case"]
    assert merits.target.itemid == "001-57479"
    assert merits.mention.discovery_evidence["printed_target_date"] == "1976-06-08"
    assert by_text["judgment of 9 June 1976 in the Engel and Others case"].target is None
    assert by_text["Engel and Others judgment"].target is None


def test_dated_short_form_span_matches_the_source_text():
    html = "<p>14. In its judgment of 8 June 1976 in the Engel and Others case the Court held.</p>"
    source = _source(html)
    mention = discover_citation_mentions(source, html=html).mentions[0]
    text = "14. In its judgment of 8 June 1976 in the Engel and Others case the Court held."
    start = mention.discovery_evidence["block_start"]
    end = mention.discovery_evidence["block_end"]
    assert text[start:end] == mention.raw_ref


def test_repeated_applicant_name_stays_in_the_printed_title_envelope():
    html = (
        "<p>20. See, mutatis mutandis, Smirnova and Smirnova v. Russia (dec.), "
        "nos. 46133/99 and 48183/99, 3 October 2002.</p>"
    )
    mentions = discover_citation_mentions(_source(html), html=html).mentions
    assert [m.raw_ref.split(" (dec.)")[0] for m in mentions] == ["Smirnova and Smirnova v. Russia"]
    assert mentions[0].cited_name == "Smirnova and Smirnova v. Russia"


def test_a_different_co_applicant_is_not_guessed_into_the_title():
    html = "<p>20. See Petrov and Smirnova v. Russia (dec.), no. 46133/99, 3 October 2002.</p>"
    mentions = discover_citation_mentions(_source(html), html=html).mentions
    assert mentions[0].raw_ref.startswith("Smirnova v. Russia")


GLASENAPP = _catalog_case(
    "001-57494", "ECLI:CE:ECHR:1986:0828JUD000922880",
    "CASE OF GLASENAPP v. GERMANY", ["9228/80"], "1986-08-28", "DEU",
)
KOSIEK = _catalog_case(
    "001-57513", "ECLI:CE:ECHR:1986:0828JUD000970482",
    "CASE OF KOSIEK v. GERMANY", ["9704/82"], "1986-08-28", "DEU",
)


def test_paired_series_a_judgments_keep_their_own_pinpoints():
    html = (
        "<p>71. Civil servants are not outside the Convention (see the Glasenapp and the "
        "Kosiek judgments of 28 August 1986, Series A no. 104, p. 26, §§ 49-50, and "
        "Series A no. 105, p. 20, §§ 35-36).</p>"
    )
    source = _source(html).model_copy(
        update={
            "scl": "Glasenapp judgment of 28 August 1986, Series A no. 104, p. 26, paras. 49-50;"
            "Kosiek judgment of 28 August 1986, Series A no. 105, p. 20, paras. 35-36"
        }
    )
    catalog = [GLASENAPP, KOSIEK]
    mentions = discover_citation_mentions(source, html=html).mentions
    resolutions = [
        *asyncio.run(resolve_citations([source], catalog=catalog)).resolutions,
        *asyncio.run(resolve_citations([source], mentions=mentions, catalog=catalog)).resolutions,
    ]
    result = extract_citation_occurrences(source, resolutions, html=html, scope="inclusive")
    by_target = {}
    for occurrence in result.occurrences:
        by_target.setdefault(occurrence.target_itemid, []).append(occurrence)
    assert [o.target_paragraphs for o in by_target["001-57494"]] == [["49-50"]]
    assert [(o.raw_text, o.target_paragraphs) for o in by_target["001-57513"]] == [
        ("Series A no. 105", ["35-36"])
    ]
    assert any(d["code"] == "nested_compound_name_merged" for d in result.diagnostics)
