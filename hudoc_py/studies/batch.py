"""Persisted native-batch adapters for bounded study stages."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol

from ..llm import ExtractResult
from ..llm.base import estimate_cost_for
from .models import BatchStageManifest

TERMINAL_BATCH_STATES = {"succeeded", "failed", "cancelled"}


def _now() -> str:
    import datetime as dt

    return dt.datetime.now(dt.UTC).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class BatchAdapter(Protocol):
    provider: str

    def prepare(self, rows: Iterable[dict[str, Any]], path: Path) -> int: ...
    def submit(
        self, *, path: Path, stage_id: str, model: str, task_ids: list[str]
    ) -> BatchStageManifest: ...
    def poll(self, manifest: BatchStageManifest) -> BatchStageManifest: ...
    def retrieve(
        self, manifest: BatchStageManifest, output_path: Path
    ) -> tuple[BatchStageManifest, dict[str, ExtractResult]]: ...
    def cancel(self, manifest: BatchStageManifest) -> BatchStageManifest: ...


class GeminiBatchAdapter:
    provider = "gemini"

    def __init__(self, client: Any):
        self.client = client

    def prepare(self, rows: Iterable[dict[str, Any]], path: Path) -> int:
        path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                generation: dict[str, Any] = {
                    "temperature": row["temperature"],
                    "maxOutputTokens": row["max_output_tokens"],
                    "responseMimeType": "application/json",
                    "responseJsonSchema": row["schema"],
                    "thinkingConfig": {
                        "thinkingBudget": int(row.get("thinking_budget", 0))
                    },
                }
                request: dict[str, Any] = {
                    "contents": [{"role": "user", "parts": [{"text": row["prompt"]}]}],
                    "generation_config": generation,
                }
                if row.get("system_instruction"):
                    request["system_instruction"] = {
                        "parts": [{"text": row["system_instruction"]}]
                    }
                handle.write(
                    json.dumps({"key": row["task_id"], "request": request}, ensure_ascii=False)
                    + "\n"
                )
                count += 1
        return count

    def submit(
        self, *, path: Path, stage_id: str, model: str, task_ids: list[str]
    ) -> BatchStageManifest:
        try:
            from google.genai import types
        except ImportError as exc:
            raise ImportError('Gemini batch requires "echr-py[llm]"') from exc
        uploaded = self.client.files.upload(
            file=str(path),
            config=types.UploadFileConfig(display_name=f"hudoc-{stage_id}", mime_type="jsonl"),
        )
        job = self.client.batches.create(
            model=model,
            src=uploaded.name,
            config={"display_name": f"hudoc-{stage_id}"},
        )
        now = _now()
        return BatchStageManifest(
            stage_id=stage_id,
            provider="gemini",
            model=model,
            job_id=job.name,
            state="submitted",
            task_ids=task_ids,
            requests_path=str(path),
            requests_sha256=_sha256(path),
            submitted_at=now,
            updated_at=now,
            provider_input_file_id=uploaded.name,
        )

    def poll(self, manifest: BatchStageManifest) -> BatchStageManifest:
        job = self.client.batches.get(name=manifest.job_id)
        raw = str(getattr(getattr(job, "state", None), "name", "")).upper()
        state = {
            "JOB_STATE_SUCCEEDED": "succeeded",
            "JOB_STATE_FAILED": "failed",
            "JOB_STATE_CANCELLED": "cancelled",
            "JOB_STATE_EXPIRED": "failed",
        }.get(raw, "running")
        diagnostics = list(manifest.diagnostics)
        if getattr(job, "error", None):
            diagnostics.append(str(job.error))
        dest = getattr(job, "dest", None)
        output_id = getattr(dest, "file_name", None) if dest is not None else None
        return manifest.model_copy(
            update={
                "state": state,
                "updated_at": _now(),
                "provider_output_file_id": output_id or manifest.provider_output_file_id,
                "diagnostics": diagnostics,
            }
        )

    def retrieve(
        self, manifest: BatchStageManifest, output_path: Path
    ) -> tuple[BatchStageManifest, dict[str, ExtractResult]]:
        if manifest.state != "succeeded" or not manifest.provider_output_file_id:
            raise ValueError("Gemini batch is not ready for retrieval")
        content = self.client.files.download(file=manifest.provider_output_file_id)
        text = content.decode("utf-8") if isinstance(content, bytes) else str(content)
        output_path.write_text(text, encoding="utf-8")
        results: dict[str, ExtractResult] = {}
        total_in = total_out = 0
        total_cost = 0.0
        diagnostics = list(manifest.diagnostics)
        for ordinal, line in enumerate(text.splitlines()):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                key = str(row.get("key") or "")
                response = row.get("response") or {}
                usage = response.get("usageMetadata") or {}
                input_tokens = int(usage.get("promptTokenCount", 0) or 0)
                output_tokens = int(usage.get("candidatesTokenCount", 0) or 0)
                thinking_tokens = int(usage.get("thoughtsTokenCount", 0) or 0)
                error = str(row["error"]) if row.get("error") else None
                raw = None
                data: dict[str, Any] = {}
                if error is None:
                    raw = response["candidates"][0]["content"]["parts"][0].get("text", "")
                    parsed = json.loads(raw)
                    if not isinstance(parsed, dict):
                        raise ValueError("JSON response is not an object")
                    data = parsed
                cost = estimate_cost_for(
                    manifest.model,
                    input_tokens,
                    output_tokens,
                    thinking_tokens,
                    batch=True,
                )
                results[key] = ExtractResult(
                    data=data,
                    model=manifest.model,
                    provider="gemini",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    thinking_tokens=thinking_tokens,
                    cost_usd=cost,
                    raw_text=raw,
                    error=error,
                )
                total_in += input_tokens
                total_out += output_tokens
                total_cost += cost
            except Exception as exc:
                diagnostics.append(f"result line {ordinal}: {exc}")
        return manifest.model_copy(
            update={
                "results_path": str(output_path),
                "results_sha256": _sha256(output_path),
                "input_tokens": total_in,
                "output_tokens": total_out,
                "cost_usd": round(total_cost, 6),
                "updated_at": _now(),
                "diagnostics": diagnostics,
            }
        ), results

    def cancel(self, manifest: BatchStageManifest) -> BatchStageManifest:
        self.client.batches.cancel(name=manifest.job_id)
        return manifest.model_copy(update={"state": "cancelled", "updated_at": _now()})


class OpenAIBatchAdapter:
    provider = "openai"

    def __init__(self, client: Any):
        self.client = client

    def prepare(self, rows: Iterable[dict[str, Any]], path: Path) -> int:
        path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                system = row.get("system_instruction") or "Return only valid JSON."
                body = {
                    "model": row["model"],
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": row["prompt"]},
                    ],
                    "temperature": row["temperature"],
                    "max_tokens": row["max_output_tokens"],
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "hudoc_study_response",
                            "strict": True,
                            "schema": row["schema"],
                        },
                    },
                }
                handle.write(json.dumps({
                    "custom_id": row["task_id"],
                    "method": "POST",
                    "url": "/v1/chat/completions",
                    "body": body,
                }, ensure_ascii=False) + "\n")
                count += 1
        return count

    def submit(
        self, *, path: Path, stage_id: str, model: str, task_ids: list[str]
    ) -> BatchStageManifest:
        with path.open("rb") as handle:
            uploaded = self.client.files.create(file=handle, purpose="batch")
        job = self.client.batches.create(
            input_file_id=uploaded.id,
            endpoint="/v1/chat/completions",
            completion_window="24h",
            metadata={"study_stage": stage_id},
        )
        now = _now()
        return BatchStageManifest(
            stage_id=stage_id,
            provider="openai",
            model=model,
            job_id=job.id,
            state="submitted",
            task_ids=task_ids,
            requests_path=str(path),
            requests_sha256=_sha256(path),
            submitted_at=now,
            updated_at=now,
            provider_input_file_id=uploaded.id,
        )

    def poll(self, manifest: BatchStageManifest) -> BatchStageManifest:
        job = self.client.batches.retrieve(manifest.job_id)
        raw = str(getattr(job, "status", ""))
        state = {
            "completed": "succeeded",
            "failed": "failed",
            "expired": "failed",
            "cancelled": "cancelled",
        }.get(raw, "running")
        diagnostics = list(manifest.diagnostics)
        errors = getattr(job, "errors", None)
        if errors:
            diagnostics.append(str(errors))
        return manifest.model_copy(update={
            "state": state,
            "updated_at": _now(),
            "provider_output_file_id": getattr(job, "output_file_id", None)
            or manifest.provider_output_file_id,
            "diagnostics": diagnostics,
        })

    def retrieve(
        self, manifest: BatchStageManifest, output_path: Path
    ) -> tuple[BatchStageManifest, dict[str, ExtractResult]]:
        if manifest.state != "succeeded" or not manifest.provider_output_file_id:
            raise ValueError("OpenAI batch is not ready for retrieval")
        response = self.client.files.content(manifest.provider_output_file_id)
        content = getattr(response, "content", response)
        text = content.decode("utf-8") if isinstance(content, bytes) else str(content)
        output_path.write_text(text, encoding="utf-8")
        results: dict[str, ExtractResult] = {}
        total_in = total_out = 0
        total_cost = 0.0
        diagnostics = list(manifest.diagnostics)
        for ordinal, line in enumerate(text.splitlines()):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                key = str(row.get("custom_id") or "")
                body = (row.get("response") or {}).get("body") or {}
                usage = body.get("usage") or {}
                input_tokens = int(usage.get("prompt_tokens", 0) or 0)
                output_tokens = int(usage.get("completion_tokens", 0) or 0)
                error_value = row.get("error") or body.get("error")
                error = str(error_value) if error_value else None
                raw = None
                data: dict[str, Any] = {}
                if error is None:
                    raw = body["choices"][0]["message"].get("content", "")
                    parsed = json.loads(raw)
                    if not isinstance(parsed, dict):
                        raise ValueError("JSON response is not an object")
                    data = parsed
                cost = estimate_cost_for(
                    manifest.model, input_tokens, output_tokens, batch=True
                )
                results[key] = ExtractResult(
                    data=data,
                    model=manifest.model,
                    provider="openai",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost,
                    raw_text=raw,
                    error=error,
                )
                total_in += input_tokens
                total_out += output_tokens
                total_cost += cost
            except Exception as exc:
                diagnostics.append(f"result line {ordinal}: {exc}")
        return manifest.model_copy(update={
            "results_path": str(output_path),
            "results_sha256": _sha256(output_path),
            "input_tokens": total_in,
            "output_tokens": total_out,
            "cost_usd": round(total_cost, 6),
            "updated_at": _now(),
            "diagnostics": diagnostics,
        }), results

    def cancel(self, manifest: BatchStageManifest) -> BatchStageManifest:
        self.client.batches.cancel(manifest.job_id)
        return manifest.model_copy(update={"state": "cancelled", "updated_at": _now()})


def batch_adapter(provider: str, client: Any) -> BatchAdapter:
    if provider == "gemini":
        return GeminiBatchAdapter(client)
    if provider == "openai":
        return OpenAIBatchAdapter(client)
    raise ValueError(f"provider {provider!r} does not support native study batches")


__all__ = [
    "BatchAdapter",
    "GeminiBatchAdapter",
    "OpenAIBatchAdapter",
    "TERMINAL_BATCH_STATES",
    "batch_adapter",
]
