"""Tests for individual-opinion splitting (EN + FR headings)."""

from __future__ import annotations

import pytest

from hudoc_py.text import segment_full, split_opinions, split_opinions_report

EN_BLOCK = """JOINT PARTLY DISSENTING OPINION OF JUDGES COSTA, BRATZA AND VAJIĆ

1. We regret that we cannot share the view of the majority.
2. In our opinion the interference was not necessary.

CONCURRING OPINION OF JUDGE PINTO DE ALBUQUERQUE

1. I agree with the outcome, but for different reasons.

PARTLY DISSENTING OPINION OF JUDGE
LÓPEZ GUERRA JOINED BY JUDGES SAJÓ AND KŪRIS

1. Like the majority, I accept the applicability of Article 8.

DECLARATION OF JUDGE TÜRMEN

I voted with the majority but wish to record a reservation.

DISSENTING OPINION OF MR SHEVCHUK, AD HOC JUDGE

1. I am unable to agree.
"""

FR_BLOCK = """OPINION DISSIDENTE COMMUNE AUX JUGES TULKENS, SPIELMANN ET LAFFRANQUE

1. Nous ne pouvons souscrire à la conclusion de la majorité.

OPINION EN PARTIE DISSIDENTE DU JUGE COSTA

1. Je regrette de ne pouvoir suivre la majorité sur ce point.

OPINION CONCORDANTE DE MME LA JUGE NUSSBERGER

1. Je partage la conclusion de la Cour.

OPINION SÉPARÉE DU JUGE SERGHIDES

1. La présente affaire soulève une question importante.
"""


def test_en_joint_partly_dissenting_multi_judge():
    ops = split_opinions(EN_BLOCK)
    assert len(ops) == 5
    first = ops[0]
    assert first.opinion_type == "partly_dissenting"
    assert first.joint is True
    assert first.judges == ["COSTA", "SIR NICOLAS BRATZA", "VAJIĆ"]
    assert first.language == "EN"
    assert "cannot share the view" in first.text
    assert "PINTO DE ALBUQUERQUE" not in first.text  # body ends at next heading


def test_en_single_concurring_multiword_surname():
    ops = split_opinions(EN_BLOCK)
    concurring = ops[1]
    assert concurring.opinion_type == "concurring"
    assert concurring.joint is False
    assert concurring.judges == ["PINTO DE ALBUQUERQUE"]


def test_en_multiline_header_with_joined_by():
    ops = split_opinions(EN_BLOCK)
    lopez = ops[2]
    assert lopez.opinion_type == "partly_dissenting"
    assert lopez.judges == ["LÓPEZ GUERRA", "SAJÓ", "KŪRIS"]
    assert lopez.authors == ["LÓPEZ GUERRA"]
    assert lopez.joined_by == ["SAJÓ", "KŪRIS"]
    assert lopez.joint_heading is False
    assert lopez.joint is True  # multiple judges even without the word JOINT
    assert "applicability of Article 8" in lopez.text


def test_en_declaration_and_ad_hoc():
    ops = split_opinions(EN_BLOCK)
    decl = ops[3]
    assert decl.opinion_type == "declaration"
    assert decl.judges == ["TÜRMEN"]
    adhoc = ops[4]
    assert adhoc.opinion_type == "dissenting"
    assert adhoc.judges == ["SHEVCHUK"]  # "AD HOC JUDGE" trailer stripped


def test_fr_headings():
    ops = split_opinions(FR_BLOCK)
    assert [o.opinion_type for o in ops] == [
        "dissenting", "partly_dissenting", "concurring", "separate",
    ]
    assert all(o.language == "FR" for o in ops)
    joint = ops[0]
    assert joint.joint is True
    assert joint.judges == ["TULKENS", "SPIELMANN", "LAFFRANQUE"]
    assert ops[1].judges == ["COSTA"]
    assert ops[2].judges == ["NUSSBERGER"]
    assert ops[3].judges == ["SERGHIDES"]


def test_fr_real_gc_headings():
    """Header forms verified against the real Öcalan GC judgment (001-69023)."""
    block = (
        "OPINION PARTIELLEMENT CONCORDANTE ET PARTIELLEMENT DISSIDENTE "
        "DE M. LE JUGE GARLICKI\n\n(Traduction)\n\nI. Article 5.\n\n"
        "OPINION PARTIELLEMENT DISSIDENTE COMMUNE À MM LES JUGES WILDHABER, COSTA, "
        "CAFLISCH, TÜRMEN, GARLICKI ET BORREGO BORREGO\n\n1. La majorité de la Cour...\n\n"
        "OPINION PARTIELLEMENT DISSIDENTE COMMUNE À MM. LES JUGES COSTA, CAFLISCH, "
        "TÜRMEN ET BORREGO BORREGO\n\n(Traduction)\n\n1. Nous ne partageons pas...\n"
    )
    ops = split_opinions(block)
    assert len(ops) == 3
    assert ops[0].opinion_type == "partly_concurring_partly_dissenting"
    assert ops[0].judges == ["GARLICKI"]
    assert ops[1].opinion_type == "partly_dissenting"
    assert ops[1].joint is True
    assert ops[1].judges == [
        "WILDHABER", "COSTA", "CAFLISCH", "TÜRMEN", "GARLICKI", "BORREGO BORREGO",
    ]
    assert ops[2].judges == ["COSTA", "CAFLISCH", "TÜRMEN", "BORREGO BORREGO"]


