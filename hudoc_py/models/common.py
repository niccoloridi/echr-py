"""Shared model bits: section container, value normalizers."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field


class TextProvenance(BaseModel):
    """Where a :class:`~hudoc_py.models.case.Case`'s ``text`` came from.

    ``is_fallback`` is true when the text was loaded from a different document
    than the case itself – in practice the French-language sibling of an
    English placeholder case (see :mod:`hudoc_py.bilingual`).
    """

    model_config = ConfigDict(extra="ignore")

    source_itemid: str
    source_language: str = ""
    is_fallback: bool = False


OpinionType: TypeAlias = Literal[
    "dissenting",
    "concurring",
    "partly_dissenting",
    "partly_concurring",
    "partly_concurring_partly_dissenting",
    "separate",
    "declaration",
]

BenchRole: TypeAlias = Literal[
    "president",
    "vice_president",
    "section_president",
    "judge",
    "ad_hoc_judge",
]


class BenchMember(BaseModel):
    """One judge parsed from the deciding formation's composition block."""

    model_config = ConfigDict(extra="ignore")

    name: str
    raw_name: str
    role: BenchRole = "judge"
    country: str | None = None
    region: str | None = None
    tenure_start: int | None = None
    tenure_end: int | None = None
    is_ad_hoc: bool = False
    source_block_id: str | None = None
    char_start: int | None = None
    char_end: int | None = None


class BenchComposition(BaseModel):
    """Deterministically parsed majority formation, separate from opinion authors."""

    model_config = ConfigDict(extra="ignore")

    members: list[BenchMember] = Field(default_factory=list)
    language: str = ""
    raw_text: str = ""
    source_block_ids: list[str] = Field(default_factory=list)
    char_start: int | None = None
    char_end: int | None = None
    confidence: float = 0.0
    diagnostics: list[str] = Field(default_factory=list)

    @property
    def judges(self) -> list[str]:
        """Canonical judge names in source order."""
        return [member.name for member in self.members]


class Opinion(BaseModel):
    """One individual separate opinion (or declaration) from a judgment.

    Produced by :func:`hudoc_py.text.split_opinions` from the combined
    ``separate_opinion`` section. ``authors`` and ``joined_by`` preserve the
    heading's distinct roles; ``judges`` is their backwards-compatible union.
    """

    model_config = ConfigDict(extra="ignore")

    opinion_type: OpinionType
    joint: bool = False
    joint_heading: bool = False
    authors: list[str] = Field(default_factory=list)
    joined_by: list[str] = Field(default_factory=list)
    judges: list[str] = Field(default_factory=list)
    raw_header: str = ""
    text: str = ""
    body: str = ""
    language: str = ""  # "EN" / "FR" (from the heading matched)
    opinion_id: str | None = None
    ordinal: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    start_block: int | None = None
    end_block: int | None = None


class OpinionSplit(BaseModel):
    """Opinions plus machine-readable parser quality information."""

    model_config = ConfigDict(extra="ignore")

    opinions: list[Opinion] = Field(default_factory=list)
    confidence: float = 1.0
    diagnostics: list[str] = Field(default_factory=list)


CanonicalSection: TypeAlias = Literal[
    "procedure",
    "facts",
    "subject_matter",
    "complaints",
    "the_law",
    "court_assessment",
    "operative",
    "separate_opinion",
    "appendix",
]

BlockType: TypeAlias = Literal["heading", "paragraph", "list_item", "blockquote", "footnote"]
HeadingRole: TypeAlias = Literal[
    "frontmatter",
    "toc_entry",
    "section",
    "subsection",
    "operative",
    "separate_opinion",
    "appendix",
    "artifact",
]


class SegmentationDiagnostic(BaseModel):
    """One auditable warning or information item from text segmentation."""

    model_config = ConfigDict(extra="ignore")

    code: str
    severity: Literal["info", "warning", "error"] = "warning"
    message: str
    section: CanonicalSection | None = None
    block_index: int | None = None
    char_start: int | None = None
    char_end: int | None = None


