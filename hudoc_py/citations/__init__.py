"""Authoritative SCL parsing, exact-document resolution, review, and graphs.

Measurement workflow:

1. :func:`parse_scl_mentions` preserves every printed bibliographic signal.
2. :func:`resolve_citations` uses explicit identifiers, the Court's exact-
   reference authority, corroborating HUDOC metadata, and reviewed overrides.
3. :class:`CitationGraph` consumes the resolution completeness contract before
   producing measurement-grade metrics or visualization artifacts.

The legacy :meth:`CitationGraph.resolve` method remains a strict offline
convenience, but has no authority provenance and therefore cannot silently
produce measurement-grade metrics. ``extractedappno`` is deprecated as a
resolution input because it contains unrelated body mentions.
"""

from .artifacts import (
    load_resolution_artifacts,
    load_resolutions,
    write_occurrence_artifacts,
    write_resolution_artifacts,
    write_review,
)
from .authority import import_authority_pdf, load_authority, merge_authorities, write_authority_csv
from .benchmarks import (
    align_benchmark_annotations,
    benchmark_citation_annotations,
    compare_citation_exports,
    fetch_benchmark,
    import_benchmark,
    import_mumford,
    load_benchmark_rows,
    load_competitor_citations,
    parse_mumford_xmi,
    project_mumford_occurrences,
)
from .catalog import (
    build_historical_catalog,
    load_historical_catalog,
    verify_historical_catalog,
    write_historical_catalog,
)
from .extractor import (
    APPNO_REGEX,
    external_source_authority,
    extract_citations,
    match_external_source,
    parse_external_sources,
    parse_scl,
)
from .graph import CitationGraph
from .models import (
    CitationAuthority,
    CitationAuthorityEntry,
    CitationAuthoritySource,
    CitationCandidate,
    CitationDiscoveryResult,
    CitationMention,
    CitationOccurrence,
    CitationOccurrenceReport,
    CitationOccurrenceResult,
    CitationOverride,
    CitationParagraphResolution,
    CitationResolution,
    CitationResolutionReport,
    CitationResolutionResult,
    CitationSourceInvocation,
    HistoricalCatalogEntry,
    HistoricalCitationCatalog,
    IncompleteCitationResolutionError,
    ReporterLocator,
)
from .occurrences import discover_citation_mentions, extract_citation_occurrences
from .paragraphs import resolve_occurrence_paragraphs
from .reporter import parse_reporter, parse_scl_mentions
from .resolver import TargetCatalog, canonical_node_id, load_overrides, resolve_citations

__all__ = [
    "APPNO_REGEX",
    "extract_citations",
    "parse_scl",
    "parse_external_sources",
    "match_external_source",
    "external_source_authority",
    "CitationGraph",
    "CitationAuthority",
    "CitationAuthorityEntry",
    "CitationAuthoritySource",
    "CitationCandidate",
    "CitationDiscoveryResult",
    "HistoricalCitationCatalog",
    "HistoricalCatalogEntry",
    "CitationMention",
    "CitationOccurrence",
    "CitationParagraphResolution",
    "CitationOccurrenceReport",
    "CitationOccurrenceResult",
    "CitationOverride",
    "CitationResolution",
    "CitationResolutionReport",
    "CitationResolutionResult",
    "CitationSourceInvocation",
    "IncompleteCitationResolutionError",
    "ReporterLocator",
    "TargetCatalog",
    "canonical_node_id",
    "import_authority_pdf",
    "load_authority",
    "merge_authorities",
    "load_overrides",
    "load_resolution_artifacts",
    "load_resolutions",
    "parse_reporter",
    "parse_scl_mentions",
    "resolve_citations",
    "extract_citation_occurrences",
    "discover_citation_mentions",
    "resolve_occurrence_paragraphs",
    "write_occurrence_artifacts",
    "write_resolution_artifacts",
    "write_review",
    "write_authority_csv",
    "build_historical_catalog",
    "align_benchmark_annotations",
    "benchmark_citation_annotations",
    "compare_citation_exports",
    "fetch_benchmark",
    "import_benchmark",
    "import_mumford",
    "load_benchmark_rows",
    "load_competitor_citations",
    "parse_mumford_xmi",
    "project_mumford_occurrences",
    "load_historical_catalog",
    "verify_historical_catalog",
    "write_historical_catalog",
]
