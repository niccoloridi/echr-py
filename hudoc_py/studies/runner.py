"""Bounded, checkpointed study execution."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from ..text import segment_full
from ..utils.jsonl import append_jsonl, iter_jsonl
from .hooks import StudyHook, load_study_hook
from .models import BatchStageManifest, EvidenceRef, StageSpec, StudyRecord, StudyRun, StudySpec
from .spec import canonical_json, dump_resolved_spec, sha256_value, study_spec_hash

MODEL_STAGE_KINDS = {"triage", "extract", "synthesize"}
TERMINAL_RECORD_STATES = {"ok", "invalid", "skipped"}


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_local(path: str) -> list[dict[str, Any]]:
    source = Path(path)
    if source.is_dir():
        from ..local.registry import find_table

        found = find_table("texts", source)
        if found is None:
            raise FileNotFoundError(f"No texts table under {source}")
        source = found[0]
    if source.suffix == ".parquet":
        import pandas as pd

        return pd.read_parquet(source).to_dict("records")
    if source.suffix in {".jsonl", ".ndjson"}:
        return list(iter_jsonl(source))
    value = json.loads(source.read_text(encoding="utf-8"))
    if isinstance(value, list):
        return [dict(row) for row in value]
    if isinstance(value, dict):
        return [value]
    raise ValueError(f"Unsupported local study input: {source}")


def acquire_source(spec: StudySpec) -> list[dict[str, Any]]:
    """Acquire source records without invoking a model."""
    if spec.source.kind == "local":
        return _read_local(str(spec.source.path))
    if spec.source.kind == "hudoc":
        from .._sync import smart_fetch

        filters = dict(spec.source.filters)
        top = int(spec.source.limit or filters.pop("top", 100))
        cases = smart_fetch(
            query=spec.source.query,
            top=top,
            with_text=True,
            rich_sections=True,
            **filters,
        )
        return [case.model_dump(mode="json") for case in cases]
    raise ValueError(f"Unsupported study source: {spec.source.kind}")


def _record_id(record: dict[str, Any]) -> str:
    for key in (
        "occurrence_id",
        "itemid",
        "source_itemid",
        "execidentifier",
        "id",
        "document_id",
    ):
        if record.get(key):
            return str(record[key])
    return sha256_value(record)[:20]


def _record_text(record: dict[str, Any]) -> str:
    return str(
        record.get("text")
        or record.get("source_context")
        or record.get("context_text")
        or record.get("text_md")
        or record.get("body")
        or ""
    )


def unitize(spec: StudySpec, records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Create stable source-addressed units for a study."""
    units: list[dict[str, Any]] = []
    for record in records:
        itemid = _record_id(record)
        text = _record_text(record)
        if spec.unit == "document":
            units.append({"source_id": itemid, "itemid": itemid, "text": text, "metadata": record})
            continue
        if spec.unit == "citation_occurrence":
            occurrence_id = str(record.get("occurrence_id") or itemid)
            units.append(
                {
                    "source_id": occurrence_id,
                    "itemid": record.get("source_itemid"),
                    "paragraph_id": record.get("source_para_id"),
                    "section": record.get("source_section"),
                    "source_component": record.get("source_component"),
                    "opinion_id": record.get("source_opinion_id"),
                    "target_itemid": record.get("target_itemid"),
                    "target_paragraphs": record.get("target_paragraphs", []),
                    "text": text,
                    "metadata": record,
                }
            )
            continue
        sections = segment_full(text, document_id=itemid)
        spine = sections.spine
        if spec.unit == "section":
            blocks = [
                block for block in (spine.blocks if spine else []) if block.section == spec.section
            ]
            section_text = "\n\n".join(block.text for block in blocks)
            units.append(
                {
                    "source_id": f"{itemid}:section:{spec.section}",
                    "itemid": itemid,
                    "section": spec.section,
                    "text": section_text,
                    "metadata": record,
                }
            )
        elif spec.unit == "paragraph":
            for block in spine.blocks if spine else []:
                if block.type == "heading" or not block.text.strip():
                    continue
                para_id = block.para_id or block.block_id
                units.append(
                    {
                        "source_id": f"{itemid}:paragraph:{para_id}",
                        "itemid": itemid,
                        "paragraph_id": para_id,
                        "section": block.section,
                        "opinion_id": block.opinion_id,
                        "text": block.text,
                        "metadata": record,
                    }
                )
        else:  # opinion
            grouped: dict[str, list[Any]] = {}
            for block in spine.blocks if spine else []:
                if block.opinion_id:
                    grouped.setdefault(block.opinion_id, []).append(block)
            for opinion_id, blocks in grouped.items():
                units.append(
                    {
                        "source_id": opinion_id,
                        "itemid": itemid,
                        "opinion_id": opinion_id,
                        "text": "\n\n".join(block.text for block in blocks),
                        "metadata": record,
                    }
                )
    return units


