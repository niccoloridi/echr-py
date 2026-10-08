"""Opt-in DOCX structure: Registry styles become roles, sections, opinions, footnotes and an agreement report."""

from __future__ import annotations

import io
import zipfile

import pytest

from hudoc_py.models import Case
from hudoc_py.models.common import Opinion, Sections
from hudoc_py.text.docx_structure import (
    build_docx_structure,
    compare_docx_structure,
    normalise_style_id,
    role_for_style,
)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _p(style: str | None, *runs: str, footnote: str | None = None) -> str:
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    body = "".join(
        f'<w:r><w:t xml:space="preserve">{r}</w:t></w:r>' if r != "<br/>" else "<w:r><w:br/></w:r>"
        for r in runs
    )
    ref = f'<w:r><w:footnoteReference w:id="{footnote}"/></w:r>' if footnote else ""
    return f"<w:p>{ppr}{body}{ref}</w:p>"


def make_docx(paragraphs: list[str], footnotes: dict[str, str] | None = None) -> bytes:
    document = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="{W}"><w:body>'
        + "".join(paragraphs)
        + "<w:tbl><w:tr><w:tc>"
        + _p("JuPara", "Table cell text")
        + "</w:tc></w:tr></w:tbl>"
        + "</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        archive.writestr("word/document.xml", document)
        if footnotes is not None:
            notes = "".join(
                f'<w:footnote w:id="{fid}">{_p("FootnoteText", text)}</w:footnote>'
                for fid, text in footnotes.items()
            )
            archive.writestr(
                "word/footnotes.xml",
                f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:footnotes xmlns:w="{W}">'
                f'<w:footnote w:type="separator" w:id="-1">{_p(None, "")}</w:footnote>{notes}</w:footnotes>',
            )
    return buffer.getvalue()


JUDGMENT = [
    _p("JuCase", "CASE OF X v. Y"),
    _p("JuJudges", "Mr A. Judge, President,"),
    _p("JuJudges", "Mrs B. Judge,"),
    _p("JuHHead", "PROCEDURE"),
    _p("JuPara", "1.  The case originated in an application."),
    _p("JuHHead", "THE FACTS"),
    _p("JuParaCharCharCharChar", "2.  The applicant was born in 1950.", footnote="1"),
    _p("JuQuot", "“Everyone has the right to liberty.”"),
    _p("JuHHead", "THE LAW"),
    _p("JuH1", "I.  ALLEGED VIOLATION OF ARTICLE 3"),
    _p("JuPara", "3.  The Court reiterates its case-law."),
    _p("JuHHead", "FOR THESE REASONS, THE COURT"),
    _p("JuList", "1.  Holds, unanimously, that there has been a violation."),
    _p("OpiHHead", "PARTLY CONCURRING, PARTLY DISSENTING OPINION OF JUDGE GARLICKI"),
    _p("OpiPara", "I voted with the majority on most points."),
    _p(
        "OpiHHead",
        "JOINT PARTLY DISSENTING OPINION",
        "<br/>",
        "OF JUDGES WILDHABER, COSTA, CAFLISCH, TÜRMEN, GARLICKI AND BORREGO BORREGO",
    ),
    _p("OpiPara", "1.  We disagree with paragraph 175."),
    _p(
        "OpiHHead",
        "JOINT PARTLY DISSENTING OPINION",
        "<br/>",
        "OF JUDGES COSTA, CAFLISCH, TÜRMEN",
        "<br/>",
        "AND BORREGO BORREGO",
    ),
    _p(
        "OpiPara",
        "In paragraph 175 of the judgment, the majority expresses the opinion that the applicant was not entitled.",
    ),
]


def test_style_normalisation_absorbs_template_drift():
    assert normalise_style_id("JuParaCharCharCharChar") == "jupara"
    assert normalise_style_id("JuHIroman0") == "juhiroman"
    assert normalise_style_id("StyleJuSignedRight1") == "jusignedright"
    assert normalise_style_id("ECHRHeading3") == "echrheading"
    assert role_for_style("ECHRParaQuote") == "quotation"
    assert role_for_style("OpiHHead") == "opinion_heading"
    assert role_for_style("SuPara") == "summary"
    assert role_for_style("ECHRTitle1") == "section_heading"
    assert role_for_style("Jup") == "other"