def test_prose_reference_not_matched_as_heading():
    """The annex-listing sentence (sentence case) must not become an opinion."""
    prose = (
        "In accordance with Article 45 § 2 of the Convention, the joint "
        "dissenting opinion of Judges Villiger, Mahoney and Silvis is annexed "
        "to this judgment.\n"
    )
    assert split_opinions(prose) == []


def test_en_compound_type():
    block = "PARTLY CONCURRING AND PARTLY DISSENTING OPINION OF JUDGE WOJTYCZEK\n\nText."
    ops = split_opinions(block)
    assert len(ops) == 1
    assert ops[0].opinion_type == "partly_concurring_partly_dissenting"


def test_en_separate_opinion_heading():
    block = "SEPARATE OPINION OF JUDGE DE MEYER\n\nText here."
    ops = split_opinions(block)
    assert ops[0].opinion_type == "separate"
    assert ops[0].judges == ["DE MEYER"]


def test_name_list_continuation_across_lines():
    block = (
        "JOINT CONCURRING OPINION OF JUDGES COSTA,\n"
        "BRATZA, LORENZEN AND VAJIĆ\n\nWe agree.\n"
    )
    ops = split_opinions(block)
    assert ops[0].judges == ["COSTA", "SIR NICOLAS BRATZA", "LORENZEN", "VAJIĆ"]


def test_empty_and_no_headings():
    assert split_opinions(None) == []
    assert split_opinions("") == []
    assert split_opinions("No opinions were annexed to this judgment.") == []
    assert split_opinions_report(None).confidence == 1.0
    assert split_opinions_report("   ").confidence == 1.0
    assert split_opinions_report(None).diagnostics == []


def test_segment_full_populates_opinions():
    judgment = (
        "PROCEDURE\n\n1. The case originated in an application.\n\n"
        "THE FACTS\n\n2. The applicant was born in 1970.\n\n"
        "THE LAW\n\n3. The applicant complained under Article 3.\n\n"
        "FOR THESE REASONS, THE COURT\n\n1. Holds that there has been a violation;\n\n"
        + EN_BLOCK
    )
    sections = segment_full(judgment)
    assert sections.separate_opinion is not None
    assert len(sections.opinions) == 5
    assert sections.opinions[0].judges == ["COSTA", "SIR NICOLAS BRATZA", "VAJIĆ"]
    assert sections.opinions_confidence > 0


def test_toc_index_block_not_emitted_or_used_as_body_boundary():
    block = """JOINT DISSENTING OPINION OF JUDGES SPIELMANN AND KELLER..........82
CONCURRING OPINION OF JUDGE MOTOC . . . . . 86
JOINT DISSENTING OPINION OF JUDGES SPIELMANN AND KELLER
CONCURRING OPINION OF JUDGE MOTOC
JOINT DISSENTING OPINION OF JUDGES SPIELMANN AND KELLER

1. We disagree in the first real opinion and explain our reasons in full.

CONCURRING OPINION OF JUDGE MOTOC

1. I concur in the second real opinion and explain my reasons in full.
"""
    report = split_opinions_report(block)
    assert len(report.opinions) == 2
    assert "1. We disagree" in report.opinions[0].text
    assert "..........82" not in report.opinions[0].text
    assert sum(item.startswith("dropped_toc:") for item in report.diagnostics) == 2
    assert sum(item.startswith("dropped_index_block:") for item in report.diagnostics) == 2


def test_real_pdf_wrapped_toc_entry_is_not_laundered_into_an_opinion():
    # Layout excerpted from the official KlimaSeniorinnen PDF (001-233206):
    # the page locator is on the names continuation, not the type line.
    block = """PARTLY CONCURRING PARTLY DISSENTING OPINION OF
JUDGE EICKE..........................................................................................233

PARTLY CONCURRING PARTLY DISSENTING OPINION
               OF JUDGE EICKE

1. I agree with parts of the judgment but respectfully disagree with other parts.
"""
    report = split_opinions_report(block)
    assert len(report.opinions) == 1
    assert report.opinions[0].authors == ["EICKE"]
    assert report.opinions[0].body.startswith("1. I agree")
    assert report.diagnostics[0] == "dropped_toc:1"
    assert any(item.startswith("low_coverage:") for item in report.diagnostics)


