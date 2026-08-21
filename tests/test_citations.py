"""Tests for citation extraction and the citation graph."""

from __future__ import annotations

import pytest

from hudoc_py.citations import (
    APPNO_REGEX,
    CitationGraph,
    external_source_authority,
    extract_citations,
    match_external_source,
    parse_external_sources,
    parse_scl,
)
from hudoc_py.models import Case

# Real-shape Öcalan SCL fragment (4 refs, FR-style).
SCL_SAMPLE = (
    "Abbas Sertkaya c. Turquie, (déc.) no 77113/01, 11 décembre 2003, p. 4;"
    "Ahmed c. Autriche, arrêt du 17 décembre 1996, Recueil 1996-VI, p. 2207, § 43;"
    "Aquilina c. Malte [GC], no 25642/94, § 49, CEDH 1999-III;"
    "Streletz, Kessler et Krenz c. Allemagne [GC], nos 34044/96 et 2 autres, § 89, CEDH 2001-II"
)


def _make_case(**kwargs) -> Case:
    """Build a Case with sensible defaults for graph tests."""
    base = {
        "itemid": kwargs.pop("itemid", "001-1"),
        "docname": kwargs.pop("docname", "FOO v. STATE"),
        "ecli": kwargs.pop("ecli", "ECLI:CE:ECHR:2020:0101JUD000000001"),
        "language": kwargs.pop("language", "ENG"),
    }
    base.update(kwargs)
    return Case.model_validate(base)


# ----- parse_scl / extract_citations -----------------------------------


def test_parse_scl_splits_on_semicolons():
    refs = parse_scl(SCL_SAMPLE)
    assert len(refs) == 4


def test_parse_scl_extracts_appnos():
    refs = parse_scl(SCL_SAMPLE)
    abbas = next(r for r in refs if "Abbas" in r[0])
    ahmed = next(r for r in refs if "Ahmed" in r[0])
    aquilina = next(r for r in refs if "Aquilina" in r[0])
    streletz = next(r for r in refs if "Streletz" in r[0])
    assert abbas[2] == ["77113/01"]
    assert ahmed[2] == []  # pre-1998 Series A, no appno in standard form
    assert aquilina[2] == ["25642/94"]
    assert streletz[2] == ["34044/96"]


def test_parse_scl_captures_case_names():
    refs = parse_scl(SCL_SAMPLE)
    names = [r[1] for r in refs]
    assert any(n and "Abbas Sertkaya" in n for n in names)
    assert any(n and "Aquilina" in n for n in names)


def test_parse_scl_handles_empty():
    assert parse_scl(None) == []
    assert parse_scl("") == []
    assert parse_scl("   ") == []


def test_appno_regex_rejects_short_numbers():
    # HUDOC's extractedappno sometimes contains paragraph refs like "38/40".
    # Our regex requires 3-5 digits so those are filtered.
    assert APPNO_REGEX.findall("§ 38/40") == []
    assert APPNO_REGEX.findall("§ 3/83") == []
    assert APPNO_REGEX.findall("no 12345/99") == ["12345/99"]
    assert APPNO_REGEX.findall("nos 1234/00 et 5678/01") == ["1234/00", "5678/01"]


def test_extract_citations_attaches_source_metadata():
    case = _make_case(itemid="001-100", appno="46221/99", scl=SCL_SAMPLE)
    citations = extract_citations(case)
    assert len(citations) == 4
    assert all(c.source_itemid == "001-100" for c in citations)
    assert all(c.source_appno == ["46221/99"] for c in citations)
    assert all(c.resolved is False for c in citations)


# ----- CitationGraph ----------------------------------------------------