def test_structure_roles_sections_opinions_and_footnotes():
    structure = build_docx_structure(
        make_docx(JUDGMENT, {"1": "See the Registry note."}), itemid="001-test"
    )
    assert (
        structure.itemid == "001-test" and structure.byte_count > 0 and len(structure.sha256) == 64
    )
    roles = [b.role for b in structure.blocks]
    assert roles[:4] == ["case_title", "judges", "judges", "section_heading"]
    assert structure.blocks[6].role == "paragraph" and structure.blocks[6].para_num == 2
    assert structure.blocks[7].role == "quotation"
    assert structure.blocks[-1].in_table is True
    assert [s.name for s in structure.sections] == [
        "procedure",
        "facts",
        "the_law",
        "operative",
        "separate_opinion",
    ]
    assert [s.start_index for s in structure.sections] == [3, 5, 8, 11, 13]
    assert structure.sections[-2].end_index == 13
    assert len(structure.opinions) == 3, structure.diagnostics
    assert structure.opinions[1].heading_text.startswith(
        "JOINT PARTLY DISSENTING OPINION OF JUDGES WILDHABER"
    )
    assert structure.opinions[1].authors == [
        "WILDHABER",
        "COSTA",
        "CAFLISCH",
        "TÜRMEN",
        "GARLICKI",
        "BORREGO BORREGO",
    ]
    assert structure.opinions[2].authors == ["COSTA", "CAFLISCH", "TÜRMEN", "BORREGO BORREGO"]
    assert structure.opinions[0].opinion_type == "partly_concurring_partly_dissenting"
    assert structure.opinions[1].end_index == 17 and structure.opinions[2].end_index == len(
        structure.blocks
    )
    assert structure.footnotes[0].footnote_id == "1" and structure.footnotes[
        0
    ].invoking_block_indexes == [6]
    assert structure.bench_lines == ["Mr A. Judge, President,", "Mrs B. Judge,"]
    assert structure.style_counts["JuHHead"] == 4 and structure.unmapped_styles == {}
    assert structure.paragraph_count == 4  # three body paragraphs plus the table cell


def test_unreadable_payload_is_diagnosed_not_raised():
    structure = build_docx_structure(b"not a zip", itemid="001-x")
    assert (
        structure.blocks == []
        and structure.diagnostics
        and structure.diagnostics[0].startswith("unreadable_docx")
    )


def test_agreement_report_against_html_segmentation():
    structure = build_docx_structure(make_docx(JUDGMENT))
    sections = Sections(
        procedure="…",
        facts="…",
        the_law="…",
        operative="…",
        separate_opinion="…",
        opinions=[
            Opinion(opinion_type="partly_concurring_partly_dissenting", authors=["GARLICKI"])
        ],
    )
    case = Case(itemid="001-test", sections=sections, docx_structure=structure)
    report = compare_docx_structure(case)
    assert report.sections_agree is True
    # a sub-section seen on one side only is reported but does not flip the flag
    case_sub = Case(
        itemid="001-test",
        sections=sections.model_copy(update={"court_assessment": "…"}),
        docx_structure=structure,
    )
    report_sub = compare_docx_structure(case_sub)
    assert (
        report_sub.sections_agree is True
        and "subsection:court_assessment:html_only" in report_sub.differences
    )
    assert report.html_opinion_count == 1 and report.docx_opinion_count == 3
    assert report.opinion_counts_agree is False
    assert any(d.startswith("opinion_count:html=1:docx=3") for d in report.differences)
    assert report.docx_bench_count == 2


def test_case_round_trips_docx_structure_through_json():
    structure = build_docx_structure(make_docx(JUDGMENT))
    case = Case(itemid="001-test", docx_structure=structure)
    restored = Case.model_validate_json(case.model_dump_json())
    assert restored.docx_structure is not None
    assert [o.authors for o in restored.docx_structure.opinions] == [
        o.authors for o in structure.opinions
    ]


def test_compare_requires_structure():
    with pytest.raises(ValueError):
        compare_docx_structure(Case(itemid="001-none"))


def test_corpus_helper_writes_docx_structure_jsonl(tmp_path):
    import json

    from hudoc_py.bilingual.corpus import _write_docx_structures

    docx_dir = tmp_path / "docs" / "docx"
    docx_dir.mkdir(parents=True)
    (docx_dir / "001-a.docx").write_bytes(make_docx(JUDGMENT))
    sections = Sections(
        procedure="…",
        facts="…",
        the_law="…",
        operative="…",
        separate_opinion="…",
        opinions=[Opinion(opinion_type="dissenting", authors=["X"])] * 3,
    )
    cases = [Case(itemid="001-a", sections=sections), Case(itemid="001-missing")]
    counts = _write_docx_structures(cases, tmp_path)
    assert counts == {"cases": 2, "with_docx": 1, "sections_agree": 1, "opinions_agree": 1}
    lines = [
        json.loads(line) for line in (tmp_path / "docx_structure.jsonl").read_text().splitlines()
    ]
    assert (
        lines[0]["itemid"] == "001-a"
        and lines[0]["structure"]["opinions"][2]["authors"][-1] == "BORREGO BORREGO"
    )
    assert lines[0]["agreement"]["opinion_counts_agree"] is True
    assert lines[1] == {"itemid": "001-missing", "docx_path": None, "structure": None}
    assert cases[0].docx_structure is not None and cases[1].docx_structure is None