def test_toc_entries_without_page_numbers_are_rejected_as_index_block():
    block = """DISSENTING OPINION OF JUDGE KELLER
CONCURRING OPINION OF JUDGE MOTOC
DISSENTING OPINION OF JUDGE KELLER

The real dissent begins here and contains enough text to form an opinion body.

CONCURRING OPINION OF JUDGE MOTOC

The real concurrence begins here and contains enough text to form an opinion body.
"""
    report = split_opinions_report(block)
    assert len(report.opinions) == 2
    assert "real dissent begins" in report.opinions[0].text
    assert len([d for d in report.diagnostics if d.startswith("dropped_index_block:")]) == 2


def test_stripped_toc_block_ending_at_eof_emits_no_phantom_opinion():
    report = split_opinions_report(
        "DISSENTING OPINION OF JUDGE KELLER\n"
        "CONCURRING OPINION OF JUDGE MOTOC\n"
    )
    assert report.opinions == []
    assert report.confidence == 0.0
    assert report.diagnostics == [
        "dropped_index_block:1",
        "dropped_index_block:2",
        "no_headings_in_block",
    ]


def test_bodyless_final_declaration_after_real_opinion_is_preserved():
    report = split_opinions_report(
        "DISSENTING OPINION OF JUDGE KELLER\n\n"
        "The dissent contains a sufficiently developed explanatory body.\n\n"
        "declaration OF judge botoucharova\n"
    )
    assert [op.opinion_type for op in report.opinions] == ["dissenting", "declaration"]
    assert report.opinions[1].authors == ["BOTOUCHAROVA"]
    assert report.opinions[1].body == ""
    assert report.confidence == 1.0
    assert report.diagnostics == ["bodyless_declaration:5"]


def test_repeated_running_header_merges_without_losing_either_body_part():
    block = """DISSENTING OPINION OF JUDGE KELLER

Part one of the same opinion contains the opening legal analysis.

DISSENTING OPINION OF JUDGE KELLER

Part two of the same opinion contains the concluding legal analysis.
"""
    report = split_opinions_report(block)
    assert len(report.opinions) == 1
    assert "Part one" in report.opinions[0].text
    assert "Part two" in report.opinions[0].text
    assert "DISSENTING OPINION" not in report.opinions[0].body
    assert "Part one" in report.opinions[0].body
    assert "Part two" in report.opinions[0].body
    assert report.diagnostics == ["dropped_duplicate:dissenting:KELLER"]


def test_same_type_opinions_by_different_judges_are_not_deduplicated():
    block = """DISSENTING OPINION OF JUDGE KELLER

Keller gives a distinct and sufficiently developed dissenting opinion here.

DISSENTING OPINION OF JUDGE MOTOC

Motoc gives another distinct and sufficiently developed dissenting opinion here.
"""
    assert [op.judges for op in split_opinions(block)] == [["KELLER"], ["MOTOC"]]


@pytest.mark.parametrize(
    "joiner",
    [
        "APPROUVÉE PAR M. LE JUGE",
        "À LAQUELLE SE RALLIE M. LE JUGE",
        "À LAQUELLE S’EST RALLIÉ M. LE JUGE",
        "RALLIÉE PAR M. LE JUGE",
        "SE RALLIE À M. LE JUGE",
    ],
)
def test_french_joiner_clauses_preserve_authorship_roles(joiner):
    block = (
        f"OPINION SÉPARÉE DE M. LE JUGE MARTENS, {joiner} RUSSO\n\n"
        "Le corps de cette opinion contient une analyse suffisamment développée."
    )
    opinion = split_opinions(block)[0]
    assert opinion.authors == ["MARTENS"]
    assert opinion.joined_by == ["RUSSO"]
    assert opinion.judges == ["MARTENS", "RUSSO"]


def test_parenthetical_joiner_and_translation_are_not_part_of_names():
    joined = split_opinions(
        "CONCURRING OPINION OF JUDGE KELLER (joined by Judge Motoc)\n\n"
        "The opinion contains enough explanatory text to constitute its body."
    )[0]
    translated = split_opinions(
        "DISSENTING OPINION OF JUDGE KELLER (TRANSLATION)\n\n"
        "The opinion contains enough explanatory text to constitute its body."
    )[0]
    assert joined.authors == ["KELLER"] and joined.joined_by == ["MOTOC"]
    assert translated.authors == ["KELLER"] and translated.joined_by == []


