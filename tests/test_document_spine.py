"""Source-aware HUDOC document spine and canonical section tests."""

from __future__ import annotations

from hudoc_py.models import DocumentSpine
from hudoc_py.text import build_spine_from_text, html_to_text, segment_full, segment_html

HTML = """
<style>
.body { font-size: 12pt; margin-bottom: 0pt }
.section { font-size: 14pt; margin-top: 18pt }
.bold { font-weight: bold }
</style>
<div>
  <p class="body"><span class="bold">CASE OF EXAMPLE v. STATE</span></p>
  <p class="section">PROCEDURE</p>
  <p class="body">1. The application was lodged.</p>
  <p class="body">2. The request asked whether the facts of the case disclosed a violation.</p>
  <p class="section">THE FACTS</p>
  <p class="body">3. The applicant was born in 1980.</p>
  <p class="body">General background</p>
  <p class="body">4. The relevant events followed.</p>
  <p class="section">THE LAW</p>
  <p class="body">5. The Court gives its reasons.</p>
  <p class="section">FOR THESE REASONS, THE COURT</p>
  <p class="body">1. Holds that there has been a violation.</p>
</div>
"""


def test_html_spine_preserves_source_blocks_and_styles():
    sections = segment_html(
        HTML,
        doctype="HEJUD",
        doctype_branch="CHAMBER",
        document_id="001-example",
    )

    assert sections.full == html_to_text(HTML)
    assert sections.spine is not None
    assert sections.spine.schema_version == "hudoc-spine.v1"
    assert sections.spine.document_id == "001-example"
    assert sections.spine.source_format == "hudoc_html"
    assert len(sections.spine.blocks) == 12
    assert sections.spine.blocks[1].source_tag == "p"
    assert sections.spine.blocks[1].source_style["font-size"] == "14pt"
    assert "source_large_font" in sections.spine.blocks[1].heading_source


def test_html_spine_preserves_inline_typography_without_promoting_block_style():
    spine = segment_html(
        """
        <style>.ital { font-style: italic } .bold { font-weight: 700 }</style>
        <p class="body">1. See <span class="ital">Soering</span>, and
        <strong><em>Öcalan</em></strong>.</p>
        """,
        doctype="HEJUD",
    ).spine
    assert spine is not None
    block = spine.blocks[0]
    soering = next(run for run in block.inline_runs if run.text == "Soering")
    ocalan = next(run for run in block.inline_runs if run.text == "Öcalan")
    assert block.text[soering.start : soering.end] == "Soering"
    assert block.text[ocalan.start : ocalan.end] == "Öcalan"
    assert soering.italic and not soering.bold
    assert ocalan.italic and ocalan.bold
    assert "font-style" not in block.source_style
    assert "font-weight" not in block.source_style


def test_inline_tags_do_not_invent_spaces_inside_citation_punctuation():
    spine = segment_html(
        """
        <p>1. See Ukraine v. Russia (<em>re Crimea</em>), and
        <em>Andrejeva</em>, cited above, § 77.</p>
        """,
        doctype="HEJUD",
    ).spine

    assert spine is not None
    block = spine.blocks[0]
    assert block.text == ("1. See Ukraine v. Russia (re Crimea), and Andrejeva, cited above, § 77.")
    styled = [run for run in block.inline_runs if run.italic]
    assert [run.text for run in styled] == ["re Crimea", "Andrejeva"]
    assert all(block.text[run.start : run.end] == run.text for run in styled)


def test_nested_word_lists_keep_canonical_heading_separate_from_subheadings():
    sections = segment_html(
        """
        <ul><li><span>THE FACTS</span><ol><li><span>CIRCUMSTANCES</span></li></ol></li></ul>
        <p>1. Facts here.</p>
        <ul><li><span>THE LAW</span><ol><li><span>ARTICLE 3</span></li></ol></li></ul>
        <p>2. Legal reasoning here.</p>
        <ul><li><span>FOR THESE REASONS, THE COURT</span></li></ul>
        <p>1. Holds that there has been a violation.</p>
        """,
        doctype="HEJUD",
        document_id="001-nested-lists",
    )

    assert sections.spine is not None
    headings = [block for block in sections.spine.blocks if block.type == "heading"]
    assert [block.text for block in headings] == [
        "THE FACTS",
        "THE LAW",
        "ARTICLE 3",
        "FOR THESE REASONS, THE COURT",
    ]
    law = next(block for block in sections.spine.blocks if "Legal reasoning" in block.text)
    assert law.section == "the_law"