def test_graph_resolves_appnos_to_corpus_cases():
    aquilina = _make_case(itemid="001-200", appno="25642/94", docname="AQUILINA v. MALTA")
    streletz = _make_case(itemid="001-300", appno="34044/96", docname="STRELETZ ET AL v. GERMANY")
    ocalan = _make_case(itemid="001-100", appno="46221/99", scl=SCL_SAMPLE, language="ENG")

    g = CitationGraph([ocalan, aquilina, streletz])
    g.resolve()
    stats = g.stats()
    assert stats["total_refs"] == 4
    assert stats["resolved"] == 2  # Aquilina + Streletz
    assert stats["unresolved"] == 2  # Abbas (not in corpus) + Ahmed (no appno)


def test_graph_prefers_same_language_when_ambiguous():
    # Two cases share appno 25642/94 – one ENG, one FRE. Source is ENG.
    eng = _make_case(itemid="001-200E", appno="25642/94", language="ENG", docname="A v. M")
    fre = _make_case(itemid="001-200F", appno="25642/94", language="FRE", docname="A c. M")
    source = _make_case(
        itemid="001-100",
        appno="46221/99",
        scl="Aquilina v. Malta [GC], no 25642/94, § 49",
        language="ENG",
    )

    g = CitationGraph([source, eng, fre])
    g.resolve()
    [resolved] = [c for c in g.citations if c.resolved]
    assert resolved.cited_itemid == "001-200E"


def test_graph_resolves_via_extractedappno_only_when_opted_in():
    """extractedappno is a noisy fallback; require explicit opt-in."""
    target = _make_case(itemid="001-200", appno="", extractedappno="25642/94")
    source = _make_case(itemid="001-100", scl="Aquilina v. Malta [GC], no 25642/94")

    # Default: extracted_appno is NOT consulted, so no resolution.
    g_strict = CitationGraph([source, target])
    g_strict.resolve()
    assert g_strict.stats()["resolved"] == 0

    # Opt-in: extracted_appno is used as fallback.
    g_loose = CitationGraph([source, target], use_extracted_appno=True)
    g_loose.resolve()
    assert g_loose.stats()["resolved"] == 1


def test_graph_prefers_primary_appno_over_extracted_when_both_match():
    """The reliable case (appno in primary field) should win over the noisy one."""
    primary = _make_case(itemid="001-200", appno="25642/94", language="ENG", docname="A v. M")
    noisy = _make_case(
        itemid="001-999", appno="99999/99", extractedappno="25642/94", language="ENG"
    )
    source = _make_case(itemid="001-100", scl="Aquilina v. Malta [GC], no 25642/94", language="ENG")

    g = CitationGraph([source, primary, noisy], use_extracted_appno=True)
    g.resolve()
    [resolved] = [c for c in g.citations if c.resolved]
    assert resolved.cited_itemid == "001-200"


def test_graph_does_not_self_cite():
    """An appno match against the SOURCE case should never resolve as a self-edge."""
    source = _make_case(itemid="001-100", appno="46221/99", scl="Some prior case, no 46221/99")
    g = CitationGraph([source])
    g.resolve()
    assert g.stats()["resolved"] == 0


def test_dataframes_have_expected_columns():
    pd = pytest.importorskip("pandas")
    aquilina = _make_case(itemid="001-200", appno="25642/94")
    source = _make_case(itemid="001-100", appno="46221/99", scl=SCL_SAMPLE)
    g = CitationGraph([source, aquilina])
    g.resolve()

    nodes = g.nodes_dataframe()
    assert isinstance(nodes, pd.DataFrame)
    assert {"itemid", "docname", "ecli", "appno"} <= set(nodes.columns)
    assert len(nodes) == 2

    edges = g.edges_dataframe()
    assert {"source_itemid", "cited_itemid", "raw_ref", "resolved"} <= set(edges.columns)
    assert len(edges) >= 1


def test_missing_refs_bucket_surfaces_pre1998_cases():
    source = _make_case(itemid="001-100", scl=SCL_SAMPLE)
    g = CitationGraph([source])
    g.resolve()
    missing = g.missing_refs_dataframe()
    # All 4 refs end up unresolved (corpus has only the source)
    assert len(missing) >= 1
    # Ahmed (no appno) is definitely in there
    assert missing["raw_ref"].str.contains("Ahmed").any()