def test_old_template_subheadings_stay_inside_opinions_and_order_is_enforced():
    paragraphs = [
        _p("JuHHead", "PROCEDURE"),
        _p("JuPara", "1.  The case was referred."),
        _p("JuHHead", "AS TO THE FACTS"),
        _p(
            "JuHHead", "PROCEEDINGS BEFORE THE COMMISSION"
        ),  # a sub-heading printed in the section style
        _p("JuPara", "2.  The Commission declared the application admissible."),
        _p("JuHHead", "AS TO THE LAW"),
        _p("JuPara", "3.  The Court holds."),
        _p("JuHHead", "FOR THESE REASONS, THE COURT"),
        _p("OpiHHead", "DISSENTING OPINION OF JUDGE ZEKIA"),
        _p("OpiHHead", "I. The issue of applicability in general"),
        _p("OpiPara", "I cannot agree."),
        _p("OpiHHead", ""),
        _p("OpiHHead", "JOINT DISSENTING OPINION"),
        _p("OpiHHead", "OF JUDGES MATSCHER AND PETTITI"),
        _p("OpiPara", "We dissent."),
    ]
    structure = build_docx_structure(make_docx(paragraphs))
    assert [s.name for s in structure.sections] == [
        "procedure",
        "facts",
        "the_law",
        "operative",
        "separate_opinion",
    ]
    assert "section_heading_out_of_order:procedure:3" in structure.diagnostics
    assert [o.authors for o in structure.opinions] == [["ZEKIA"], ["MATSCHER", "PETTITI"]]
    assert structure.opinions[0].end_index == 12  # the merged heading starts the second opinion


def test_2013_template_titles_and_centred_opinion_headings():
    paragraphs = [
        _p("ECHRCoverTitle4", "CASE OF A v. B"),
        _p("ECHRTitle1", "PROCEDURE"),
        _p("ECHRPara", "1.  The case originated in an application."),
        _p("ECHRTitle1", "THE FACTS"),
        _p("ECHRHeading1", "I. THE CIRCUMSTANCES OF THE CASE"),
        _p("ECHRPara", "2.  The applicant was born in 1960."),
        _p("ECHRParaQuote", "“Quoted domestic law.”"),
        _p("ECHRTitle1", "THE LAW"),
        _p("ECHRHeading1", "I. ALLEGED VIOLATION OF ARTICLE 3 OF THE CONVENTION"),
        _p("ECHRPara", "3.  The Court reiterates."),
        _p("ECHRTitle1", "FOR THESE REASONS, THE COURT"),
        _p("ECHRTitleCentre1", "CONCURRING OPINION OF JUDGE ZIEMELE"),
        _p("OpiPara", "I agree."),
        _p("ECHRTitleCentre1", "PARTLY DISSENTING OPINION OF JUDGE CHARLETON"),
        _p("OpiPara", "I disagree in part."),
    ]
    structure = build_docx_structure(make_docx(paragraphs))
    assert [b.role for b in structure.blocks[:3]] == ["case_title", "section_heading", "paragraph"]
    assert structure.blocks[6].role == "quotation"
    assert [s.name for s in structure.sections] == [
        "procedure",
        "facts",
        "the_law",
        "operative",
        "separate_opinion",
    ]
    assert structure.sections[-1].start_index == 11 and structure.sections[-2].end_index == 11
    assert [(o.opinion_type, o.authors) for o in structure.opinions] == [
        ("concurring", ["ZIEMELE"]),
        ("partly_dissenting", ["CHARLETON"]),
    ]


def test_case_accepts_docx_structure_serialised_as_json_string():
    import json

    structure = build_docx_structure(make_docx(JUDGMENT))
    row = Case(itemid="001-test", docx_structure=structure).model_dump(mode="json")
    row["docx_structure"] = json.dumps(
        row["docx_structure"]
    )  # as written to Parquet/CSV by smart-fetch
    restored = Case.model_validate(row)
    assert restored.docx_structure is not None and len(restored.docx_structure.opinions) == 3
    assert Case.model_validate({"itemid": "001-x", "docx_structure": ""}).docx_structure is None
    assert (
        Case.model_validate({"itemid": "001-x", "docx_structure": float("nan")}).docx_structure
        is None
    )