def _render_prompt(
    template: str,
    unit: dict[str, Any],
    context: str = "",
    *,
    previous_output: dict[str, Any] | None = None,
    stage_outputs: dict[str, dict[str, Any]] | None = None,
) -> str:
    replacements = {
        "{{text}}": str(unit.get("text", "")),
        "{{context}}": context,
        "{{source_id}}": str(unit.get("source_id", "")),
        "{{metadata_json}}": canonical_json(unit.get("metadata", {})),
        "{{citation_json}}": canonical_json(unit.get("metadata", {})),
        "{{previous_output_json}}": canonical_json(previous_output or {}),
        "{{stage_outputs_json}}": canonical_json(stage_outputs or {}),
    }
    rendered = template
    for marker, value in replacements.items():
        rendered = rendered.replace(marker, value)
    unknown = [part.split("}}", 1)[0] for part in rendered.split("{{")[1:] if "}}" in part]
    if unknown:
        raise ValueError(f"unknown prompt placeholders: {unknown}")
    return rendered


def _evidence_schema(data_schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "data": data_schema,
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "field_path": {"type": "string"},
                        "paragraph_id": {"type": ["string", "null"]},
                        "quote": {"type": "string"},
                        "start": {"type": ["integer", "null"]},
                        "end": {"type": ["integer", "null"]},
                    },
                    "required": ["field_path", "quote"],
                },
            },
        },
        "required": ["data", "evidence"],
    }


def _validate_schema(data: dict[str, Any], schema: dict[str, Any] | None) -> None:
    if schema is None:
        return
    try:
        import jsonschema
    except ImportError as exc:
        raise ImportError("Schema validation requires echr-py[research-agent]") from exc
    jsonschema.validate(data, schema)


def verify_evidence(
    evidence: Iterable[EvidenceRef], unit: dict[str, Any]
) -> tuple[list[EvidenceRef], list[str]]:
    text = str(unit.get("text", ""))
    valid: list[EvidenceRef] = []
    errors: list[str] = []
    for index, ref in enumerate(evidence):
        if not ref.quote:
            errors.append(f"evidence[{index}] quote is not an exact source substring")
            continue
        if ref.start is not None or ref.end is not None:
            if ref.start is None or ref.end is None:
                errors.append(f"evidence[{index}] must provide both start and end offsets")
                continue
            start = ref.start
            if start < 0 or ref.end < start or text[start : ref.end] != ref.quote:
                errors.append(f"evidence[{index}] offsets do not match the exact source slice")
                continue
        else:
            starts: list[int] = []
            cursor = 0
            while (found := text.find(ref.quote, cursor)) >= 0:
                starts.append(found)
                cursor = found + max(1, len(ref.quote))
            if not starts:
                errors.append(f"evidence[{index}] quote is not an exact source substring")
                continue
            if len(starts) > 1:
                errors.append(f"evidence[{index}] quote is ambiguous without source offsets")
                continue
            start = starts[0]
        valid.append(
            ref.model_copy(
                update={
                    "field_path": (
                        ref.field_path
                        if not ref.field_path or ref.field_path.startswith("/")
                        else f"/{ref.field_path}"
                    ),
                    "itemid": ref.itemid or unit.get("itemid"),
                    "paragraph_id": ref.paragraph_id or unit.get("paragraph_id"),
                    "section": ref.section or unit.get("section"),
                    "source_component": ref.source_component or unit.get("source_component"),
                    "opinion_id": ref.opinion_id or unit.get("opinion_id"),
                    "start": start,
                    "end": start + len(ref.quote),
                }
            )
        )
    return valid, errors


class BudgetExceededError(RuntimeError):
    pass


class UnknownPricingError(ValueError):
    pass