def test_numbered_legal_paragraph_owns_adjacent_unnumbered_html_blocks():
    spine = segment_html(
        """
        <p>79. The legal paragraph starts in this physical block.</p>
        <p>Its reasoning continues in a separate physical block.</p>
        <p>It has a second continuation.</p>
        <h2>A NEW SUBSECTION</h2>
        <p>This block is not part of paragraph 79.</p>
        <p>80. The next legal paragraph starts here.</p>
        """,
        doctype="HEJUD",
    ).spine

    assert spine is not None
    numbered = next(block for block in spine.blocks if block.para_num == 79)
    continuations = [
        block for block in spine.blocks if block.text.startswith(("Its reasoning", "It has"))
    ]
    after_heading = next(block for block in spine.blocks if block.text.startswith("This block"))
    next_numbered = next(block for block in spine.blocks if block.para_num == 80)

    assert numbered.para_id == numbered.legal_para_id == "79"
    assert numbered.legal_para_num == 79
    assert [block.para_id for block in continuations] == ["u-001", "u-002"]
    assert all(block.legal_para_id == "79" for block in continuations)
    assert all(block.legal_para_num == 79 for block in continuations)
    assert after_heading.legal_para_id is None
    assert after_heading.legal_para_num is None
    assert next_numbered.legal_para_id == "80"
    assert next_numbered.legal_para_num == 80


def test_hudoc_footnote_is_typed_linked_and_does_not_collide_with_opinion_paras():
    sections = segment_html(
        """
        <p>THE LAW</p>
        <p>162. The majority relies on forty-one ratifications<a name="_ftnref1"></a>
        <a href="#_ftn1"><span>[1]</span></a>.</p>
        <p>FOR THESE REASONS, THE COURT</p>
        <p>1. Holds that there has been a violation.</p>
        <p>DISSENTING OPINION OF JUDGE EXAMPLE</p>
        <p>1. I respectfully disagree.</p>
        <hr />
        <div id="_ftn1"><p>1. At the date of the Chamber judgment.</p></div>
        """,
        document_id="001-footnote",
    )
    assert sections.spine is not None
    spine = sections.spine
    source = next(block for block in spine.blocks if "ratifications" in block.text)
    body = next(block for block in spine.blocks if block.type == "footnote")
    assert source.footnote_references[0].footnote_id == "ftn1"
    assert source.footnote_references[0].label == "1"
    assert (
        source.text[source.footnote_references[0].start : source.footnote_references[0].end]
        == "[1]"
    )
    assert body.footnote_id == "ftn1"
    assert body.para_num is None
    assert body.para_id == "fn-ftn1"
    assert body.referenced_by_block_ids == [source.block_id]
    assert body.section == "the_law"
    assert body.opinion_id is None
    assert spine.footnotes[0].text == "At the date of the Chamber judgment."
    assert spine.footnotes[0].body_block_ids == [body.block_id]
    assert spine.footnotes[0].reference_block_ids == [source.block_id]
    assert spine.footnotes[0].reference_para_ids == [source.para_id]


def test_footnote_reference_offset_follows_dom_anchor_not_identical_plain_text():
    sections = segment_html(
        """
        <p>THE LAW</p>
        <p>4. Text [1] then the actual note <a href="#_ftn1">[1]</a>.</p>
        <div id="_ftn1"><p>[1] The note.</p></div>
        """,
        document_id="001-repeated-marker",
    )
    assert sections.spine is not None
    source = next(block for block in sections.spine.blocks if "actual note" in block.text)
    reference = source.footnote_references[0]
    assert reference.start == source.text.rindex("[1]")
    assert source.text[reference.start : reference.end] == "[1]"


