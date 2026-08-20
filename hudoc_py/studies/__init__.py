"""Bounded, reproducible research studies over HUDOC corpora."""

from .hooks import StudyHook, load_study_hook
from .jobs import StudyJob, StudyJobManager
from .models import (
    BatchStageManifest,
    BudgetSpec,
    EvidenceRef,
    OutputSpec,
    ProviderCapabilities,
    RetrievalSpec,
    SourceSpec,
    StageSpec,
    StudyRecord,
    StudyRun,
    StudySpec,
)
from .runner import (
    BudgetExceededError,
    StudyRunner,
    UnknownPricingError,
    acquire_source,
    load_study_run,
    unitize,
    verify_evidence,
)
from .spec import dump_resolved_spec, load_study_spec, study_spec_hash
from .taxonomies import (
    citation_taxonomy_schema,
    list_citation_taxonomies,
    write_citation_use_study,
)

__all__ = [
    "BatchStageManifest",
    "BudgetSpec",
    "EvidenceRef",
    "OutputSpec",
    "ProviderCapabilities",
    "RetrievalSpec",
    "SourceSpec",
    "StageSpec",
    "StudyHook",
    "StudyRecord",
    "StudyRun",
    "StudySpec",
    "BudgetExceededError",
    "UnknownPricingError",
    "StudyJob",
    "StudyJobManager",
    "StudyRunner",
    "acquire_source",
    "dump_resolved_spec",
    "load_study_hook",
    "load_study_run",
    "load_study_spec",
    "study_spec_hash",
    "unitize",
    "verify_evidence",
    "citation_taxonomy_schema",
    "list_citation_taxonomies",
    "write_citation_use_study",
]