class StudyRunner:
    """Execute the finite stage list declared by a :class:`StudySpec`."""

    def __init__(
        self,
        spec: StudySpec,
        output_dir: str | Path,
        *,
        provider_factory: Callable[..., Any] | None = None,
        installed_only_hooks: bool = False,
        cancel_check: Callable[[], bool] | None = None,
        pricing: dict[str, dict[str, float]] | None = None,
        batch_adapter_factory: Callable[..., Any] | None = None,
    ):
        self.spec = spec
        self.output_dir = Path(output_dir).resolve()
        self.provider_factory = provider_factory
        self.cancel_check = cancel_check or (lambda: False)
        self.pricing = pricing or {}
        self.batch_adapter_factory = batch_adapter_factory
        self.hook: StudyHook | None = (
            load_study_hook(spec.hook, installed_only=installed_only_hooks) if spec.hook else None
        )
        self.spec_hash = study_spec_hash(spec)

    def _provider(self, stage: StageSpec) -> Any:
        if self.provider_factory:
            return self.provider_factory(stage.provider, model=stage.model)
        from ..llm import get_provider

        return get_provider(stage.provider, model=stage.model)

    def _batch_adapter(self, stage: StageSpec, provider: Any) -> Any:
        if self.batch_adapter_factory:
            return self.batch_adapter_factory(stage.provider, provider=provider)
        from .batch import batch_adapter

        return batch_adapter(str(stage.provider), provider._get_client())

    def _source_hash(self, records: list[dict[str, Any]]) -> str:
        if self.spec.source.kind == "local" and self.spec.source.path:
            path = Path(self.spec.source.path)
            if path.is_file():
                return _file_sha256(path)
        return sha256_value(records)

    def prepare(self) -> tuple[list[dict[str, Any]], str]:
        records = acquire_source(self.spec)
        units = unitize(self.spec, records)
        if self.hook:
            units = [value for unit in units if (value := self.hook.prepare_unit(unit)) is not None]
        return units, self._source_hash(records)

    def plan(self) -> dict[str, Any]:
        units, source_hash = self.prepare()
        model_stages = [stage for stage in self.spec.stages if self._is_model_stage(stage)]
        requests = len(units) * len(model_stages)
        estimated_input_tokens = sum(len(str(unit.get("text", ""))) // 4 for unit in units) * len(
            model_stages
        )
        return {
            "schema_version": "hudoc-study-plan/v1",
            "study_id": self.spec.id,
            "study_version": self.spec.version,
            "spec_sha256": self.spec_hash,
            "source_sha256": source_hash,
            "units": len(units),
            "stages": [stage.id for stage in self.spec.stages],
            "model_requests": requests,
            "estimated_input_tokens": estimated_input_tokens,
            "models": [
                {"stage": stage.id, "provider": stage.provider, "model": stage.model}
                for stage in model_stages
            ],
        }

    @staticmethod
    def _is_model_stage(stage: StageSpec) -> bool:
        return stage.kind in MODEL_STAGE_KINDS or (
            stage.kind in {"rerank", "verify"} and bool(stage.provider)
        )

    def _pricing_profile(self, stage: StageSpec) -> dict[str, float] | None:
        profile = self.pricing.get(f"{stage.provider}:{stage.model}") or self.pricing.get(
            str(stage.model)
        )
        if profile:
            return {key: float(value) for key, value in profile.items()}
        from ..config import MODEL_PRICING

        builtin = MODEL_PRICING.get(str(stage.model))
        if builtin is None:
            return None
        return {
            "input_per_m": float(builtin.input_per_m),
            "output_per_m": float(builtin.output_per_m),
            "batch_discount": float(builtin.batch_discount),
        }

    def _estimated_cost(self, stage: StageSpec, input_tokens: int, output_tokens: int) -> float:
        profile = self._pricing_profile(stage)
        if profile is None:
            if self.spec.budget.max_usd is not None:
                raise UnknownPricingError(
                    f"no pricing profile for {stage.provider}:{stage.model}; "
                    "a dollar-capped run cannot start"
                )
            return 0.0
        cost = input_tokens / 1_000_000 * profile.get("input_per_m", 0)
        cost += output_tokens / 1_000_000 * profile.get("output_per_m", 0)
        if stage.batch:
            cost *= profile.get("batch_discount", 1.0)
        return cost

    def _check_budget(
        self,
        run: StudyRun,
        *,
        next_input: int = 0,
        next_output: int = 0,
        next_usd: float = 0.0,
        next_requests: int = 1,
    ) -> None:
        budget = self.spec.budget
        if budget.max_requests is not None and run.requests + next_requests > budget.max_requests:
            raise BudgetExceededError("maximum request budget reached")
        if (
            budget.max_input_tokens is not None
            and run.input_tokens + next_input > budget.max_input_tokens
        ):
            raise BudgetExceededError("maximum input-token budget reached")
        if (
            budget.max_output_tokens is not None
            and run.output_tokens + next_output > budget.max_output_tokens
        ):
            raise BudgetExceededError("maximum output-token budget reached")
        if budget.max_usd is not None and run.cost_usd + next_usd > budget.max_usd:
            raise BudgetExceededError("maximum dollar budget reached")

    def _existing(self, path: Path) -> set[tuple[str, str]]:
        if not path.exists():
            return set()
        return {
            (str(row.get("task_id")), str(row.get("stage_id")))
            for row in iter_jsonl(path)
            if row.get("status") in TERMINAL_RECORD_STATES
        }

    def _task_id(
        self, unit: dict[str, Any], stage: StageSpec, schema: dict[str, Any] | None
    ) -> str:
        return sha256_value(
            {
                "study": self.spec.id,
                "version": self.spec.version,
                "source": unit["source_id"],
                "source_sha256": hashlib.sha256(str(unit.get("text", "")).encode()).hexdigest(),
                "stage": stage.id,
                "prompt": stage.prompt,
                "schema": schema,
            }
        )

    @staticmethod
    def _latest_records(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
        latest: dict[tuple[str, str], dict[str, Any]] = {}
        if path.exists():
            for row in iter_jsonl(path):
                latest[(str(row.get("task_id")), str(row.get("stage_id")))] = row
        return latest

    def _refresh_run_counts(self, run: StudyRun, records_path: Path, raw_path: Path) -> None:
        latest = self._latest_records(records_path)
        run.records = len(latest)
        run.errors = sum(row.get("status") == "error" for row in latest.values())
        run.invalid = sum(row.get("status") == "invalid" for row in latest.values())
        raw = list(iter_jsonl(raw_path)) if raw_path.exists() else []
        run.requests = len(raw)
        run.input_tokens = sum(int(row.get("input_tokens") or 0) for row in raw)
        run.output_tokens = sum(int(row.get("output_tokens") or 0) for row in raw)
        run.cost_usd = round(sum(float(row.get("cost_usd") or 0) for row in raw), 6)

    def _record_from_result(
        self,
        *,
        result: Any,
        unit: dict[str, Any],
        stage: StageSpec,
        schema: dict[str, Any],
        task_id: str,
        attempt_id: str,
    ) -> StudyRecord:
        if not result.ok:
            return StudyRecord(
                task_id=task_id,
                attempt_id=attempt_id,
                study_id=self.spec.id,
                study_version=self.spec.version,
                source_id=unit["source_id"],
                stage_id=stage.id,
                status="error",
                error=result.error,
                meta=result.meta(),
            )
        payload = result.data.get("data", {}) if stage.required_evidence else result.data
        evidence = [
            EvidenceRef.model_validate(value)
            for value in (result.data.get("evidence", []) if stage.required_evidence else [])
        ]
        warnings: list[str] = []
        try:
            _validate_schema(payload, schema)
            if self.hook:
                payload, hook_warnings = self.hook.validate_record(payload, unit)
                warnings.extend(hook_warnings)
            verified, evidence_errors = verify_evidence(evidence, unit)
            warnings.extend(evidence_errors)
            present_paths = {ref.field_path for ref in verified}
            missing_paths = [path for path in stage.evidence_fields if path not in present_paths]
            warnings.extend(f"missing verified evidence for {path}" for path in missing_paths)
            invalid = bool(evidence_errors or missing_paths) or (
                stage.required_evidence and not verified
            )
            return StudyRecord(
                task_id=task_id,
                attempt_id=attempt_id,
                study_id=self.spec.id,
                study_version=self.spec.version,
                source_id=unit["source_id"],
                stage_id=stage.id,
                status="invalid" if invalid else "ok",
                data=payload,
                evidence=verified,
                warnings=warnings,
                meta=result.meta(),
            )
        except Exception as exc:
            return StudyRecord(
                task_id=task_id,
                attempt_id=attempt_id,
                study_id=self.spec.id,
                study_version=self.spec.version,
                source_id=unit["source_id"],
                stage_id=stage.id,
                status="invalid",
                data=payload,
                warnings=warnings,
                error=str(exc),
                meta=result.meta(),
            )

    def _run_batch_stage(
        self,
        *,
        run: StudyRun,
        stage: StageSpec,
        units: list[dict[str, Any]],
        contexts: dict[str, str],
        outputs: dict[str, dict[str, dict[str, Any]]],
        records_path: Path,
        requests_path: Path,
        raw_path: Path,
        diagnostics_path: Path,
        wait: bool,
        poll_interval: float,
        max_polls: int | None,
    ) -> str:
        schema = stage.response_schema or self.spec.response_schema or {"type": "object"}
        request_schema = _evidence_schema(schema) if stage.required_evidence else schema
        provider = self._provider(stage)
        adapter = self._batch_adapter(stage, provider)
        batch_dir = self.output_dir / "batches" / stage.id
        batch_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = batch_dir / "manifest.json"
        request_file = batch_dir / "requests.jsonl"
        result_file = batch_dir / "results.jsonl"
        latest = self._latest_records(records_path)
        unit_by_task = {self._task_id(unit, stage, schema): unit for unit in units}
        submitted_now = False
        if manifest_path.exists():
            manifest = BatchStageManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
            if manifest.requests_sha256 != _file_sha256(Path(manifest.requests_path)):
                raise ValueError(f"batch request checksum changed for stage {stage.id}")
        else:
            rows: list[dict[str, Any]] = []
            reserved_input = reserved_output = reserved_requests = 0
            reserved_cost = 0.0
            prior_requests = list(iter_jsonl(requests_path)) if requests_path.exists() else []
            for task_id, unit in unit_by_task.items():
                current = latest.get((task_id, stage.id))
                if current and current.get("status") in TERMINAL_RECORD_STATES:
                    continue
                unit_outputs = outputs.get(unit["source_id"], {})
                previous = next(reversed(unit_outputs.values())) if unit_outputs else {}
                prompt = _render_prompt(
                    stage.prompt or "",
                    unit,
                    contexts.get(unit["source_id"], ""),
                    previous_output=previous,
                    stage_outputs=unit_outputs,
                )
                estimated_input = provider.count_tokens(prompt, model=stage.model)
                estimated_cost = self._estimated_cost(
                    stage, estimated_input, stage.max_output_tokens
                )
                self._check_budget(
                    run,
                    next_input=reserved_input + estimated_input,
                    next_output=reserved_output + stage.max_output_tokens,
                    next_usd=reserved_cost + estimated_cost,
                    next_requests=reserved_requests + 1,
                )
                ordinal = sum(row.get("task_id") == task_id for row in prior_requests)
                attempt_id = sha256_value({
                    "task": task_id,
                    "provider": stage.provider,
                    "model": stage.model,
                    "temperature": stage.temperature,
                    "max_output_tokens": stage.max_output_tokens,
                    "options": stage.options,
                    "batch": True,
                    "ordinal": ordinal,
                })
                row = {
                    "task_id": task_id,
                    "attempt_id": attempt_id,
                    "stage_id": stage.id,
                    "source_id": unit["source_id"],
                    "provider": stage.provider,
                    "model": stage.model,
                    "temperature": stage.temperature,
                    "max_output_tokens": stage.max_output_tokens,
                    "thinking_budget": int(stage.options.get("thinking_budget", 0)),
                    "system_instruction": str(stage.options.get("system_instruction") or ""),
                    "prompt": prompt,
                    "schema": request_schema,
                    "estimated_input_tokens": estimated_input,
                    "estimated_cost_usd": estimated_cost,
                }
                append_jsonl(requests_path, row)
                rows.append(row)
                reserved_input += estimated_input
                reserved_output += stage.max_output_tokens
                reserved_cost += estimated_cost
                reserved_requests += 1
            if not rows:
                return "complete"
            adapter.prepare(rows, request_file)
            manifest = adapter.submit(
                path=request_file,
                stage_id=stage.id,
                model=str(stage.model),
                task_ids=[row["task_id"] for row in rows],
            )
            submitted_now = True
            manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        polls = 0
        while manifest.state not in {"succeeded", "failed", "cancelled"}:
            if self.cancel_check():
                manifest = adapter.cancel(manifest)
                manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
                return "cancelled"
            if submitted_now and manifest.state == "submitted" and not wait and polls == 0:
                return "waiting"
            manifest = adapter.poll(manifest)
            manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
            polls += 1
            if manifest.state in {"succeeded", "failed", "cancelled"}:
                break
            if not wait or (max_polls is not None and polls >= max_polls):
                return "waiting"
            time.sleep(poll_interval)
        if manifest.state == "cancelled":
            return "cancelled"
        if manifest.state == "failed":
            append_jsonl(diagnostics_path, {
                "code": "batch_failed",
                "stage_id": stage.id,
                "job_id": manifest.job_id,
                "diagnostics": manifest.diagnostics,
            })
            return "partial"
        manifest, results = adapter.retrieve(manifest, result_file)
        manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        request_rows = {
            str(row["task_id"]): row
            for row in iter_jsonl(requests_path)
            if row.get("stage_id") == stage.id
        }
        from ..llm import ExtractResult

        for task_id in manifest.task_ids:
            found_unit = unit_by_task.get(task_id)
            request_row = request_rows.get(task_id)
            if found_unit is None or request_row is None:
                append_jsonl(diagnostics_path, {
                    "code": "batch_orphan_result",
                    "stage_id": stage.id,
                    "task_id": task_id,
                })
                continue
            unit = found_unit
            current = self._latest_records(records_path).get((task_id, stage.id))
            if current and current.get("status") in TERMINAL_RECORD_STATES:
                continue
            result = results.get(task_id) or ExtractResult(
                data={},
                model=str(stage.model),
                provider=str(stage.provider),
                error="native batch returned no result for this task",
            )
            estimated_input = int(request_row.get("estimated_input_tokens") or 0)
            estimated_cost = float(request_row.get("estimated_cost_usd") or 0)
            actual_input = result.input_tokens or estimated_input
            actual_cost = result.cost_usd or estimated_cost
            append_jsonl(raw_path, {
                "task_id": task_id,
                "attempt_id": request_row["attempt_id"],
                "stage_id": stage.id,
                "source_id": unit["source_id"],
                "provider": result.provider,
                "model": result.model,
                "batch_job_id": manifest.job_id,
                "input_tokens": actual_input,
                "output_tokens": result.output_tokens,
                "cost_usd": actual_cost,
                "raw_text": result.raw_text,
                "data": result.data,
                "error": result.error,
            })
            record = self._record_from_result(
                result=result,
                unit=unit,
                stage=stage,
                schema=schema,
                task_id=task_id,
                attempt_id=str(request_row["attempt_id"]),
            )
            append_jsonl(records_path, record.model_dump(mode="json"))
            if record.status == "ok":
                outputs.setdefault(unit["source_id"], {})[stage.id] = record.data
        return "complete"

    def run(
        self,
        *,
        resume: bool = True,
        wait: bool = False,
        poll_interval: float = 30.0,
        max_polls: int | None = None,
    ) -> StudyRun:
        if not resume and self.output_dir.exists() and any(self.output_dir.iterdir()):
            raise FileExistsError(
                f"study output is not empty: {self.output_dir}; choose a new directory or resume"
            )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        records_path = self.output_dir / "records.jsonl"
        requests_path = self.output_dir / "requests.jsonl"
        raw_path = self.output_dir / "raw-responses.jsonl"
        diagnostics_path = self.output_dir / "diagnostics.jsonl"
        manifest_path = self.output_dir / "manifest.json"
        stage_path = self.output_dir / "stages.jsonl"
        retrieval_path = self.output_dir / "retrieval-candidates.jsonl"
        units, source_hash = self.prepare()
        if resume and manifest_path.exists():
            run = StudyRun.model_validate_json(manifest_path.read_text(encoding="utf-8"))
            if run.spec_sha256 != self.spec_hash or run.source_sha256 != source_hash:
                raise ValueError("cannot resume: resolved study or source checksum has changed")
            run.schema_version = "hudoc-study-run/v2"
            run.status = "running"
            run.finished_at = None
        else:
            run = StudyRun(
                run_id=sha256_value(
                    {"spec": self.spec_hash, "source": source_hash, "time": _now()}
                )[:24],
                study_id=self.spec.id,
                study_version=self.spec.version,
                status="running",
                output_dir=str(self.output_dir),
                spec_sha256=self.spec_hash,
                source_sha256=source_hash,
                started_at=_now(),
            )
        run.artifacts.update(
            {
                "records_jsonl": str(records_path),
                "requests_jsonl": str(requests_path),
                "raw_responses_jsonl": str(raw_path),
                "diagnostics_jsonl": str(diagnostics_path),
                "source_manifest_jsonl": str(self.output_dir / "source-manifest.jsonl"),
                "stages_jsonl": str(stage_path),
                "retrieval_candidates_jsonl": str(retrieval_path),
            }
        )
        dump_resolved_spec(self.spec, self.output_dir / "study.resolved.yaml")
        (self.output_dir / "source-manifest.jsonl").write_text(
            "".join(
                json.dumps(
                    {
                        "source_id": unit["source_id"],
                        "sha256": hashlib.sha256(str(unit.get("text", "")).encode()).hexdigest(),
                    },
                    ensure_ascii=False,
                )
                + "\n"
                for unit in units
            ),
            encoding="utf-8",
        )
        selection_path = self.output_dir / "source-selection.jsonl"
        selection_path.write_text(
            "".join(
                json.dumps(
                    {
                        "source_id": unit["source_id"],
                        "itemid": unit.get("itemid"),
                        "paragraph_id": unit.get("paragraph_id"),
                        "section": unit.get("section"),
                        "source_component": unit.get("source_component"),
                        "opinion_id": unit.get("opinion_id"),
                        "represented_by": unit.get("metadata", {}).get("represented_by")
                        or unit.get("metadata", {}).get("representedby"),
                        "metadata": {
                            key: value
                            for key, value in unit.get("metadata", {}).items()
                            if key not in {"text", "text_md", "body", "source_context"}
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n"
                for unit in units
            ),
            encoding="utf-8",
        )
        run.artifacts["source_selection_jsonl"] = str(selection_path)
        self._refresh_run_counts(run, records_path, raw_path)
        manifest_path.write_text(run.model_dump_json(indent=2), encoding="utf-8")
        latest = self._latest_records(records_path)
        outputs: dict[str, dict[str, dict[str, Any]]] = {}
        for row in latest.values():
            if row.get("status") == "ok":
                outputs.setdefault(str(row["source_id"]), {})[str(row["stage_id"])] = dict(
                    row.get("data") or {}
                )
        contexts: dict[str, str] = {}
        if retrieval_path.exists():
            contexts.update(
                {
                    str(row["source_id"]): str(row.get("context") or "")
                    for row in iter_jsonl(retrieval_path)
                }
            )
        try:
            for stage in self.spec.stages:
                if stage.id in run.completed_stages:
                    continue
                if self.cancel_check():
                    run.status = "cancelled"
                    break
                if stage.kind in {"select", "acquire", "export"}:
                    append_jsonl(stage_path, {"stage_id": stage.id, "status": "complete", "at": _now()})
                    run.completed_stages.append(stage.id)
                    continue
                if stage.kind == "retrieve":
                    from ..retrieval import HybridRetriever

                    if self.spec.retrieval is None:
                        raise ValueError("retrieve stage has no retrieval configuration")
                    retriever = HybridRetriever.from_spec(self.spec.retrieval)
                    retrieval_rows: list[dict[str, Any]] = []
                    for unit in units:
                        query = self.spec.retrieval.query or str(unit.get("text", ""))
                        hits = retriever.search(query)
                        contexts[unit["source_id"]] = "\n\n".join(
                            str(hit.get("text", "")) for hit in hits
                        )
                        retrieval_rows.append({
                            "source_id": unit["source_id"],
                            "query": query,
                            "hits": hits,
                            "context": contexts[unit["source_id"]],
                        })
                    retrieval_path.write_text(
                        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in retrieval_rows),
                        encoding="utf-8",
                    )
                    append_jsonl(stage_path, {"stage_id": stage.id, "status": "complete", "at": _now()})
                    run.completed_stages.append(stage.id)
                    continue
                if stage.kind == "verify" and not stage.provider:
                    for unit in units:
                        prior = outputs.get(unit["source_id"], {})
                        if not prior:
                            continue
                        previous_stage, payload = next(reversed(prior.items()))
                        task_id = self._task_id(unit, stage, {"type": "object"})
                        attempt_id = sha256_value({"task": task_id, "run": run.run_id, "verify": previous_stage})
                        source_row = next(
                            (
                                row
                                for row in reversed(list(latest.values()))
                                if row.get("source_id") == unit["source_id"]
                                and row.get("stage_id") == previous_stage
                            ),
                            None,
                        )
                        evidence = [EvidenceRef.model_validate(value) for value in (source_row or {}).get("evidence", [])]
                        verified, warnings = verify_evidence(evidence, unit)
                        record = StudyRecord(
                            task_id=task_id,
                            attempt_id=attempt_id,
                            study_id=self.spec.id,
                            study_version=self.spec.version,
                            source_id=unit["source_id"],
                            stage_id=stage.id,
                            status="ok" if not warnings else "invalid",
                            data=payload,
                            evidence=verified,
                            warnings=warnings,
                        )
                        append_jsonl(records_path, record.model_dump(mode="json"))
                    run.completed_stages.append(stage.id)
                    append_jsonl(stage_path, {"stage_id": stage.id, "status": "complete", "at": _now()})
                    latest = self._latest_records(records_path)
                    continue
                if not self._is_model_stage(stage):
                    raise ValueError(f"stage {stage.id!r} has no executable provider or hook")
                if stage.batch:
                    batch_status = self._run_batch_stage(
                        run=run,
                        stage=stage,
                        units=units,
                        contexts=contexts,
                        outputs=outputs,
                        records_path=records_path,
                        requests_path=requests_path,
                        raw_path=raw_path,
                        diagnostics_path=diagnostics_path,
                        wait=wait,
                        poll_interval=poll_interval,
                        max_polls=max_polls,
                    )
                    self._refresh_run_counts(run, records_path, raw_path)
                    if batch_status == "waiting":
                        run.status = "waiting"
                        run.waiting_stage = stage.id
                        break
                    if batch_status == "cancelled":
                        run.status = "cancelled"
                        break
                    if batch_status == "partial":
                        run.status = "partial"
                        break
                    run.waiting_stage = None
                    run.completed_stages.append(stage.id)
                    append_jsonl(
                        stage_path,
                        {"stage_id": stage.id, "status": "complete", "at": _now()},
                    )
                    latest = self._latest_records(records_path)
                    continue
                schema = stage.response_schema or self.spec.response_schema or {"type": "object"}
                request_schema = _evidence_schema(schema) if stage.required_evidence else schema
                provider = self._provider(stage)
                stage_units = units
                if stage.kind == "synthesize" and stage.options.get("scope") == "corpus":
                    stage_units = [{
                        "source_id": f"study:{self.spec.id}:synthesis",
                        "itemid": None,
                        "text": "\n\n".join(str(unit.get("text", "")) for unit in units),
                        "metadata": {"unit_ids": [unit["source_id"] for unit in units]},
                    }]
                prepared: list[tuple[dict[str, Any], str, str, str, int, float]] = []
                reserved_input = reserved_output = reserved_requests = 0
                reserved_cost = 0.0
                prior_requests = list(iter_jsonl(requests_path)) if requests_path.exists() else []
                for unit in stage_units:
                    task_id = self._task_id(unit, stage, schema)
                    current = latest.get((task_id, stage.id))
                    if current and current.get("status") in TERMINAL_RECORD_STATES:
                        continue
                    unit_outputs = outputs.get(unit["source_id"], {})
                    previous = next(reversed(unit_outputs.values())) if unit_outputs else {}
                    prompt = _render_prompt(
                        stage.prompt or "",
                        unit,
                        contexts.get(unit["source_id"], ""),
                        previous_output=previous,
                        stage_outputs=unit_outputs,
                    )
                    estimated_input = provider.count_tokens(prompt, model=stage.model)
                    estimated_cost = self._estimated_cost(stage, estimated_input, stage.max_output_tokens)
                    self._check_budget(
                        run,
                        next_input=reserved_input + estimated_input,
                        next_output=reserved_output + stage.max_output_tokens,
                        next_usd=reserved_cost + estimated_cost,
                        next_requests=reserved_requests + 1,
                    )
                    ordinal = sum(row.get("task_id") == task_id for row in prior_requests)
                    attempt_id = sha256_value({
                        "task": task_id,
                        "provider": stage.provider,
                        "model": stage.model,
                        "temperature": stage.temperature,
                        "max_output_tokens": stage.max_output_tokens,
                        "options": stage.options,
                        "batch": False,
                        "ordinal": ordinal,
                    })
                    request_row = {
                        "task_id": task_id,
                        "attempt_id": attempt_id,
                        "stage_id": stage.id,
                        "source_id": unit["source_id"],
                        "provider": stage.provider,
                        "model": stage.model,
                        "temperature": stage.temperature,
                        "max_output_tokens": stage.max_output_tokens,
                        "prompt": prompt,
                        "schema": request_schema,
                    }
                    append_jsonl(requests_path, request_row)
                    prepared.append((unit, task_id, attempt_id, prompt, estimated_input, estimated_cost))
                    reserved_input += estimated_input
                    reserved_output += stage.max_output_tokens
                    reserved_cost += estimated_cost
                    reserved_requests += 1

                def execute(
                    value: tuple[dict[str, Any], str, str, str, int, float],
                    provider: Any = provider,
                    request_schema: dict[str, Any] = request_schema,
                    stage: StageSpec = stage,
                ) -> tuple[Any, ...]:
                    unit, task_id, attempt_id, prompt, estimated_input, estimated_cost = value
                    try:
                        result = provider.extract(
                            prompt,
                            request_schema,
                            model=stage.model,
                            system_instruction=str(stage.options.get("system_instruction") or "") or None,
                            thinking_budget=int(stage.options.get("thinking_budget", 0)),
                            temperature=stage.temperature,
                            max_output_tokens=stage.max_output_tokens,
                            timeout_seconds=float(stage.options.get("timeout_seconds", 600)),
                            max_retries=int(stage.options.get("max_retries", 3)),
                        )
                    except Exception as exc:
                        from ..llm import ExtractResult

                        result = ExtractResult(
                            data={}, model=str(stage.model), provider=str(stage.provider), error=str(exc)
                        )
                    return unit, task_id, attempt_id, result, estimated_input, estimated_cost

                with ThreadPoolExecutor(max_workers=stage.concurrency) as pool:
                    futures = [pool.submit(execute, value) for value in prepared]
                    for future in as_completed(futures):
                        unit, task_id, attempt_id, result, estimated_input, estimated_cost = future.result()
                        actual_input = result.input_tokens or estimated_input
                        actual_cost = result.cost_usd or estimated_cost
                        append_jsonl(raw_path, {
                            "task_id": task_id,
                            "attempt_id": attempt_id,
                            "stage_id": stage.id,
                            "source_id": unit["source_id"],
                            "provider": result.provider,
                            "model": result.model,
                            "input_tokens": actual_input,
                            "output_tokens": result.output_tokens,
                            "cost_usd": actual_cost,
                            "raw_text": result.raw_text,
                            "data": result.data,
                            "error": result.error,
                        })
                        record = self._record_from_result(
                            result=result,
                            unit=unit,
                            stage=stage,
                            schema=schema,
                            task_id=task_id,
                            attempt_id=attempt_id,
                        )
                        append_jsonl(records_path, record.model_dump(mode="json"))
                        if record.status == "ok":
                            outputs.setdefault(unit["source_id"], {})[stage.id] = record.data
                        self._refresh_run_counts(run, records_path, raw_path)
                        manifest_path.write_text(run.model_dump_json(indent=2), encoding="utf-8")
                run.completed_stages.append(stage.id)
                append_jsonl(stage_path, {"stage_id": stage.id, "status": "complete", "at": _now()})
                latest = self._latest_records(records_path)
        except KeyboardInterrupt:
            run.status = "interrupted"
        except (BudgetExceededError, UnknownPricingError) as exc:
            append_jsonl(diagnostics_path, {"code": "budget_error", "message": str(exc)})
            run.status = "partial"
        except Exception as exc:
            append_jsonl(diagnostics_path, {"code": "run_error", "message": str(exc)})
            run.status = "partial"
        self._refresh_run_counts(run, records_path, raw_path)
        if run.status == "running":
            run.status = "complete" if run.errors == 0 and run.invalid == 0 else "partial"
        run.finished_at = None if run.status == "waiting" else _now()
        self._export(records_path, run)
        manifest_path.write_text(run.model_dump_json(indent=2), encoding="utf-8")
        return run

    def cancel(self) -> StudyRun:
        """Cancel a persisted native batch for the current run, if one is waiting."""
        manifest_path = self.output_dir / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"no study manifest under {self.output_dir}")
        run = StudyRun.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        if not run.waiting_stage:
            return run
        stage = next((value for value in self.spec.stages if value.id == run.waiting_stage), None)
        if stage is None or not stage.batch:
            raise ValueError(f"waiting stage {run.waiting_stage!r} is not a native batch stage")
        batch_path = self.output_dir / "batches" / stage.id / "manifest.json"
        batch = BatchStageManifest.model_validate_json(batch_path.read_text(encoding="utf-8"))
        provider = self._provider(stage)
        adapter = self._batch_adapter(stage, provider)
        batch = adapter.cancel(batch)
        batch_path.write_text(batch.model_dump_json(indent=2), encoding="utf-8")
        run.status = "cancelled"
        run.waiting_stage = None
        run.finished_at = _now()
        manifest_path.write_text(run.model_dump_json(indent=2), encoding="utf-8")
        return run

    def _export(self, records_path: Path, run: StudyRun) -> None:
        latest = self._latest_records(records_path)
        records = [latest[key] for key in sorted(latest)]
        latest_path = self.output_dir / "records-latest.jsonl"
        latest_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records),
            encoding="utf-8",
        )
        run.artifacts["records_latest_jsonl"] = str(latest_path)
        if self.spec.output.parquet:
            import pandas as pd

            parquet = self.output_dir / "records.parquet"
            parquet_records = [
                {
                    **record,
                    # Arrow cannot materialize a struct column when every
                    # provider-error record contains an empty object.
                    "data": record.get("data") or None,
                }
                for record in records
            ]
            pd.DataFrame(parquet_records).to_parquet(parquet, index=False)
            run.artifacts["records_parquet"] = str(parquet)
        if self.spec.output.report != "none":
            summary = (
                f"# {self.spec.id} study run\n\n"
                f"- Status: {run.status}\n- Records: {run.records}\n"
                f"- Invalid: {run.invalid}\n- Errors: {run.errors}\n"
                f"- Cost: ${run.cost_usd:.6f}\n"
            )
            if self.spec.output.report in {"markdown", "both"}:
                path = self.output_dir / "report.md"
                path.write_text(summary, encoding="utf-8")
                run.artifacts["report_markdown"] = str(path)
            if self.spec.output.report in {"html", "both"}:
                import html

                path = self.output_dir / "report.html"
                path.write_text(
                    "<!doctype html><meta charset='utf-8'><pre>" + html.escape(summary) + "</pre>",
                    encoding="utf-8",
                )
                run.artifacts["report_html"] = str(path)


def load_study_run(path: str | Path) -> StudyRun:
    source = Path(path)
    if source.is_dir():
        source = source / "manifest.json"
    return StudyRun.model_validate_json(source.read_text(encoding="utf-8"))


__all__ = [
    "BudgetExceededError",
    "StudyRunner",
    "acquire_source",
    "load_study_run",
    "unitize",
    "verify_evidence",
]