def test_footnote_shared_across_majority_and_opinion_has_neutral_context():
    sections = segment_html(
        """
        <p>THE LAW</p><p>2. Majority note<a href="#_ftn1">[1]</a>.</p>
        <p>FOR THESE REASONS, THE COURT</p><p>1. Dismisses.</p>
        <p>DISSENTING OPINION OF JUDGE EXAMPLE</p>
        <p>2. Opinion note<a href="#_ftn1">[1]</a>.</p>
        <div id="_ftn1"><p>[1] Shared source material.</p></div>
        """,
        document_id="001-shared-note",
    )
    assert sections.spine is not None
    body = next(block for block in sections.spine.blocks if block.type == "footnote")
    assert body.section is None
    assert body.opinion_id is None
    assert len(body.referenced_by_block_ids) == 2
    assert "footnote_multiple_source_contexts" in {
        diagnostic.code for diagnostic in sections.spine.diagnostics
    }


def test_multi_paragraph_footnote_is_one_logical_body_with_opinion_context():
    sections = segment_html(
        """
        <p>THE LAW</p><p>1. Majority reasons.</p>
        <p>FOR THESE REASONS, THE COURT</p><p>1. Dismisses the complaint.</p>
        <p>DISSENTING OPINION OF JUDGE EXAMPLE</p>
        <p>14. I disagree<a href="#_ftn2">[2]</a>.</p>
        <div id="_ftn2">
          <p>[2] First footnote paragraph.</p>
          <p>Second footnote paragraph with further reasons.</p>
        </div>
        """,
        document_id="001-opinion-footnote",
    )
    assert sections.spine is not None
    footnote = sections.spine.footnotes[0]
    bodies = [block for block in sections.spine.blocks if block.block_id in footnote.body_block_ids]
    invoking = next(block for block in sections.spine.blocks if "I disagree" in block.text)
    assert len(bodies) == 2
    assert [body.para_id for body in bodies] == ["fn-ftn2", "fn-ftn2-2"]
    assert footnote.text == (
        "First footnote paragraph.\n\nSecond footnote paragraph with further reasons."
    )
    assert footnote.reference_para_ids == [invoking.para_id]
    assert invoking.opinion_id is not None
    assert all(body.opinion_id == invoking.opinion_id for body in bodies)
    assert all(body.section == "separate_opinion" for body in bodies)
    assert all(
        not paragraph_id.startswith("fn-")
        for span in sections.spans
        for paragraph_id in span.paragraph_ids
    )
    assert sections.separate_opinion is not None
    assert "First footnote paragraph" not in sections.separate_opinion
    assert sections.opinions
    assert "First footnote paragraph" not in sections.opinions[0].text


def test_orphan_footnote_reference_and_body_are_diagnostics():
    missing_body = segment_html('<p>1. Text<a href="#_ftn3">[3]</a>.</p>', document_id="missing")
    orphan_body = segment_html(
        '<div id="_ftn4"><p>[4] Orphan text.</p></div>', document_id="orphan"
    )
    assert missing_body.spine is not None
    assert orphan_body.spine is not None
    assert "footnote_body_missing" in {
        diagnostic.code for diagnostic in missing_body.spine.diagnostics
    }
    assert "footnote_reference_missing" in {
        diagnostic.code for diagnostic in orphan_body.spine.diagnostics
    }


def test_body_phrase_cannot_become_html_section_boundary():
    sections = segment_html(HTML, doctype="HEJUD")

    assert sections.status == "complete"
    assert sections.facts is not None
    assert sections.facts.startswith("THE FACTS")
    assert "whether the facts of the case" not in sections.facts
    assert [span.heading for span in sections.spans] == [
        "PROCEDURE",
        "THE FACTS",
        "THE LAW",
        "FOR THESE REASONS, THE COURT",
    ]


def test_spine_has_strict_paragraph_ids_and_duplicate_diagnostic():
    sections = segment_html(HTML, doctype="HEJUD")
    assert sections.spine is not None
    numbered = [
        (block.para_num, block.para_id)
        for block in sections.spine.blocks
        if block.para_num is not None
    ]
    assert numbered == [(1, "1"), (2, "2"), (3, "3"), (4, "4"), (5, "5"), (1, "1-2")]
    assert "duplicate_paragraph_number" in {diagnostic.code for diagnostic in sections.diagnostics}