class InlineTextRun(BaseModel):
    """One style-aware run addressed within a :class:`DocumentBlock`.

    Offsets are relative to the block's plain ``text``.  Runs are optional so
    spines serialized before inline typography was retained remain valid.
    """

    model_config = ConfigDict(extra="ignore")

    text: str
    start: int
    end: int
    bold: bool = False
    italic: bool = False


class FootnoteReference(BaseModel):
    """One inline reference to a document-local footnote body."""

    model_config = ConfigDict(extra="ignore")

    footnote_id: str
    label: str
    start: int
    end: int
    target_anchor: str | None = None


class DocumentFootnote(BaseModel):
    """A footnote body and the source blocks that invoke it."""

    model_config = ConfigDict(extra="ignore")

    footnote_id: str
    label: str
    text: str
    body_block_id: str
    body_block_ids: list[str] = Field(default_factory=list)
    reference_block_ids: list[str] = Field(default_factory=list)
    reference_para_ids: list[str] = Field(default_factory=list)


class DocumentBlock(BaseModel):
    """One source-order block in a versioned HUDOC document spine.

    Paragraph and heading boundaries come from the HUDOC HTML when available.
    ``char_start`` and ``char_end`` address the exact plain text stored in
    :attr:`Sections.full`.  ``heading_source`` explains why a block was treated
    as a heading instead of hiding that decision inside a regex.
    """

    model_config = ConfigDict(extra="ignore")

    block_id: str
    type: BlockType
    text: str
    char_start: int
    char_end: int
    para_id: str | None = None
    para_num: int | None = None
    legal_para_id: str | None = None
    legal_para_num: int | None = None
    heading_level: int | None = None
    heading_role: HeadingRole | None = None
    heading_source: list[str] = Field(default_factory=list)
    section: CanonicalSection | None = None
    source_tag: str | None = None
    source_classes: list[str] = Field(default_factory=list)
    source_style: dict[str, str] = Field(default_factory=dict)
    inline_runs: list[InlineTextRun] = Field(default_factory=list)
    footnote_id: str | None = None
    footnote_references: list[FootnoteReference] = Field(default_factory=list)
    referenced_by_block_ids: list[str] = Field(default_factory=list)
    referenced_by_para_ids: list[str] = Field(default_factory=list)
    opinion_id: str | None = None
    opinion_ordinal: int | None = None
    opinion_type: OpinionType | None = None
    opinion_authors: list[str] = Field(default_factory=list)
    opinion_joined_by: list[str] = Field(default_factory=list)


class DocumentSpine(BaseModel):
    """Versioned, source-order representation underlying canonical sections."""

    model_config = ConfigDict(extra="ignore")

    schema_version: Literal["hudoc-spine.v1"] = "hudoc-spine.v1"
    document_id: str | None = None
    source_format: Literal["hudoc_html", "plain_text"]
    blocks: list[DocumentBlock] = Field(default_factory=list)
    first_substantive_block_index: int | None = None
    diagnostics: list[SegmentationDiagnostic] = Field(default_factory=list)
    footnotes: list[DocumentFootnote] = Field(default_factory=list)


class SectionSpan(BaseModel):
    """Exact source and block span for one canonical legal section.

    ``end_block`` is exclusive, matching ordinary Python slice semantics.
    """

    model_config = ConfigDict(extra="ignore")

    section: CanonicalSection
    heading: str
    char_start: int
    char_end: int
    start_block: int
    end_block: int
    paragraph_ids: list[str] = Field(default_factory=list)


class DispositiveParagraph(BaseModel):
    """One source-addressable ruling from the operative part."""

    model_config = ConfigDict(extra="ignore")

    disposition_id: str
    document_id: str | None = None
    block_id: str
    block_ids: list[str] = Field(default_factory=list)
    para_id: str | None = None
    paragraph_ids: list[str] = Field(default_factory=list)
    order: int
    text: str
    decision: str | None = None
    vote: str | None = None