def test_court_assessment_subheading_is_a_section_like_in_html():
    paragraphs = [
        _p("JuHHead", "THE FACTS"),
        _p("JuPara", "1.  Facts."),
        _p("JuHHead", "THE LAW"),
        _p("JuHA", "A. The parties’ submissions"),
        _p("JuPara", "2.  The Government argued."),
        _p("JuHA", "B. The Court’s assessment"),
        _p("JuPara", "3.  The Court finds."),
        _p(
            "JuH1", "The Court’s assessment"
        ),  # the Registry's usual unprefixed form, repeated per complaint
        _p("JuPara", "4.  The Court also finds."),
        _p("JuHHead", "FOR THESE REASONS, THE COURT"),
    ]
    structure = build_docx_structure(make_docx(paragraphs))
    assert [s.name for s in structure.sections] == [
        "facts",
        "the_law",
        "court_assessment",
        "operative",
    ]
    assert structure.sections[2].start_index == 5
    assert not any(d.startswith("section_heading_out_of_order") for d in structure.diagnostics)


def test_corpus_helper_restores_persisted_sections_on_resumed_builds(tmp_path):
    import json

    from hudoc_py.bilingual.corpus import _write_docx_structures

    (tmp_path / "docs" / "docx").mkdir(parents=True)
    (tmp_path / "docs" / "docx" / "001-a.docx").write_bytes(make_docx(JUDGMENT))
    sections = Sections(
        procedure="…",
        facts="…",
        the_law="…",
        operative="…",
        separate_opinion="…",
        opinions=[Opinion(opinion_type="dissenting", authors=["X"])] * 3,
    )
    (tmp_path / "sections.jsonl").write_text(
        json.dumps({"itemid": "001-a", "sections": sections.model_dump(mode="json")}) + "\n"
    )
    counts = _write_docx_structures(
        [Case(itemid="001-a")], tmp_path, sections_path=tmp_path / "sections.jsonl"
    )
    assert counts == {"cases": 1, "with_docx": 1, "sections_agree": 1, "opinions_agree": 1}
    record = json.loads((tmp_path / "docx_structure.jsonl").read_text().splitlines()[0])
    assert record["agreement"]["opinion_counts_agree"] is True


def test_external_entities_are_not_resolved(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET")
    document = (
        '<?xml version="1.0"?><!DOCTYPE w:document [<!ENTITY xxe SYSTEM "file://'
        + str(secret)
        + '">]>'
        f'<w:document xmlns:w="{W}"><w:body><w:p><w:pPr><w:pStyle w:val="JuPara"/></w:pPr><w:r><w:t>1.  &xxe;</w:t></w:r></w:p></w:body></w:document>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document)
    structure = build_docx_structure(buffer.getvalue())
    assert "TOP SECRET" not in " ".join(b.text for b in structure.blocks)


def test_opinion_section_fallback_truncates_the_operative_section_before_an_appendix():
    paragraphs = [
        _p("ECHRTitle1", "THE LAW"),
        _p("ECHRPara", "1.  The Court finds."),
        _p("ECHRTitle1", "FOR THESE REASONS, THE COURT"),
        _p("ECHRPara", "Holds that there has been a violation."),
        _p("ECHRTitleCentre1", "CONCURRING OPINION OF JUDGE ZIEMELE"),
        _p("OpiPara", "I agree."),
        _p("ECHRTitle1", "APPENDIX"),
        _p("ECHRPara", "List of applicants."),
    ]
    structure = build_docx_structure(make_docx(paragraphs))
    by_name = {s.name: s for s in structure.sections}
    assert by_name["operative"].end_index == 4
    assert (
        by_name["separate_opinion"].start_index == 4 and by_name["separate_opinion"].end_index == 6
    )
    assert by_name["appendix"].start_index == 6


def test_download_sessions_identify_the_package():
    import re
    from pathlib import Path

    import hudoc_py
    from hudoc_py.main.downloader import DOWNLOAD_HEADERS, download_headers

    root = Path(hudoc_py.__file__).parent
    offenders = [
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if re.search(r"ClientSession\(headers=DOWNLOAD_HEADERS\)", path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
    assert DOWNLOAD_HEADERS["User-Agent"].startswith("echr-py")
    assert (
        download_headers()["User-Agent"]
        == f"echr-py/{hudoc_py.__version__} (+https://github.com/niccoloridi/echr-py)"
    )