def test_collection_split_resolved_unresolved():
    aquilina = _make_case(itemid="001-200", appno="25642/94")
    source = _make_case(itemid="001-100", scl=SCL_SAMPLE)
    g = CitationGraph([source, aquilina])
    g.resolve()
    assert len(g.citations.resolved) >= 1
    assert len(g.citations.unresolved) >= 1
    assert len(g.citations) == len(g.citations.resolved) + len(g.citations.unresolved)


def test_to_networkx_builds_digraph():
    pytest.importorskip("networkx")
    aquilina = _make_case(itemid="001-200", appno="25642/94", docname="A v. M")
    source = _make_case(itemid="001-100", appno="46221/99", scl=SCL_SAMPLE)
    g = CitationGraph([source, aquilina])
    g.resolve()
    graph = g.to_networkx()
    assert graph.is_directed()
    assert "001-100" in graph.nodes
    assert "001-200" in graph.nodes
    assert ("001-100", "001-200") in graph.edges


# ---------------------------------------------------------------------------
# HUDOC "International Law" field (``externalsources``)
# ---------------------------------------------------------------------------

# Real-shape fragment from Janowiec and Others v. Russia (001-144276).
EXTERNAL_SAMPLE = (
    "International Criminal Tribunal for the Former Yugoslavia, Furundžija case, "
    "judgment of 10 December 1998;"
    "UN Human Rights Committee, General Comment 20, Article 7 (Forty-fourth session, 1992);"
    "Inter-American Court of Human Rights, Gomes Lund v. Brazil (judgment of "
    "24 November 2010, Preliminary Objections, Merits, Reparations and Costs)"
)


def test_parse_external_sources_splits_and_dedupes():
    entries = parse_external_sources(EXTERNAL_SAMPLE)
    assert len(entries) == 3
    assert entries[2].startswith("Inter-American Court of Human Rights, Gomes Lund")
    assert parse_external_sources("a;a;b") == ["a", "b"]


def test_parse_external_sources_handles_empty():
    assert parse_external_sources(None) == []
    assert parse_external_sources("") == []
    assert parse_external_sources("   ;  ") == []


def test_match_external_source_identifies_non_ecthr_authority():
    entries = parse_external_sources(EXTERNAL_SAMPLE)
    hit = match_external_source("Gomes Lund v. Brazil", entries)
    assert hit is not None and "Inter-American Court" in hit


def test_match_external_source_is_accent_insensitive():
    entries = parse_external_sources(EXTERNAL_SAMPLE)
    assert match_external_source("Furundzija", entries) is not None


def test_match_external_source_ignores_unrelated_strasbourg_case():
    entries = parse_external_sources(EXTERNAL_SAMPLE)
    assert match_external_source("Nikolova v. Bulgaria", entries) is None


def test_match_external_source_never_matches_on_state_words_alone():
    """"D. v. the United Kingdom" must not attach to an unrelated entry."""
    entries = parse_external_sources(
        "UN Human Rights Committee, Concluding observations, United Kingdom, 1 April 1997"
    )
    assert match_external_source("D. v. the United Kingdom", entries) is None


def test_match_external_source_skips_strasbourg_entries():
    entries = parse_external_sources(
        "European Court of Human Rights, Soering v. the United Kingdom, 7 July 1989"
    )
    assert match_external_source("Soering v. the United Kingdom", entries) is None


def test_match_external_source_requires_a_name():
    assert match_external_source(None, ["anything"]) is None
    assert match_external_source("", ["anything"]) is None


def test_external_source_authority_reads_the_case_field():
    case = _make_case(itemid="001-144276", external_sources=EXTERNAL_SAMPLE)
    assert external_source_authority(case, "Gomes Lund v. Brazil") is not None
    assert external_source_authority(case, "Nikolova v. Bulgaria") is None


def test_external_source_authority_handles_absent_field():
    case = _make_case(itemid="001-000", external_sources=None)
    assert external_source_authority(case, "Gomes Lund v. Brazil") is None
