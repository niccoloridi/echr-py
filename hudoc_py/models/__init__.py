"""Pydantic v2 models for HUDOC data."""

from .case import Case, CaseCollection
from .citation import Citation, CitationCollection
from .common import (
    BenchComposition,
    BenchMember,
    BenchRole,
    CanonicalSection,
    DispositiveParagraph,
    DocumentBlock,
    DocumentFootnote,
    DocumentSpine,
    FootnoteReference,
    InlineTextRun,
    Opinion,
    OpinionSplit,
    OpinionType,
    Sections,
    SectionSpan,
    SegmentationDiagnostic,
    TextProvenance,
)
from .document import Document
from .exec_case import ExecutionCase, ExecutionCaseCollection
from .exec_document import ExecutionDocument, ExecutionDocumentCollection
from .version import AcquisitionManifest, DocumentVersion, FormatOutcome, ManifestFile

__all__ = [
    "Case",
    "CaseCollection",
    "BenchComposition",
    "BenchMember",
    "BenchRole",
    "Citation",
    "CitationCollection",
    "Sections",
    "CanonicalSection",
    "DispositiveParagraph",
    "DocumentBlock",
    "DocumentFootnote",
    "DocumentSpine",
    "FootnoteReference",
    "InlineTextRun",
    "SectionSpan",
    "SegmentationDiagnostic",
    "TextProvenance",
    "Opinion",
    "OpinionSplit",
    "OpinionType",
    "Document",
    "DocumentVersion",
    "ManifestFile",
    "FormatOutcome",
    "AcquisitionManifest",
    "ExecutionCase",
    "ExecutionCaseCollection",
    "ExecutionDocument",
    "ExecutionDocumentCollection",
]
