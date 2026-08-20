"""Typed models for authoritative case-law citation resolution."""

from __future__ import annotations

import datetime as dt
from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field, computed_field

ResolutionStatus = Literal[
    "resolved_identifier",
    "resolved_authority",
    "resolved_metadata",
    "resolved_override",
    "ambiguous_document",
    "unresolved_reference",
    "target_not_in_hudoc",
]
ResolvedStatus = Literal[
    "resolved_identifier",
    "resolved_authority",
    "resolved_metadata",
    "resolved_override",
]
ProceduralPhase = Literal[
    "merits",
    "preliminary_objections",
    "admissibility",
    "article_50",
    "just_satisfaction",
    "revision",
    "striking_out",
    "friendly_settlement",
    "advisory_opinion",
    "commission_decision",
    "commission_report",
    "unknown",
]
ReporterFamily = Literal[
    "series_a",
    "reports",
    "echr",
    "dr",
    "commission_report",
    "commission_collection",
]

RESOLVED_STATUSES: frozenset[str] = frozenset(
    {
        "resolved_identifier",
        "resolved_authority",
        "resolved_metadata",
        "resolved_override",
    }
)


class ReporterLocator(BaseModel):
    """A structured reporter citation parsed from a printed reference."""

    family: ReporterFamily
    year: int | None = None
    volume: str | None = None
    number: str | None = None
    suffix: str | None = None
    page: int | None = None
    extracts: bool = False
    raw: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def key(self) -> str:
        bits = [self.family, str(self.year or ""), self.volume or "", self.number or ""]
        bits.extend([self.suffix or "", str(self.page or "")])
        return ":".join(bit.upper() for bit in bits)


class CitationMention(BaseModel):
    """One SCL fragment parsed into bibliographic evidence."""

    mention_id: str
    reference_hash: str
    source_itemid: str | None = None
    source_ecli: str | None = None
    source_appnos: list[str] = Field(default_factory=list)
    source_language: str | None = None
    source_date: dt.date | None = None
    ordinal: int
    raw_ref: str
    normalized_ref: str
    cited_name: str | None = None
    respondent: str | None = None
    explicit_ecli: str | None = None
    explicit_itemid: str | None = None
    advisory_request_id: str | None = None
    explicit_appnos: list[str] = Field(default_factory=list)
    scl_appno_candidates: list[str] = Field(default_factory=list)
    target_date: dt.date | None = None
    target_year: int | None = None
    target_month: int | None = None
    target_day: int | None = None
    document_kind: Literal["judgment", "decision", "commission", "advisory", "unknown"] = "unknown"
    procedural_phase: ProceduralPhase = "unknown"
    grand_chamber: bool = False
    reporter: ReporterLocator | None = None
    target_paragraphs: list[str] = Field(default_factory=list)
    source_context: str | None = None
    origin: Literal["scl", "text_discovery"] = "scl"
    source_section: str | None = None
    source_block_id: str | None = None
    source_para_id: str | None = None
    source_opinion_id: str | None = None
    source_footnote_id: str | None = None
    source_invoking_block_ids: list[str] = Field(default_factory=list)
    source_invoking_para_ids: list[str] = Field(default_factory=list)
    discovery_evidence: dict[str, object] = Field(default_factory=dict)


class CitationCandidate(BaseModel):
    """One canonical HUDOC document considered for a mention."""

    node_id: str
    itemid: str | None = None
    ecli: str | None = None
    advisory_request_id: str | None = None
    docname: str | None = None
    title_aliases: list[str] = Field(default_factory=list)
    reporter_keys: list[str] = Field(default_factory=list)
    casecitations: list[str] = Field(default_factory=list)
    appnos: list[str] = Field(default_factory=list)
    date: dt.date | None = None
    language: str | None = None
    doctype: str | None = None
    document_kind: str = "unknown"
    procedural_phase: ProceduralPhase = "unknown"
    grand_chamber: bool = False
    respondent: list[str] = Field(default_factory=list)
    is_placeholder: bool | None = None
    in_source_corpus: bool = False
    hudoc_url: str | None = None
    positive_evidence: list[str] = Field(default_factory=list)
    conflicting_evidence: list[str] = Field(default_factory=list)
    title_similarity: float = 0.0


