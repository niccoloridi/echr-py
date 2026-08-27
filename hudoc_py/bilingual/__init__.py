"""Bilingual (ENG/FRE) corpus correctness for HUDOC.

HUDOC publishes many English "placeholder" cases whose downloadable body text
exists only in the French-language sibling document. This subpackage
reconciles the two language streams by ECLI, rescues missing French siblings
by application number, and threads the resulting provenance through the
:class:`~hudoc_py.models.case.Case` model.
"""

from __future__ import annotations

from .ecli import (
    DOCNAME_SIMILARITY_THRESHOLD,
    ClusterKind,
    classify_ecli_cluster,
    docname_similarity,
    normalize_appnos,
    normalize_docname,
    normalize_ecli,
)
from .reconcile import (
    ReconcileInvariantError,
    ReconcileResult,
    ReconcileStats,
    reconcile,
)
from .rescue import (
    SIBLING_DOCTYPES,
    RescueRecord,
    RescueStats,
    apply_rescue_mapping,
    export_rescue_csv,
    find_french_sibling,
    rescue_candidates,
    rescue_french,
    sibling_conflicts,
)


def __getattr__(name: str):
    # Lazy: corpus pulls in aiohttp/pandas; keep them off the import path for
    # callers that only need reconcile/ecli.
    if name in ("build_corpus", "CorpusReport", "load_cases", "load_selection", "SelectionEntry"):
        from . import corpus

        return getattr(corpus, name)
    if name in (
        "CorpusManifest",
        "CorpusPackageReport",
        "CorpusValidationReport",
        "generate_corpus_manifest",
        "package_corpus",
        "validate_corpus",
    ):
        from . import bundle

        return getattr(bundle, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ClusterKind",
    "DOCNAME_SIMILARITY_THRESHOLD",
    "classify_ecli_cluster",
    "docname_similarity",
    "normalize_appnos",
    "normalize_docname",
    "normalize_ecli",
    "reconcile",
    "ReconcileResult",
    "ReconcileStats",
    "ReconcileInvariantError",
    "rescue_french",
    "sibling_conflicts",
    "rescue_candidates",
    "find_french_sibling",
    "apply_rescue_mapping",
    "export_rescue_csv",
    "RescueRecord",
    "RescueStats",
    "SIBLING_DOCTYPES",
    "build_corpus",
    "CorpusReport",
    "load_cases",
    "load_selection",
    "SelectionEntry",
    "CorpusManifest",
    "CorpusPackageReport",
    "CorpusValidationReport",
    "generate_corpus_manifest",
    "package_corpus",
    "validate_corpus",
]
