"""Tests for ENG/FRE reconciliation."""

from __future__ import annotations

import pytest

from hudoc_py.bilingual import ReconcileInvariantError, reconcile
from hudoc_py.models import Case, CaseCollection


def _c(itemid, lang, ecli, *, appno="1/00", docname="CASE", rep="") -> Case:
    return Case.model_validate(
        {
            "itemid": itemid,
            "languageisocode": lang,
            "ecli": ecli,
            "appno": appno,
            "docname": docname,
            "representedby": rep,
        }
    )


def _fixture() -> CaseCollection:
    return CaseCollection(
        [
            # 1. Matched ENG+FRE pair; FRE has representedby, ENG does not.
            _c("e1", "ENG", "ECLI:A", docname="CASE OF A v. FRANCE"),
            _c("f1", "FRE", "ECLI:A", docname="AFFAIRE A c. FRANCE", rep="DUPONT M."),
            # 2. Matched pair; ENG already has representedby (must NOT overwrite).
            _c("e2", "ENG", "ECLI:B", rep="SMITH J."),
            _c("f2", "FRE", "ECLI:B", rep="MARTIN P."),
            # 3. ENG with two FRE siblings – one has rep (preferred), extra dropped.
            _c("e3", "ENG", "ECLI:C"),
            _c("f3a", "FRE", "ECLI:C"),  # no rep
            _c("f3b", "FRE", "ECLI:C", rep="LEROY A."),  # rep → preferred sibling
            # 4. Same-language duplicate (HUDOC re-index): two ENG under one ECLI.
            _c("e4", "ENG", "ECLI:D"),
            _c("e4dup", "ENG", "ECLI:D"),
            # 5. Duplicate itemid within ENG (pagination artifact).
            _c("e5", "ENG", "ECLI:E"),
            _c("e5", "ENG", "ECLI:E"),
            # 6. FRE-only (no ENG): promoted to primary.
            _c("f6", "FRE", "ECLI:F"),
            # 7. No ECLI: passes through.
            _c("e7", "ENG", ""),
            # 8. Other language: passes through.
            _c("g8", "DEU", "ECLI:H"),
        ]
    )


def test_matched_pair_sets_french_itemid_and_backfills():
    result = reconcile(_fixture())
    by_id = {c.itemid: c for c in result.cases}
    assert by_id["e1"].french_itemid == "f1"
    assert by_id["e1"].represented_by == "DUPONT M."  # backfilled from FRE


def test_backfill_never_overwrites_existing_eng_representedby():
    result = reconcile(_fixture())
    by_id = {c.itemid: c for c in result.cases}
    assert by_id["e2"].represented_by == "SMITH J."  # unchanged


def test_sibling_pick_prefers_representedby():
    result = reconcile(_fixture())
    by_id = {c.itemid: c for c in result.cases}
    assert by_id["e3"].french_itemid == "f3b"  # the one with a representative


def test_extra_fre_dropped_by_default():
    result = reconcile(_fixture())
    dup_ids = {c.itemid for c in result.duplicates}
    assert "f3a" in dup_ids
    assert result.stats.fre_extra_dropped == 1


def test_same_language_duplicate_dropped():
    result = reconcile(_fixture())
    primary_ids = {c.itemid for c in result.cases}
    assert "e4" in primary_ids
    assert "e4dup" not in primary_ids
    dup_ids = {c.itemid for c in result.duplicates}
    assert "e4dup" in dup_ids


def test_itemid_pagination_duplicate_dropped():
    result = reconcile(_fixture())
    # e5 appears once as primary; the second identical row is a duplicate.
    assert sum(1 for c in result.cases if c.itemid == "e5") == 1


def test_fre_only_promoted():
    result = reconcile(_fixture())
    by_id = {c.itemid: c for c in result.cases}
    assert "f6" in by_id
    assert by_id["f6"].language == "FRE"
    assert by_id["f6"].french_itemid is None
    assert result.stats.fre_unmatched == 1


def test_no_ecli_and_other_language_pass_through():
    result = reconcile(_fixture())
    ids = {c.itemid for c in result.cases}
    assert "e7" in ids  # no ECLI
    assert "g8" in ids  # other language
    assert result.stats.no_ecli_rows == 1
    assert result.stats.other_language_rows == 1


def test_one_primary_per_ecli_invariant_holds():
    result = reconcile(_fixture())
    ecolis = [c.ecli for c in result.cases if c.ecli]
    assert len(ecolis) == len(set(ecolis))


def test_input_collection_not_mutated():
    fixture = _fixture()
    before = fixture[0].french_itemid
    reconcile(fixture)
    assert fixture[0].french_itemid == before  # still None


def test_keep_policy_reproduces_duplicate_primaries():
    result = reconcile(_fixture(), extra_sibling_policy="keep")
    # f3a (extra FRE under a matched ECLI) becomes a primary under "keep".
    primary_ids = {c.itemid for c in result.cases}
    assert "f3a" in primary_ids
    assert result.stats.fre_extra_dropped == 0


def test_stats_parity_keys():
    result = reconcile(_fixture())
    s = result.stats
    # eng_rows counts the partition before itemid dedup:
    # e1,e2,e3,e4,e4dup,e5,e5,e7 = 8
    assert s.eng_rows == 8
    assert s.fre_rows == 5  # f1,f2,f3a,f3b,f6
    assert s.eng_matched_fre == 3  # A, B, C
    # A (f1 rep) and C (f3b rep) backfill; B's ENG already had a rep.
    assert s.eng_representedby_filled_from_fre == 2
    assert "matched_by_appno_docname" in s.clusters or "ambiguous" in s.clusters


def test_invariant_raises_on_synthetic_violation(monkeypatch):
    # Two ENG rows with the same ECLI but which slip past dedup (distinct itemid)
    # are handled as same-language duplicates, so to force a violation we bypass
    # via the internal checker directly.
    from hudoc_py.bilingual import reconcile as recon_mod  # noqa: F401
    from hudoc_py.bilingual.reconcile import _check_invariant

    dup = [_c("a", "ENG", "ECLI:X"), _c("b", "ENG", "ECLI:X")]
    with pytest.raises(ReconcileInvariantError):
        _check_invariant(dup)