class CitationResolution(BaseModel):
    """Resolution outcome and its complete audit trail for one mention."""

    mention: CitationMention
    status: ResolutionStatus
    method: str
    target: CitationCandidate | None = None
    candidates: list[CitationCandidate] = Field(default_factory=list)
    authority_entry_id: str | None = None
    override_note: str | None = None
    override_reviewed_at: str | None = None
    documented_exclusion: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def resolved(self) -> bool:
        return self.status in RESOLVED_STATUSES and self.target is not None


class CitationAuthorityEntry(BaseModel):
    """One exact reference from an authoritative citation concordance."""

    entry_id: str
    entry_source: Literal["official_master", "curated_supplement"] = "official_master"
    language: Literal["eng", "fra"] | None = None
    citation: str
    normalized_citation: str
    title: str | None = None
    normalized_title: str = ""
    appnos: list[str] = Field(default_factory=list)
    date: dt.date | None = None
    document_kind: str = "unknown"
    procedural_phase: ProceduralPhase = "unknown"
    grand_chamber: bool = False
    reporter: ReporterLocator | None = None
    target_ecli: str | None = None
    target_itemid: str | None = None
    target_docname: str | None = None
    target_unavailable: bool = False
    coverage_note: str | None = None
    equivalent_entry_ids: list[str] = Field(default_factory=list)


class CitationAuthoritySource(BaseModel):
    language: Literal["eng", "fra"]
    url: str
    retrieved_at: str
    updated_through: str | None = None
    sha256: str
    entry_count: int


class CitationAuthority(BaseModel):
    """Versioned, provenance-bearing collection of exact reference entries."""

    schema_version: str = "citation-authority/v1"
    source_url: str
    updated_through: str | None = None
    imported_at: str = Field(default_factory=lambda: dt.datetime.now(dt.UTC).isoformat())
    parser_version: str = "1"
    source_sha256: str | None = None
    coverage: Literal["seed", "full"] = "full"
    sources: list[CitationAuthoritySource] = Field(default_factory=list)
    entries: list[CitationAuthorityEntry] = Field(default_factory=list)


class CitationOverride(BaseModel):
    """A reviewed source-specific or reusable reference mapping."""

    reference_hash: str
    target_ecli: str | None = None
    target_itemid: str | None = None
    source_ecli: str | None = None
    source_itemid: str | None = None
    reviewer_note: str = ""
    reviewed_at: str = ""


