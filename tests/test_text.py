"""Tests for HTML→text/MD conversion and section segmentation."""

from __future__ import annotations

from hudoc_py.text import html_to_md, html_to_text, segment_main_sections

SAMPLE_HTML = """
<html><body>
<h1>CASE OF FOO v. BAR</h1>
<p>1. The applicant, Mr Foo, was born in 1970.</p>
<p>2. He alleged a violation of Article 3.</p>
<h2>THE LAW</h2>
<p>3. The Court reiterates its case-law.</p>
<p>4. There has accordingly been a violation of Article 3.</p>
<h2>FOR THESE REASONS, THE COURT, UNANIMOUSLY,</h2>
<p>1. Declares the application admissible;</p>
<p>2. Holds that there has been a violation of Article 3.</p>
</body></html>
"""


def test_html_to_text_extracts_paragraphs_and_headings():
    text = html_to_text(SAMPLE_HTML)
    assert "CASE OF FOO v. BAR" in text
    assert "THE LAW" in text
    assert "FOR THESE REASONS" in text
    # Blocks separated by blank lines
    assert "\n\n" in text


def test_html_to_md_produces_markdown_headings():
    md = html_to_md(SAMPLE_HTML)
    assert "# CASE OF FOO v. BAR" in md
    assert "## THE LAW" in md or "THE LAW" in md  # html2text may render h2 as ## or just text


def test_html_to_md_preserves_hudoc_footnote_reference_and_body():
    html = """
    <p>162. The rule applies<a href="#_ftn1"><span>[1]</span></a>.</p>
    <hr />
    <div id="_ftn1">
      <p>[1] Position at the date of judgment.</p>
      <p>A second explanatory paragraph.</p>
    </div>
    """
    md = html_to_md(html)
    assert "applies[^1]." in md
    assert "[^1]: Position at the date of judgment." in md
    assert "    A second explanatory paragraph." in md
    assert md.count("[^1]:") == 1


def test_segment_main_sections_finds_law_and_dispositif():
    text = html_to_text(SAMPLE_HTML)
    sections = segment_main_sections(text)
    assert sections.full == text
    assert sections.the_law is not None
    assert "THE LAW" in sections.the_law
    assert "violation of Article 3" in sections.the_law
    assert sections.dispositif is not None
    assert sections.dispositif.startswith("FOR THESE REASONS")
    assert "Declares the application admissible" in sections.dispositif


def test_segment_returns_full_text_even_without_markers():
    text = "Bare text without canonical markers."
    sections = segment_main_sections(text)
    assert sections.full == text
    assert sections.the_law is None
    assert sections.dispositif is None


def test_segment_empty_returns_empty_sections():
    sections = segment_main_sections("")
    assert sections.full is None
    assert sections.the_law is None
    assert sections.dispositif is None


def test_segment_french_en_droit_marker():
    text = "Some intro.\n\nEN DROIT\n\nLa Cour rappelle.\n\nPAR CES MOTIFS\n\nDit qu'il y a violation."
    sections = segment_main_sections(text)
    assert sections.the_law is not None
    assert "EN DROIT" in sections.the_law
    assert sections.dispositif is not None
    assert sections.dispositif.startswith("PAR CES MOTIFS")