@pytest.mark.parametrize(
    ("header", "expected_type", "expected_judge"),
    [
        ("1. DISSENTING OPINION OF JUDGE KELLER", "dissenting", "KELLER"),
        ("IV) CONCURRING OPINION OF JUDGE MOTOC", "concurring", "MOTOC"),
    ],
)
def test_numbered_and_roman_prefixed_headings(header, expected_type, expected_judge):
    opinion = split_opinions(
        f"{header}\n\nThe opinion contains a sufficiently developed explanatory body."
    )[0]
    assert opinion.opinion_type == expected_type
    assert opinion.judges == [expected_judge]


def test_french_open_header_and_blank_line_before_name():
    opinion = split_opinions(
        "OPINION PARTIELLEMENT DISSIDENTE DU JUGE\n\nKELLER\n\n"
        "Le corps contient une analyse suffisamment développée pour être conservée."
    )[0]
    assert opinion.opinion_type == "partly_dissenting"
    assert opinion.judges == ["KELLER"]
    assert opinion.body.startswith("Le corps contient")
    assert "KELLER" not in opinion.body


def test_blank_line_after_open_header_does_not_swallow_prose_as_a_name():
    block = (
        "DISSENTING OPINION OF JUDGE\n\n"
        "We regret that we cannot share the view of the majority."
    )
    assert split_opinions(block) == []


def test_titlecase_heading_is_accepted_but_titlecase_prose_reference_is_not():
    heading = (
        "Dissenting Opinion of Judge Keller\n\n"
        "The opinion contains a sufficiently developed explanatory body."
    )
    prose = "Dissenting opinion of Judges Keller and Motoc is annexed to this judgment."
    assert split_opinions(heading)[0].judges == ["KELLER"]
    assert split_opinions(prose) == []

    multi = (
        "Joint Dissenting Opinion of Judges Keller and Motoc\n\n"
        "The opinion contains a sufficiently developed explanatory body."
    )
    assert split_opinions(multi)[0].judges == ["KELLER", "MOTOC"]


def test_bare_concurring_and_dissenting_type_has_explicit_semantics():
    opinion = split_opinions(
        "CONCURRING AND DISSENTING OPINION OF JUDGE KELLER\n\n"
        "The opinion contains a sufficiently developed explanatory body."
    )[0]
    assert opinion.opinion_type == "partly_concurring_partly_dissenting"


def test_opinion_split_confidence_and_diagnostics():
    malformed = split_opinions_report("A malformed separate-opinion block with no heading.")
    unknown = split_opinions_report(
        "DISSENTING OPINION OF JUDGE PARSERGARBAGE\n\n"
        "The opinion contains a sufficiently developed explanatory body."
    )
    assert malformed.confidence == 0.0
    assert malformed.diagnostics == ["no_headings_in_block"]
    assert "unknown_judge:PARSERGARBAGE" in unknown.diagnostics
    assert unknown.confidence < 1.0


def test_lowercased_two_line_heading_is_accepted_when_names_are_rostered():
    """Legacy conversions lowercase the names tail of a split heading.

    Observed in HUDOC markdown renderings of pre-2000 judgments, where the
    opinion type sits on one line and ``of Judges <names>`` on the next, both
    lowercased. Casing alone cannot separate that from prose, so the roster
    anchors it.
    """
    text = (
        "SEPARATE OPINIONS\n\n"
        "concurring opinion of LORD reed\n\n"
        "I have voted with the majority.\n\n\n"
        "partly dissenting opinion\n"
        "of Judges rozakis and costa\n\n"
        "We are unable to agree.\n\n\n"
        "Joint partly dissenting opinion\n"
        "of Judges Pastor ridruejo, ress, makarczyk, tulkens and butkevych\n\n"
        "In our view the trial was unfair.\n"
    )
    opinions = split_opinions(text)
    assert [o.opinion_type for o in opinions] == [
        "concurring",
        "partly_dissenting",
        "partly_dissenting",
    ]
    assert opinions[1].authors == ["ROZAKIS", "COSTA"]
    assert opinions[2].authors == [
        "PASTOR RIDRUEJO",
        "RESS",
        "MAKARCZYK",
        "TULKENS",
        "BUTKEVYCH",
    ]


def test_lowercased_two_line_heading_still_rejects_unrostered_names():
    text = (
        "SEPARATE OPINIONS\n\n"
        "partly dissenting opinion\n"
        "of Judges nobody and nowhere\n\n"
        "Body text.\n"
    )
    assert split_opinions(text) == []


def test_lowercased_continuation_does_not_swallow_prose():
    text = (
        "SEPARATE OPINIONS\n\n"
        "partly dissenting opinion\n"
        "in our view the applicants trial was unfair\n\n"
        "Body text.\n"
    )
    assert split_opinions(text) == []
