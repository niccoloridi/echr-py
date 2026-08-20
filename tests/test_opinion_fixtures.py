"""Synthetic English/French opinion-boundary regressions, fully offline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hudoc_py.text import judge_country, segment_full

FIXTURE_ROOT = Path(__file__).parent / "data" / "opinions"
EXPECTATIONS = json.loads((FIXTURE_ROOT / "expectations.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("expected", EXPECTATIONS, ids=lambda row: row["itemid"])
def test_synthetic_opinion_boundaries(expected):
    text = (FIXTURE_ROOT / expected["file"]).read_text(encoding="utf-8").rstrip("\n")
    sections = segment_full(
        text,
        doctype=expected["doctype"],
        doctype_branch=expected["doctype_branch"],
    )

    assert len(text) == expected["text_chars"]
    assert len(sections.separate_opinion or "") == expected["separate_opinion_chars"]
    assert sections.opinions_confidence == 1.0
    assert sections.opinion_diagnostics == []
    assert len(sections.opinions) == len(expected["opinions"])

    for opinion, pinned in zip(sections.opinions, expected["opinions"], strict=True):
        assert opinion.opinion_type == pinned["opinion_type"]
        assert opinion.authors == pinned["authors"]
        assert opinion.joined_by == pinned["joined_by"]
        assert opinion.joint is pinned["joint"]
        assert len(opinion.text) == pinned["text_chars"]
        assert len(opinion.body) == pinned["body_chars"]
        assert all(judge_country(judge) for judge in opinion.judges)
