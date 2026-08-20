"""Tests for the seven-section rich segmenter."""

from __future__ import annotations

from hudoc_py.text import segment_full

SAMPLE_JUDGMENT = """
CASE OF SAMPLE v. STATE

PROCEDURE

1. The case originated in application no. 12345/20.
2. The applicant was represented by Mr X.

THE FACTS

I. THE CIRCUMSTANCES OF THE CASE

3. The applicant was born in 1980 and lives in City.
4. In 2018 he was arrested.

COMPLAINTS

5. The applicant complained under Article 3 of treatment in detention.

THE LAW

I. ALLEGED VIOLATION OF ARTICLE 3

6. The Court reiterates its case-law.
7. There has accordingly been a violation of Article 3.

FOR THESE REASONS, THE COURT, UNANIMOUSLY,

1. Declares the application admissible;
2. Holds that there has been a violation of Article 3.

JOINT DISSENTING OPINION OF JUDGES A AND B

We respectfully disagree with the majority.
"""


def test_segments_full_judgment_into_six_sections():
    sections = segment_full(SAMPLE_JUDGMENT)
    found = set(sections.found)
    assert {"procedure", "facts", "complaints", "the_law", "operative", "separate_opinion"} <= found


def test_procedure_section():
    sections = segment_full(SAMPLE_JUDGMENT)
    assert sections.procedure is not None
    assert "application no. 12345/20" in sections.procedure
    # Doesn't bleed into facts
    assert "THE CIRCUMSTANCES OF THE CASE" not in sections.procedure


def test_facts_section():
    sections = segment_full(SAMPLE_JUDGMENT)
    assert sections.facts is not None
    assert "born in 1980" in sections.facts
    # Doesn't bleed into complaints
    assert "applicant complained" not in sections.facts


def test_complaints_section():
    sections = segment_full(SAMPLE_JUDGMENT)
    assert sections.complaints is not None
    assert "Article 3" in sections.complaints
    assert "violation" not in sections.complaints.lower() or "treatment" in sections.complaints


def test_the_law_section():
    sections = segment_full(SAMPLE_JUDGMENT)
    assert sections.the_law is not None
    assert "Court reiterates" in sections.the_law
    # Doesn't include the dispositif
    assert "FOR THESE REASONS" not in sections.the_law


def test_operative_and_dispositif_alias():
    sections = segment_full(SAMPLE_JUDGMENT)
    assert sections.operative is not None
    assert sections.dispositif == sections.operative
    assert "Declares the application admissible" in sections.operative


def test_separate_opinion():
    sections = segment_full(SAMPLE_JUDGMENT)
    assert sections.separate_opinion is not None
    assert "respectfully disagree" in sections.separate_opinion


def test_declaration_can_be_the_first_separate_opinion():
    text = """THE FACTS

Facts sufficiently described here.

THE LAW

The Court gives its legal reasoning here.

FOR THESE REASONS, THE COURT

Holds that there has been a violation.

DECLARATION OF JUDGE KELLER

I agree but add these sufficiently developed observations.

DISSENTING OPINION OF JUDGE MOTOC

I disagree and explain my position in a sufficiently developed opinion.
"""
    sections = segment_full(text)
    assert sections.separate_opinion is not None
    assert sections.separate_opinion.startswith("DECLARATION OF JUDGE KELLER")
    assert [op.opinion_type for op in sections.opinions] == ["declaration", "dissenting"]
    assert sections.opinions_confidence == 1.0


def test_uppercase_declaration_in_prose_is_not_a_separate_opinion_boundary():
    text = """THE FACTS

The applicant relied on the UNIVERSAL DECLARATION OF HUMAN RIGHTS in support of the claim.

THE LAW

The Court gives its legal reasoning here.

FOR THESE REASONS, THE COURT

Holds that there has been a violation.
"""
    sections = segment_full(text)
    assert sections.separate_opinion is None
    assert sections.opinions == []
    assert sections.opinions_confidence == 1.0
    assert sections.opinion_diagnostics == []


def test_explicit_judgment_metadata_overrides_press_release_phrase():
    text = """THE FACTS

The file contained a COMMUNIQUÉ DE PRESSE issued by the authority.

THE LAW

The Court gives its legal reasoning here.

FOR THESE REASONS, THE COURT

Holds that there has been a violation.

DISSENTING OPINION OF JUDGE KELLER

The dissent contains a sufficiently developed explanatory body.
"""
    sections = segment_full(text, doctype="HFJUD", doctype_branch="CHAMBER")
    assert sections.doctype_mode == "judgment"
    assert len(sections.opinions) == 1


def test_operative_header_tolerates_legacy_ocr_spacing():
    text = "THE LAW\n\nReasoning.\n\nFOR THESE REAS O NS, THE COURT\n\nHolds."
    sections = segment_full(text)
    assert sections.operative is not None
    assert sections.operative.startswith("FOR THESE REAS O NS")


def test_french_markers():
    fr = """
    PROCÉDURE

    1. La requête a son origine dans la requête no. 12345/20.

    EN FAIT

    2. Le requérant est né en 1980.

    GRIEFS

    3. Le requérant se plaint de l'article 3.

    EN DROIT

    4. La Cour rappelle sa jurisprudence.

    PAR CES MOTIFS, LA COUR, À L'UNANIMITÉ,

    Dit qu'il y a eu violation de l'article 3.

    OPINION DISSIDENTE DU JUGE Y

    Je ne partage pas l'opinion de la majorité.
    """
    sections = segment_full(fr)
    found = set(sections.found)
    assert {"procedure", "facts", "complaints", "the_law", "operative", "separate_opinion"} <= found
    assert sections.procedure is not None and "requête no. 12345/20" in sections.procedure
    assert sections.facts is not None and "1980" in sections.facts


