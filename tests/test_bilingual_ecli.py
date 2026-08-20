"""Tests for ECLI normalization and duplicate-cluster classification."""

from __future__ import annotations

from hudoc_py.bilingual import (
    classify_ecli_cluster,
    docname_similarity,
    normalize_appnos,
    normalize_docname,
    normalize_ecli,
)
from hudoc_py.models import Case


def _case(**kw) -> Case:
    return Case.model_validate({"itemid": "x", **kw})


def test_normalize_ecli_strips_whitespace_and_uppercases():
    assert normalize_ecli(" ecli:ce:echr:2014 :1003 ") == "ECLI:CE:ECHR:2014:1003"
    assert normalize_ecli("") is None
    assert normalize_ecli(None) is None
    assert normalize_ecli("   ") is None


def test_normalize_docname_drops_party_tokens_cross_language():
    en = normalize_docname("CASE OF AKSOY v. THE UNITED KINGDOM")
    fr = normalize_docname("AFFAIRE AKSOY c. LE ROYAUME-UNI")
    # "v."/"c." and articles removed; core tokens remain comparable.
    assert "AKSOY" in en and "AKSOY" in fr
    assert " V " not in f" {en} " and " C " not in f" {fr} "


def test_normalize_appnos():
    assert normalize_appnos(["46221/99", " 12738/10 ", ""]) == frozenset(
        {"46221/99", "12738/10"}
    )


def test_docname_similarity():
    assert docname_similarity("A v. FRANCE", "A c. FRANCE") >= 0.85
    assert docname_similarity("", "A v. FRANCE") == 0.0
    assert docname_similarity("SMITH v. UK", "JONES v. ITALY") < 0.85


def test_classify_unique():
    assert classify_ecli_cluster([]) == "unique"
    assert classify_ecli_cluster([_case()]) == "unique"


def test_classify_same_language_duplicate():
    cases = [
        _case(itemid="001-1", languageisocode="ENG", appno="1/00"),
        _case(itemid="001-2", languageisocode="ENG", appno="1/00"),
    ]
    assert classify_ecli_cluster(cases) == "same_language_duplicate"


def test_classify_matched_by_appno_docname_exact():
    cases = [
        _case(languageisocode="ENG", appno="1/00", docname="CASE OF A v. FRANCE"),
        _case(languageisocode="FRE", appno="1/00", docname="AFFAIRE A c. FRANCE"),
    ]
    assert classify_ecli_cluster(cases) == "matched_by_appno_docname"


def test_classify_matched_by_similarity_boundary():
    # Same appnos, titles differ only by transliteration → similarity carries it.
    cases = [
        _case(languageisocode="ENG", appno="1/00", docname="CASE OF ZUPANCIC v. SLOVENIA"),
        _case(languageisocode="FRE", appno="1/00", docname="AFFAIRE ZUPANCIC c. SLOVENIE"),
    ]
    assert classify_ecli_cluster(cases) == "matched_by_appno_docname"


def test_classify_ambiguous_differing_appnos():
    cases = [
        _case(languageisocode="ENG", appno="1/00", docname="CASE OF A v. FRANCE"),
        _case(languageisocode="FRE", appno="2/00", docname="AFFAIRE B c. ITALIE"),
    ]
    assert classify_ecli_cluster(cases) == "ambiguous"