def test_short_title_before_numbered_paragraph_becomes_auditable_subheading():
    sections = segment_html(HTML, doctype="HEJUD")
    assert sections.spine is not None
    heading = next(block for block in sections.spine.blocks if block.text == "General background")
    assert heading.type == "heading"
    assert heading.heading_role == "subsection"
    assert heading.heading_source == ["followed_by_numbered_paragraph"]
    assert heading.section == "facts"


def test_toc_candidates_are_preserved_but_not_used_as_body_boundaries():
    text = """TABLE OF CONTENTS

PROCEDURE

THE FACTS

THE LAW

PROCEDURE

1. The application was lodged.

THE FACTS

2. The applicant was born in 1980.

THE LAW

3. The Court gives its reasons.

FOR THESE REASONS

Holds that there has been a violation.
"""
    sections = segment_full(text, doctype="HEJUD")

    assert sections.procedure is not None
    assert sections.procedure.startswith("PROCEDURE\n\n1.")
    assert "table_of_contents_candidates" in {
        diagnostic.code for diagnostic in sections.diagnostics
    }
    assert sections.spine is not None
    assert sum(block.heading_role == "toc_entry" for block in sections.spine.blocks) == 3


def test_unsegmented_text_fails_visibly_but_retains_spine():
    sections = segment_full("An unstructured document without canonical headings.")
    assert sections.status == "unsegmented"
    assert sections.confidence == 0.0
    assert [diagnostic.code for diagnostic in sections.diagnostics] == ["no_section_boundaries"]
    assert isinstance(sections.spine, DocumentSpine)


def test_plain_text_spine_preserves_crlf_source_offsets():
    text = "1. First paragraph.\r\n\r\n2. Alpha v. State, no. 12/34."
    spine = build_spine_from_text(text)
    assert [block.text for block in spine.blocks] == [
        "1. First paragraph.",
        "2. Alpha v. State, no. 12/34.",
    ]
    assert spine.blocks[1].char_start == text.index("2. Alpha")


def test_plain_text_spine_accepts_inception_cr_crlf_paragraph_separators():
    text = "1. First paragraph.\r\r\n2. Alpha v. State, no. 12/34."
    spine = build_spine_from_text(text)
    assert len(spine.blocks) == 2
    assert spine.blocks[1].char_start == text.index("2. Alpha")


def test_parenthesized_list_items_are_not_promoted_to_headings():
    spine = build_spine_from_text(
        "THE FACTS\n\n1. Introductory paragraph.\n\n"
        "(i) the first listed instrument;\n\n2. The next paragraph."
    )
    item = next(block for block in spine.blocks if block.text.startswith("(i)"))
    assert item.type == "list_item"
    assert item.heading_level is None


def test_historical_commission_sections_keep_physical_source_order():
    text = """EUROPEAN COMMISSION OF HUMAN RIGHTS

EN FAIT

Les faits de la cause peuvent se résumer comme suit.

GRIEFS

Le requérant se plaint de la durée de la procédure.

PROCEDURE

La requête a été introduite le 17 septembre 1986.

EN DROIT

La Commission examine la recevabilité de la requête.

Par ces motifs, la Commission, à la majorité,

DECLARE LA REQUETE IRRECEVABLE.
"""
    sections = segment_full(
        text,
        doctype="HFDEC",
        doctype_branch="DECCOMMISSION",
    )

    assert sections.found == ["procedure", "facts", "complaints", "the_law", "operative"]
    assert [span.section for span in sections.spans] == [
        "facts",
        "complaints",
        "procedure",
        "the_law",
        "operative",
    ]
    assert sections.facts == "EN FAIT\n\nLes faits de la cause peuvent se résumer comme suit."
    assert sections.complaints is not None
    assert sections.status == "complete"


def test_operative_phrase_in_reasoning_is_not_a_boundary():
    text = """THE FACTS

1. The applicant brought proceedings.

THE LAW

2. For these reasons, the Court considers that the complaint is admissible.

FOR THESE REASONS, THE COURT

Holds that there has been a violation.
"""
    sections = segment_full(text, doctype="HEJUD")

    assert sections.the_law is not None
    assert "Court considers that the complaint" in sections.the_law
    assert sections.operative is not None
    assert sections.operative.startswith("FOR THESE REASONS, THE COURT")


