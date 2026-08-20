"""Load, resolve and hash declarative study specifications."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .models import StudySpec


def _yaml() -> Any:
    try:
        import yaml
    except ImportError as exc:
        raise ImportError("Study YAML support requires echr-py[research-agent]") from exc
    return yaml


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def load_study_spec(path: str | Path) -> StudySpec:
    source = Path(path).resolve()
    raw = _yaml().safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("study file must contain a YAML object")
    spec = StudySpec.model_validate(raw)
    base = source.parent
    update: dict[str, Any] = {}
    if spec.schema_path:
        schema_path = (base / spec.schema_path).resolve()
        update["response_schema"] = json.loads(schema_path.read_text(encoding="utf-8"))
        update["schema_path"] = str(schema_path)
    resolved_stages = []
    for stage in spec.stages:
        stage_update: dict[str, Any] = {}
        if stage.prompt_path:
            prompt_path = (base / stage.prompt_path).resolve()
            stage_update.update(
                prompt=prompt_path.read_text(encoding="utf-8"), prompt_path=str(prompt_path)
            )
        if stage.schema_path:
            schema_path = (base / stage.schema_path).resolve()
            stage_update.update(
                response_schema=json.loads(schema_path.read_text(encoding="utf-8")),
                schema_path=str(schema_path),
            )
        resolved_stages.append(stage.model_copy(update=stage_update))
    update["stages"] = resolved_stages
    if spec.source.path:
        source_path = (base / spec.source.path).resolve()
        update["source"] = spec.source.model_copy(update={"path": str(source_path)})
    if spec.retrieval:
        retrieval_update = {}
        for key in ("database", "embeddings"):
            value = getattr(spec.retrieval, key)
            if value:
                retrieval_update[key] = str((base / value).resolve())
        update["retrieval"] = spec.retrieval.model_copy(update=retrieval_update)
    return spec.model_copy(update=update)


def dump_resolved_spec(spec: StudySpec, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _yaml().safe_dump(spec.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def study_spec_hash(spec: StudySpec) -> str:
    return sha256_value(spec.model_dump(mode="json"))


__all__ = [
    "canonical_json",
    "dump_resolved_spec",
    "load_study_spec",
    "sha256_value",
    "study_spec_hash",
]
