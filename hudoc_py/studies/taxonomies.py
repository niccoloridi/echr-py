"""Optional citation-use study profiles; never used by deterministic discovery."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import BudgetSpec, OutputSpec, SourceSpec, StageSpec, StudySpec
from .spec import dump_resolved_spec

_SCHEMA_ROOT = Path(__file__).with_name("schemas")

_CITATION_USE_PROMPT = """\
Classify how this single ECtHR citation occurrence is used. This is an optional
research annotation, not part of citation discovery or target resolution.

The occurrence record (including deterministic source component, section,
source paragraph, cited authority, and any target paragraph pinpoints) is:
{{citation_json}}

The exact citing paragraph is:
---
{{text}}
---

Use the requested taxonomy conservatively. Distinguish the Court's own voice
from party submissions, third-party material, quoted domestic reasoning, and
an individual opinion. Do not infer a negative treatment merely because a
party criticises an authority. Do not treat a citation list as substantive
engagement. Return a short exact quotation from the citing paragraph for every
substantive label; uncertainty must be represented by the schema's uncertainty
or review fields rather than invented certainty.
"""


def list_citation_taxonomies() -> list[str]:
    """Names of editable starter profiles shipped with the package."""
    return sorted(
        path.stem.removeprefix("citation-use-") for path in _SCHEMA_ROOT.glob("citation-use-*.json")
    )


def citation_taxonomy_schema(name: str) -> dict[str, Any]:
    """Load a copy of one packaged citation-use JSON Schema."""
    path = _SCHEMA_ROOT / f"citation-use-{name}.json"
    if not path.exists():
        raise ValueError(
            f"unknown citation taxonomy {name!r}; available: {list_citation_taxonomies()}"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def write_citation_use_study(
    output: str | Path,
    *,
    source: str | Path,
    provider: str,
    model: str,
    taxonomy: str = "multiaxial",
    schema_path: str | Path | None = None,
) -> Path:
    """Create an explicit, user-owned YAML study for occurrence labelling."""
    if schema_path:
        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
        taxonomy_id = f"custom:{Path(schema_path).name}"
    else:
        schema = citation_taxonomy_schema(taxonomy)
        taxonomy_id = f"hudoc-citation-use-{taxonomy}/v1"
    evidence_fields = [
        f"/{name}"
        for name in schema.get("required", [])
        if name not in {"confidence", "needs_review", "taxonomy_notes"}
    ]
    spec = StudySpec(
        id="citation-use",
        version="1",
        description="Optional evidence-grounded labelling of citation occurrences.",
        source=SourceSpec(kind="local", path=str(Path(source).resolve())),
        unit="citation_occurrence",
        stages=[
            StageSpec(
                id="label-use",
                kind="extract",
                provider=provider,
                model=model,
                prompt=_CITATION_USE_PROMPT,
                response_schema=schema,
                required_evidence=True,
                evidence_fields=evidence_fields,
                temperature=0.0,
            )
        ],
        budget=BudgetSpec(),
        output=OutputSpec(parquet=True, jsonl=True, report="markdown"),
        metadata={
            "optional": True,
            "taxonomy_id": taxonomy_id,
            "deterministic_citations_unchanged": True,
        },
    )
    return dump_resolved_spec(spec, output)


__all__ = [
    "citation_taxonomy_schema",
    "list_citation_taxonomies",
    "write_citation_use_study",
]