class CitationResolutionReport(BaseModel):
    """Completeness and provenance summary for a resolution run."""

    schema_version: str = "citation-resolution/v1"
    authority_schema_version: str | None = None
    authority_parser_version: str | None = None
    authority_source_url: str | None = None
    authority_updated_through: str | None = None
    authority_source_sha256: str | None = None
    authority_coverage: Literal["seed", "full"] | None = None
    authority_entry_count: int = 0
    authority_supplement_count: int = 0
    source_documents: int = 0
    mentions: int = 0
    resolved: int = 0
    unresolved: int = 0
    documented_exclusions: int = 0
    review_required: int = 0
    statuses: dict[str, int] = Field(default_factory=dict)
    methods: dict[str, int] = Field(default_factory=dict)
    override_count: int = 0
    target_documents: int = 0
    edge_count: int = 0
    placeholder_nodes: int = 0
    unidentified_source_documents: int = 0
    lookup_errors: int = 0
    one_hop: bool = True

    @computed_field  # type: ignore[prop-decorator]
    @property
    def completeness(self) -> float:
        return (
            1.0
            if self.mentions == 0
            else (self.resolved + self.documented_exclusions) / self.mentions
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def complete(self) -> bool:
        return (
            self.review_required == 0
            and self.placeholder_nodes == 0
            and self.unidentified_source_documents == 0
        )

    @classmethod
    def from_resolutions(
        cls,
        resolutions: list[CitationResolution],
        *,
        source_documents: int,
        authority: CitationAuthority | None,
        target_documents: int,
        edge_count: int,
        placeholder_nodes: int = 0,
        lookup_errors: int = 0,
        unidentified_source_documents: int = 0,
    ) -> CitationResolutionReport:
        statuses = Counter(result.status for result in resolutions)
        methods = Counter(result.method for result in resolutions)
        resolved = sum(result.resolved for result in resolutions)
        documented_exclusions = sum(result.documented_exclusion for result in resolutions)
        unresolved = len(resolutions) - resolved
        return cls(
            authority_schema_version=authority.schema_version if authority else None,
            authority_parser_version=authority.parser_version if authority else None,
            authority_source_url=authority.source_url if authority else None,
            authority_updated_through=authority.updated_through if authority else None,
            authority_source_sha256=authority.source_sha256 if authority else None,
            authority_coverage=authority.coverage if authority else None,
            authority_entry_count=len(authority.entries) if authority else 0,
            authority_supplement_count=(
                sum(entry.entry_source == "curated_supplement" for entry in authority.entries)
                if authority
                else 0
            ),
            source_documents=source_documents,
            mentions=len(resolutions),
            resolved=resolved,
            unresolved=unresolved,
            documented_exclusions=documented_exclusions,
            review_required=unresolved - documented_exclusions,
            statuses={str(key): value for key, value in statuses.items()},
            methods=dict(methods),
            override_count=statuses.get("resolved_override", 0),
            target_documents=target_documents,
            edge_count=edge_count,
            placeholder_nodes=placeholder_nodes,
            lookup_errors=lookup_errors,
            unidentified_source_documents=unidentified_source_documents,
        )


class IncompleteCitationResolutionError(RuntimeError):
    """Raised when measurement-grade output is requested from an incomplete run."""


class CitationResolutionResult(BaseModel):
    """Complete in-memory result consumed by persistence and graph builders."""

    resolutions: list[CitationResolution] = Field(default_factory=list)
    targets: list[CitationCandidate] = Field(default_factory=list)
    nodes: list[dict[str, object]] = Field(default_factory=list)
    edges: list[dict[str, object]] = Field(default_factory=list)
    report: CitationResolutionReport

    def require_complete(self) -> CitationResolutionResult:
        if not self.report.complete:
            raise IncompleteCitationResolutionError(
                f"citation resolution is {self.report.completeness:.1%} complete: "
                f"{self.report.review_required} of {self.report.mentions} references require review; "
                f"{self.report.documented_exclusions} documented exclusions; "
                f"{self.report.placeholder_nodes} placeholder nodes; "
                f"{self.report.unidentified_source_documents} unidentified sources"
            )
        return self


class CitationParagraphResolution(BaseModel):
    """Deterministic mapping of one printed pinpoint to target spine blocks."""

    printed_label: str
    status: Literal["exact", "range", "partial", "ambiguous", "missing", "unavailable"]
    target_itemid: str | None = None
    target_ecli: str | None = None
    target_language: str | None = None
    target_block_ids: list[str] = Field(default_factory=list)
    target_para_ids: list[str] = Field(default_factory=list)
    target_para_nums: list[int] = Field(default_factory=list)
    target_sections: list[str] = Field(default_factory=list)
    evidence: dict[str, object] = Field(default_factory=dict)


class CitationSourceInvocation(BaseModel):
    """Legal source address through which a citation-bearing footnote is invoked."""

    source_block_id: str
    source_para_id: str | None = None
    source_para_num: int | None = None
    source_section: str | None = None
    source_component: Literal["majority", "opinion", "appendix"] = "majority"
    source_opinion_id: str | None = None
    source_opinion_ordinal: int | None = None
    source_opinion_type: str | None = None
    source_opinion_authors: list[str] = Field(default_factory=list)
    source_opinion_joined_by: list[str] = Field(default_factory=list)


class CitationOccurrence(BaseModel):
    """One source-addressable textual occurrence of an SCL authority."""

    schema_version: Literal[
        "citation-occurrence/v1", "citation-occurrence/v2", "citation-occurrence/v3"
    ] = (
        "citation-occurrence/v3"
    )
    occurrence_id: str
    locus_id: str | None = None
    citation_group_id: str | None = None
    group_ordinal: int = 1
    group_size: int = 1
    mention_id: str
    source_itemid: str | None = None
    source_language: str | None = None
    source_section: str | None = None
    source_block_id: str
    source_para_id: str | None = None
    source_para_num: int | None = None
    block_start: int
    block_end: int
    document_start: int
    document_end: int
    raw_text: str
    source_context: str
    italic: bool = False
    bold: bool = False
    finder: str
    evidence: dict[str, object] = Field(default_factory=dict)
    target_node_id: str | None = None
    target_ecli: str | None = None
    target_itemid: str | None = None
    target_appnos: list[str] = Field(default_factory=list)
    target_paragraphs: list[str] = Field(default_factory=list)
    source_component: Literal["majority", "opinion", "appendix"] = "majority"
    source_opinion_id: str | None = None
    source_opinion_ordinal: int | None = None
    source_opinion_type: str | None = None
    source_opinion_authors: list[str] = Field(default_factory=list)
    source_opinion_joined_by: list[str] = Field(default_factory=list)
    source_footnote_id: str | None = None
    source_invoking_block_ids: list[str] = Field(default_factory=list)
    source_invoking_para_ids: list[str] = Field(default_factory=list)
    source_invocations: list[CitationSourceInvocation] = Field(default_factory=list)
    scl_coverage: Literal["covered", "not_covered", "indeterminate"] = "covered"
    scl_mention_ids: list[str] = Field(default_factory=list)
    discovery_methods: list[str] = Field(default_factory=list)
    resolution_scope: Literal["document", "application", "unresolved"] = "unresolved"
    resolution_candidates: list[dict[str, object]] = Field(default_factory=list)
    target_paragraph_resolutions: list[CitationParagraphResolution] = Field(default_factory=list)
    paragraph_resolution_status: Literal[
        "not_requested", "resolved", "partial", "ambiguous", "missing", "unavailable"
    ] = "not_requested"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def resolved(self) -> bool:
        return self.target_node_id is not None


class CitationOccurrenceReport(BaseModel):
    """Coverage and diagnostics summary for deterministic occurrence finding."""

    schema_version: Literal["citation-occurrence-report/v1", "citation-occurrence-report/v2"] = (
        "citation-occurrence-report/v2"
    )
    documents: int = 0
    scl_mentions: int = 0
    occurrences: int = 0
    located_mentions: int = 0
    unlocated_mentions: int = 0
    ambiguous_hits: int = 0
    unmatched_candidates: int = 0
    missing_html: int = 0
    methods: dict[str, int] = Field(default_factory=dict)
    text_discovered_mentions: int = 0
    text_only_occurrences: int = 0
    scl_covered_occurrences: int = 0
    components: dict[str, int] = Field(default_factory=dict)
    sections: dict[str, int] = Field(default_factory=dict)
    pinpoint_occurrences: int = 0
    paragraph_resolved_occurrences: int = 0
    paragraph_partial_occurrences: int = 0
    paragraph_ambiguous_occurrences: int = 0
    paragraph_missing_occurrences: int = 0
    paragraph_unavailable_occurrences: int = 0
    target_html_missing: int = 0


class CitationOccurrenceResult(BaseModel):
    """Occurrence rows plus an auditable report and rejected-hit diagnostics."""

    occurrences: list[CitationOccurrence] = Field(default_factory=list)
    report: CitationOccurrenceReport = Field(default_factory=CitationOccurrenceReport)
    diagnostics: list[dict[str, object]] = Field(default_factory=list)
    mentions: list[CitationMention] = Field(default_factory=list)
    inclusive_edges: list[dict[str, object]] = Field(default_factory=list)
    paragraph_edges: list[dict[str, object]] = Field(default_factory=list)


class CitationDiscoveryResult(BaseModel):
    """Strong text-derived mentions plus rejected, typed candidates."""

    mentions: list[CitationMention] = Field(default_factory=list)
    preliminary_occurrences: list[CitationOccurrence] = Field(default_factory=list)
    rejected_candidates: list[dict[str, object]] = Field(default_factory=list)
    diagnostics: list[dict[str, object]] = Field(default_factory=list)


class HistoricalCatalogEntry(BaseModel):
    reporter_key: str
    title: str | None = None
    normalized_title: str = ""
    appnos: list[str] = Field(default_factory=list)
    date: dt.date | None = None
    document_kind: str = "unknown"
    target_ecli: str | None = None
    target_itemid: str | None = None


class HistoricalCitationCatalog(BaseModel):
    schema_version: Literal["historical-citation-catalog/v1"] = "historical-citation-catalog/v1"
    source_url: str
    coverage_date: str | None = None
    source_sha256: str | None = None
    metadata_source_url: str = "https://hudoc.echr.coe.int/app/query/results"
    metadata_coverage_from: str | None = None
    metadata_coverage_to: str | None = None
    metadata_retrieved_at: str | None = None
    content_sha256: str
    entries: list[HistoricalCatalogEntry] = Field(default_factory=list)
