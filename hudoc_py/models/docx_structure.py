"""Opt-in document structure read from a HUDOC DOCX rendition.

HUDOC's HTML is generated from the Registry's Word files and keeps only
hashed formatting classes. The DOCX keeps the Registry template's named
paragraph styles (``Ju_H_Head``, ``Ju_Para``, ``Ju_Quot``, ``Opi_H_Head``,
``Opi_Para`` ...), which label sections, numbered paragraphs, quotations,
the bench and separate opinions directly. This layer records those labels as
an advisory structure next to the canonical HTML-based segmentation. It never
changes HTML-derived outputs.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .common import CanonicalSection, OpinionType

DocxRole = Literal[
    "section_heading",
    "heading",
    "heading_article",
    "paragraph",
    "paragraph_last",
    "quotation",
    "list",
    "judges",
    "court",
    "names",
    "signed",
    "case_title",
    "opinion_heading",
    "opinion_subheading",
    "opinion_paragraph",
    "opinion_quotation",
    "opinion_note",
    "decision_title",
    "toc",
    "summary",
    "footnote_text",
    "other",
]


class DocxBlock(BaseModel):
    """One body paragraph of the DOCX, in document order."""

    index: int
    style_id: str | None = None
    role: DocxRole = "other"
    text: str
    para_num: int | None = None
    in_table: bool = False
    footnote_ids: list[str] = Field(default_factory=list)


class DocxSection(BaseModel):
    name: CanonicalSection
    heading_text: str
    start_index: int
    end_index: int  # exclusive


class DocxOpinion(BaseModel):
    ordinal: int
    heading_text: str
    start_index: int
    end_index: int  # exclusive
    opinion_type: OpinionType | None = None
    authors: list[str] = Field(default_factory=list)
    joined_by: list[str] = Field(default_factory=list)
    joint: bool | None = None


class DocxFootnote(BaseModel):
    footnote_id: str
    text: str
    invoking_block_indexes: list[int] = Field(default_factory=list)


class DocxStructure(BaseModel):
    schema_version: Literal["hudoc-docx-structure.v1"] = "hudoc-docx-structure.v1"
    itemid: str | None = None
    sha256: str
    byte_count: int
    blocks: list[DocxBlock] = Field(default_factory=list)
    sections: list[DocxSection] = Field(default_factory=list)
    opinions: list[DocxOpinion] = Field(default_factory=list)
    footnotes: list[DocxFootnote] = Field(default_factory=list)
    bench_lines: list[str] = Field(default_factory=list)
    style_counts: dict[str, int] = Field(default_factory=dict)
    unmapped_styles: dict[str, int] = Field(default_factory=dict)
    diagnostics: list[str] = Field(default_factory=list)

    @property
    def paragraph_count(self) -> int:
        return sum(1 for b in self.blocks if b.role in ("paragraph", "paragraph_last"))


class DocxAgreement(BaseModel):
    """Descriptive comparison between the HTML-based segmentation and the DOCX structure."""

    schema_version: Literal["hudoc-docx-agreement.v1"] = "hudoc-docx-agreement.v1"
    html_sections: list[str] = Field(default_factory=list)
    docx_sections: list[str] = Field(default_factory=list)
    #: Agreement on the main sections (procedure, facts, the_law, operative, separate_opinion, appendix);
    #: sub-sections are reported in ``differences`` as ``subsection:...`` without deciding this flag.
    sections_agree: bool
    html_opinion_count: int
    docx_opinion_count: int
    opinion_counts_agree: bool
    html_opinion_authors: list[list[str]] = Field(default_factory=list)
    docx_opinion_authors: list[list[str]] = Field(default_factory=list)
    opinion_authors_agree: bool
    html_bench_count: int | None = None
    docx_bench_count: int | None = None
    differences: list[str] = Field(default_factory=list)


__all__ = [
    "DocxAgreement",
    "DocxBlock",
    "DocxFootnote",
    "DocxOpinion",
    "DocxRole",
    "DocxSection",
    "DocxStructure",
]
