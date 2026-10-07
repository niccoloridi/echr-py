"""A <br> inside a HUDOC paragraph is whitespace, not a glue point."""

from __future__ import annotations

from hudoc_py.text.conversion import html_to_text
from hudoc_py.text.opinions import split_opinions_report


def test_br_inside_paragraph_becomes_a_space():
    html = (
        "<p><span>JOINT PARTLY DISSENTING OPINION</span><br /><span>OF JUDGES COSTA, CAFLISCH, "
        "TÜRMEN</span><br /><span>AND BORREGO BORREGO</span></p>"
    )
    assert html_to_text(html) == (
        "JOINT PARTLY DISSENTING OPINION OF JUDGES COSTA, CAFLISCH, TÜRMEN AND BORREGO BORREGO"
    )


def test_split_opinions_recovers_headings_broken_by_br():
    body = "<p><span>1. Reasoning.</span></p>" * 3
    html = (
        "<p>GRAND CHAMBER</p><p>CASE OF X v. Y</p>" + body
        + "<p><span>PARTLY CONCURRING, PARTLY DISSENTING OPINION OF JUDGE GARLICKI</span></p>"
        + "<p>I. First point.</p>"
        + "<p><span>JOINT PARTLY DISSENTING OPINION</span><br /><span>OF JUDGES WILDHABER, COSTA, "
        "CAFLISCH, TÜRMEN, GARLICKI AND BORREGO BORREGO</span></p>"
        + "<p>1. We disagree.</p>"
        + "<p><span>JOINT PARTLY DISSENTING OPINION</span><br /><span>OF JUDGES COSTA, CAFLISCH, "
        "TÜRMEN</span><br /><span>AND BORREGO BORREGO</span></p>"
        + "<p>In paragraph 175 of the judgment.</p>"
    )
    report = split_opinions_report(html_to_text(html))
    authors = [tuple(o.authors) for o in report.opinions]
    assert authors == [
        ("GARLICKI",),
        ("WILDHABER", "COSTA", "CAFLISCH", "TÜRMEN", "GARLICKI", "BORREGO BORREGO"),
        ("COSTA", "CAFLISCH", "TÜRMEN", "BORREGO BORREGO"),
    ]