class Sections(BaseModel):
    """Segmented sections of a HUDOC judgment text.

    Two granularities:

    * :func:`hudoc_py.text.segment_main_sections` – fast simple split:
      ``the_law`` and ``dispositif`` only.
    * :func:`hudoc_py.text.segment_full` – rich nine-section split:
      ``procedure``, ``facts``, ``complaints``, ``the_law``, ``operative``,
      ``subject_matter``, ``court_assessment``, ``separate_opinion``,
      ``appendix``. EN + FR markers, multiple layout variants per section.

    ``confidence`` is a deterministic 0.0-1.0 quality score based on template
    coverage, section breadth, source format, and anomalous slice sizes. It is
    diagnostic metadata, not a calibrated probability of correctness.
    """

    model_config = ConfigDict(extra="ignore")

    full: str | None = None

    # Simple split (legacy, fast)
    the_law: str | None = None
    dispositif: str | None = None

    # Rich split – one slice per canonical judgment section.
    procedure: str | None = None
    facts: str | None = None
    complaints: str | None = None
    operative: str | None = None
    subject_matter: str | None = None
    court_assessment: str | None = None
    separate_opinion: str | None = None
    appendix: str | None = None

    # Individual opinions split out of separate_opinion (empty if none found).
    opinions: list[Opinion] = Field(default_factory=list)
    opinions_confidence: float = 1.0
    opinion_diagnostics: list[str] = Field(default_factory=list)

    # The judges deciding the case, parsed from the front-matter composition
    # block. This is independent of the authors of separate opinions above.
    bench: BenchComposition | None = None

    # Quality metadata
    confidence: float = 0.0
    doctype_mode: str | None = (
        None  # "judgment", "communicated_case", "commission_decision", "info_note", "press_release", "unknown"
    )
    status: Literal["complete", "partial", "unsegmented", "not_applicable"] = "unsegmented"
    parser_version: str = "3"
    diagnostics: list[SegmentationDiagnostic] = Field(default_factory=list)
    spans: list[SectionSpan] = Field(default_factory=list)
    spine: DocumentSpine | None = None

    @property
    def found(self) -> list[str]:
        """Return the names of sections that have content, in canonical order."""
        return [
            name
            for name in (
                "procedure",
                "facts",
                "subject_matter",
                "complaints",
                "the_law",
                "court_assessment",
                "operative",
                "separate_opinion",
                "appendix",
            )
            if getattr(self, name)
        ]


def split_semicolon_list(value: Any) -> list[str]:
    """HUDOC packs multi-valued fields (e.g. articles, applications) as
    semicolon-separated strings. Normalize them to a clean list of strings.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        if not value.strip():
            return []
        return [p.strip() for p in value.split(";") if p.strip()]
    # pandas commonly returns a NumPy array for a Parquet list column. Treat
    # any non-string iterable as the original list rather than serialising the
    # entire array into one value such as ``"['38263/08']"``.
    if isinstance(value, Iterable):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()]


def parse_hudoc_date(value: Any) -> date | None:
    """HUDOC returns dates as ``2024-03-15T00:00:00.0Z`` or as bare ISO. Both supported."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    if "T" in s:
        s = s.split("T", 1)[0]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def parse_float_loose(value: Any) -> float | None:
    """Parse HUDOC numeric strings (e.g. the ``Rank`` relevance score) leniently."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def parse_bool_loose(value: Any) -> bool | None:
    """HUDOC encodes booleans as 'TRUE'/'FALSE' strings; accept the usual variants."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    s = str(value).strip().upper()
    if s in ("TRUE", "1", "YES", "Y"):
        return True
    if s in ("FALSE", "0", "NO", "N"):
        return False
    return None