def test_french_joint_partly_dissenting_opinion_is_a_boundary():
    text = """EN FAIT

1. Les circonstances de l'espèce.

EN DROIT

2. La Cour examine le grief.

PAR CES MOTIFS, LA COUR

Dit qu'il y a eu violation.

OPINION COMMUNE EN PARTIE DISSIDENTE DE MM. LES JUGES EXEMPLE ET TEST

Nous ne partageons pas la conclusion de la majorité.
"""
    sections = segment_full(text, doctype="HFJUD")

    assert sections.separate_opinion is not None
    assert sections.separate_opinion.startswith("OPINION COMMUNE EN PARTIE DISSIDENTE")


def test_compound_partly_concurring_partly_dissenting_heading_is_a_boundary():
    text = """THE LAW

1. The Court examines the application.

FOR THESE REASONS, THE COURT

Holds that there has been a violation.

PARTLY CONCURRING, PARTLY DISSENTING OPINION OF JUDGE EXAMPLE

I agree with one conclusion and disagree with another.
"""
    sections = segment_full(text, doctype="HEJUD")

    assert sections.separate_opinion is not None
    assert sections.separate_opinion.startswith("PARTLY CONCURRING, PARTLY DISSENTING")


def test_verified_historical_singular_grief_heading_is_supported():
    text = """EN FAIT

Les faits de la cause.

GRIEF

Le requérant invoque l'article 6 de la Convention.

EN DROIT

La Commission examine le grief.

PAR CES MOTIFS, LA COMMISSION

Déclare la requête irrecevable.
"""
    sections = segment_full(
        text,
        doctype="HFDEC",
        doctype_branch="DECCOMMISSION",
    )

    assert sections.complaints is not None
    assert sections.complaints.startswith("GRIEF")


def test_verified_french_combined_procedure_and_facts_heading_is_supported():
    sections = segment_full(
        "PROCÉDURE ET FAITS\n\n1. L'affaire a été déférée à la Cour.\n\n"
        "EN DROIT\n\n2. La Cour examine la demande.\n\n"
        "PAR CES MOTIFS, LA COUR\n\nDit que l'État doit payer.",
        doctype="HFJUD",
    )

    assert sections.procedure is not None
    assert sections.procedure.startswith("PROCÉDURE ET FAITS")


def test_plain_text_appendix_joined_to_subtitle_is_preserved():
    text = """FACTS AND PROCEDURE

1. A list of applicants is set out in the appendix.

THE LAW

2. It is appropriate to strike the case out.

For these reasons, the Court, unanimously,

Decides to strike the application out.

Appendix
List of applicants

Example Applicant is a national of the respondent State.
"""
    sections = segment_full(
        text,
        doctype="HEDEC",
        doctype_branch="DECCOMMISSION",
    )

    assert sections.appendix is not None
    assert sections.appendix.startswith("Appendix\nList of applicants")


def test_communicated_case_annex_before_questions_keeps_source_order():
    text = """THE FACTS

The applicants are retired civil servants.

COMPLAINTS

The applicants complain under Article 14.

ANNEX

Application no. 12345/67

QUESTIONS TO THE PARTIES

Has there been a difference in treatment?
"""
    sections = segment_full(
        text,
        doctype="HECOM",
        doctype_branch="COMMUNICATEDCASES",
    )

    assert [span.section for span in sections.spans] == [
        "facts",
        "complaints",
        "appendix",
        "the_law",
    ]


def test_html_spine_assigns_stable_individual_opinion_identity():
    html = (
        "<h1>THE LAW</h1><p>1. Reasons.</p>"
        "<h1>FOR THESE REASONS, THE COURT</h1><p>Dismisses.</p>"
        "<h2>DISSENTING OPINION OF JUDGE SMITH</h2>"
        "<p>1. I disagree with the majority for the reasons set out below.</p>"
    )

    first = segment_html(html, doctype="HEJUD", document_id="001-x")
    second = segment_html(html, doctype="HEJUD", document_id="001-x")

    assert first.opinions[0].opinion_id == second.opinions[0].opinion_id
    assert first.opinions[0].ordinal == 1
    opinion_blocks = [block for block in first.spine.blocks if block.opinion_id]
    assert len(opinion_blocks) == 2
    assert {tuple(block.opinion_authors) for block in opinion_blocks} == {("SMITH",)}
