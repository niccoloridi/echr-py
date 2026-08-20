"""Tests for ECHR keypoint (kpthesaurus) label ↔ ID resolution."""

from __future__ import annotations

import pytest

from hudoc_py.main.queries import build_search_query
from hudoc_py.thesaurus import (
    keypoint_label,
    keypoint_parent,
    keypoints,
    resolve_keypoint,
    search_keypoints,
)


def test_map_loaded_and_known_ids():
    kp = keypoints()
    assert len(kp) >= 500  # the canonical English taxonomy (~587)
    assert kp["350"] == "(Art. 3) Prohibition of torture"
    assert kp["445"] == "(Art. 6) Right to a fair trial"
    assert kp["449"] == "(Art. 2) Right to life"


def test_keypoint_label_and_parent():
    assert keypoint_label("451") == "(Art. 8) Right to respect for private and family life"
    assert keypoint_label(451) == keypoint_label("451")
    assert keypoint_label("999999") is None
    # 425 "(Art. 8-1) Respect for family life" is a child of 451.
    assert keypoint_parent("425") == "451"


def test_resolve_numeric_passthrough():
    assert resolve_keypoint("350") == ["350"]
    assert resolve_keypoint(350) == ["350"]
    # Unknown numeric IDs pass through unchanged (may be a legacy/older ID).
    assert resolve_keypoint("999999") == ["999999"]


def test_resolve_exact_label():
    assert resolve_keypoint("(Art. 3) Prohibition of torture") == ["350"]
    # Case-insensitive exact match.
    assert resolve_keypoint("(art. 3) prohibition of torture") == ["350"]


def test_resolve_keyword_substring():
    ids = resolve_keypoint("torture")
    assert "350" in ids  # (Art. 3) Prohibition of torture
    assert len(ids) >= 1


def test_resolve_unknown_raises():
    with pytest.raises(KeyError, match="No ECHR keypoint"):
        resolve_keypoint("definitely not a real keyword xyzzy")


def test_search_keypoints():
    results = dict(search_keypoints("positive obligations"))
    # Article-specific positive-obligation keywords exist.
    assert any("Positive obligations" in label for label in results.values())


def test_query_builder_accepts_keyword_text():
    q = build_search_query(kpthesaurus="torture")
    # Resolves to at least the Art. 3 torture keypoint ID.
    assert 'kpthesaurus:"350"' in q


def test_query_builder_accepts_numeric_id():
    q = build_search_query(kpthesaurus="350")
    assert 'kpthesaurus:"350"' in q


def test_query_builder_multiple_keywords_or_grouped():
    q = build_search_query(kpthesaurus=["350", "445"])
    assert 'kpthesaurus:"350"' in q
    assert 'kpthesaurus:"445"' in q
    assert " OR " in q
