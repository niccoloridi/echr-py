"""Generic structured-extractor protocol and registry.

Main deliberately ships no domain study targets. Applications may register
extractor factories at runtime, while versioned study specifications are the
preferred public interface.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .base import ExtractionRecord, Extractor, PreparedRequest, Provenance

# Runtime registry for third-party targets. Core ships no legal-study prompts.
EXTRACTORS: dict[str, Callable[..., Extractor]] = {}


def register_extractor(name: str, factory: Callable[..., Extractor]) -> None:
    """Register an application-owned extractor factory."""
    if not name or name in EXTRACTORS:
        raise ValueError(f"Extractor name is empty or already registered: {name!r}")
    EXTRACTORS[name] = factory


def get_extractor(name: str, **kwargs: Any) -> Extractor:
    """Instantiate a registered extractor by name."""
    try:
        factory = EXTRACTORS[name]
    except KeyError:
        raise ValueError(f"Unknown extractor {name!r}: available {sorted(EXTRACTORS)}") from None
    return factory(**kwargs)


__all__ = [
    "Extractor",
    "ExtractionRecord",
    "Provenance",
    "PreparedRequest",
    "EXTRACTORS",
    "get_extractor",
    "register_extractor",
]