def test_appendix_section():
    text = (
        "THE LAW\n\nReasoning.\n\nFOR THESE REASONS\n\nHolds.\n\nAPPENDIX\n\nList of applications."
    )
    sections = segment_full(text)
    assert sections.appendix is not None
    assert "List of applications" in sections.appendix


def test_empty_returns_empty_sections():
    sections = segment_full("")
    assert sections.full is None
    assert sections.found == []


def test_missing_markers_returns_full_only():
    text = "Some text with no canonical section markers at all."
    sections = segment_full(text)
    assert sections.full == text
    assert sections.found == []


# --- New variants and doctype routing -----------------------------------


def test_as_to_the_facts_variant():
    text = "AS TO THE FACTS\n\n1. The applicant was born in 1980.\n\nTHE LAW\n\nReasoning."
    sections = segment_full(text)
    assert sections.facts is not None and "born in 1980" in sections.facts
    assert sections.the_law is not None and "Reasoning" in sections.the_law


def test_relevant_legal_framework_as_law_marker():
    text = (
        "PROCEDURE\n\n1. Procedural history.\n\n"
        "RELEVANT LEGAL FRAMEWORK AND PRACTICE\n\n2. The Constitution provides...\n\n"
        "THE LAW\n\n3. Reasoning.\n\nFOR THESE REASONS\n\nHolds."
    )
    sections = segment_full(text)
    # the_law should be picked up first (RELEVANT LEGAL FRAMEWORK comes before THE LAW
    # in the text, both match the_law pattern – we get whichever is earlier).
    assert sections.the_law is not None
    assert "Constitution" in sections.the_law or "Reasoning" in sections.the_law


def test_committee_judgment_subject_matter_and_court_assessment():
    """Post-2018 simplified Committee judgments use these two sections in
    place of facts/the_law."""
    text = (
        "SUBJECT MATTER OF THE CASE\n\nThe case concerns Article 6 fairness.\n\n"
        "THE COURT'S ASSESSMENT\n\nThe complaint is admissible. Violation found.\n\n"
        "FOR THESE REASONS\n\nHolds unanimously."
    )
    sections = segment_full(text)
    assert sections.subject_matter is not None
    assert "Article 6 fairness" in sections.subject_matter
    assert sections.court_assessment is not None
    assert "Violation found" in sections.court_assessment
    assert sections.operative is not None
    # `found` should include subject_matter and court_assessment.
    assert "subject_matter" in sections.found
    assert "court_assessment" in sections.found


def test_info_note_doctype_is_soft_skipped():
    text = "Information Note on the Court's case-law\n\nKey case-law summary..."
    sections = segment_full(text, doctype="CLIN")
    assert sections.doctype_mode == "info_note"
    assert sections.found == []  # nothing parsed – info notes aren't judgments
    assert sections.confidence == 1.0  # we're confident about the routing


def test_press_release_doctype_is_soft_skipped():
    sections = segment_full("Press release issued by the Registrar.", doctype="PR")
    assert sections.doctype_mode == "press_release"
    assert sections.found == []


def test_content_sniff_routing_when_metadata_absent():
    """Even without doctype metadata, content sniffing routes correctly."""
    info_text = "Information Note on the Court's case-law - September 2024.\n\nSummary..."
    sections = segment_full(info_text)
    assert sections.doctype_mode == "info_note"


def test_communicated_case_routing_via_doctype_branch():
    text = "PROCEDURE\n\nCommunicated on 12 January 2024.\n\nQUESTIONS TO THE PARTIES\n\n1. ...?"
    sections = segment_full(text, doctype_branch="COMMUNICATEDCASES")
    assert sections.doctype_mode == "communicated_case"


def test_confidence_score_present_and_between_0_and_1():
    text = """
    PROCEDURE

    1. The application was lodged.

    THE FACTS

    2. Born in 1980.

    THE LAW

    3. Reasoning.

    FOR THESE REASONS

    Holds.
    """
    sections = segment_full(text)
    assert 0.0 <= sections.confidence <= 1.0
    # Four sections found in canonical order should score reasonably high.
    assert sections.confidence > 0.5


def test_normal_judgment_mode_when_no_routing_hints():
    text = "PROCEDURE\n\n1. ...\n\nTHE FACTS\n\n2. ...\n\nTHE LAW\n\n3. ...\n\nFOR THESE REASONS\n\nHolds."
    sections = segment_full(text)
    assert sections.doctype_mode == "judgment"


def test_les_circonstances_de_l_affaire_french_variant():
    text = (
        "EN FAIT\n\n"
        "LES CIRCONSTANCES DE L'AFFAIRE\n\n"
        "1. Le requérant est né en 1980.\n\n"
        "EN DROIT\n\n2. La Cour rappelle.\n\nPAR CES MOTIFS\n\nDit qu'il y a violation."
    )
    sections = segment_full(text)
    assert sections.facts is not None
    assert "1980" in sections.facts
    assert sections.the_law is not None
    assert sections.operative is not None
