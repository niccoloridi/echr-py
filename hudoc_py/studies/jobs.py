"""Persistent, path-confined background study jobs for opt-in MCP use."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .runner import StudyRunner
from .spec import load_study_spec


class StudyJob(BaseModel):
    schema_version: str = "hudoc-study-job/v1"
    job_id: str
    spec_path: str
    output_dir: str
    status: str
    created_at: str
    updated_at: str
    error: str | None = None
    run_status: str | None = None
    artifacts: list[str] = Field(default_factory=list)


def _inside(path: Path, roots: tuple[Path, ...]) -> bool:
    resolved = path.resolve()
    return any(resolved == root or root in resolved.parents for root in roots)


class StudyJobManager:
    def __init__(
        self,
        *,
        output_root: str | Path,
        input_roots: list[str | Path],
        allowed_providers: set[str],
        allowed_studies: set[str],
        pricing: dict[str, dict[str, float]],
        max_job_budget_usd: float,
        max_workers: int = 2,
    ):
        self.output_root = Path(output_root).resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.input_roots = tuple(Path(value).resolve() for value in input_roots)
        self.allowed_providers = {value.lower() for value in allowed_providers}
        self.allowed_studies = allowed_studies
        self.pricing = pricing
        self.max_job_budget_usd = max_job_budget_usd
        self._pool = ThreadPoolExecutor(max_workers=max_workers)
        self._futures: dict[str, Future[Any]] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._mark_interrupted()

    @classmethod
    def from_pricing_file(cls, *, pricing_file: str | Path, **kwargs: Any) -> StudyJobManager:
        pricing = json.loads(Path(pricing_file).read_text(encoding="utf-8"))
        if not isinstance(pricing, dict):
            raise ValueError("pricing file must contain an object keyed by provider:model")
        return cls(pricing=pricing, **kwargs)

    def _mark_interrupted(self) -> None:
        for status_file in self.output_root.glob("*/job.json"):
            job = StudyJob.model_validate_json(status_file.read_text(encoding="utf-8"))
            if job.status in {"queued", "running", "cancelling"}:
                job.status = "interrupted"
                job.updated_at = dt.datetime.now(dt.UTC).isoformat()
                status_file.write_text(job.model_dump_json(indent=2), encoding="utf-8")

    def _status_path(self, job_id: str) -> Path:
        if not job_id or any(value in job_id for value in ("/", "\\", "..")):
            raise ValueError("invalid job id")
        return self.output_root / job_id / "job.json"

    def _save(self, job: StudyJob) -> None:
        path = self._status_path(job.job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        job.updated_at = dt.datetime.now(dt.UTC).isoformat()
        path.write_text(job.model_dump_json(indent=2), encoding="utf-8")

    def _validate_spec(self, spec_path: str | Path) -> Any:
        path = Path(spec_path).resolve()
        if not _inside(path, self.input_roots):
            raise PermissionError("study spec is outside permitted input roots")
        spec = load_study_spec(path)
        if spec.id not in self.allowed_studies:
            raise PermissionError(f"study {spec.id!r} is not allowed")
        providers = {str(stage.provider).lower() for stage in spec.stages if stage.provider}
        if not providers <= self.allowed_providers:
            raise PermissionError(
                f"study providers are not allowed: {sorted(providers - self.allowed_providers)}"
            )
        if spec.hook and ":" in spec.hook:
            raise PermissionError("MCP jobs accept installed study hooks only")
        if spec.budget.max_usd is None or spec.budget.max_usd > self.max_job_budget_usd:
            raise PermissionError(f"study max_usd must be set and <= {self.max_job_budget_usd}")
        for stage in spec.stages:
            if stage.provider and not (
                self.pricing.get(f"{stage.provider}:{stage.model}")
                or self.pricing.get(str(stage.model))
            ):
                raise PermissionError(f"no approved pricing for {stage.provider}:{stage.model}")
        paths = [spec.source.path]
        if spec.retrieval:
            paths.extend([spec.retrieval.database, spec.retrieval.embeddings])
        for value in paths:
            if value and not _inside(Path(value), self.input_roots):
                raise PermissionError(f"study input is outside permitted roots: {value}")
        return spec, path

    def create(self, spec_path: str | Path) -> StudyJob:
        spec, path = self._validate_spec(spec_path)
        stamp = dt.datetime.now(dt.UTC).isoformat()
        job_id = hashlib.sha256(f"{path}|{stamp}".encode()).hexdigest()[:20]
        output = self.output_root / job_id / "run"
        job = StudyJob(
            job_id=job_id,
            spec_path=str(path),
            output_dir=str(output),
            status="queued",
            created_at=stamp,
            updated_at=stamp,
        )
        self._save(job)
        self._submit(job, spec)
        return job

    def _submit(self, job: StudyJob, spec: Any) -> None:
        cancel = threading.Event()
        self._cancel[job.job_id] = cancel

        def work() -> None:
            job.status = "running"
            self._save(job)
            try:
                run = StudyRunner(
                    spec,
                    job.output_dir,
                    installed_only_hooks=True,
                    cancel_check=cancel.is_set,
                    pricing=self.pricing,
                ).run(resume=True)
                job.run_status = run.status
                job.status = (
                    "cancelled"
                    if run.status == "cancelled"
                    else (
                        "complete"
                        if run.status == "complete"
                        else ("waiting" if run.status == "waiting" else "partial")
                    )
                )
                job.artifacts = sorted(
                    str(path.relative_to(self.output_root / job.job_id))
                    for path in Path(job.output_dir).rglob("*")
                    if path.is_file()
                )
            except Exception as exc:
                job.status = "error"
                job.error = str(exc)
            self._save(job)

        self._futures[job.job_id] = self._pool.submit(work)

    def get(self, job_id: str) -> StudyJob:
        path = self._status_path(job_id)
        if not path.exists():
            raise KeyError(f"unknown study job {job_id}")
        return StudyJob.model_validate_json(path.read_text(encoding="utf-8"))

    def list_jobs(self) -> list[StudyJob]:
        return sorted(
            (self.get(path.parent.name) for path in self.output_root.glob("*/job.json")),
            key=lambda job: job.created_at,
            reverse=True,
        )

    def cancel(self, job_id: str) -> StudyJob:
        job = self.get(job_id)
        if job.status == "waiting":
            spec, _ = self._validate_spec(job.spec_path)
            run = StudyRunner(
                spec,
                job.output_dir,
                installed_only_hooks=True,
                pricing=self.pricing,
            ).cancel()
            job.status = "cancelled"
            job.run_status = run.status
            self._save(job)
            return job
        if job.status not in {"queued", "running"}:
            return job
        job.status = "cancelling"
        self._save(job)
        self._cancel.setdefault(job_id, threading.Event()).set()
        return job

    def resume(self, job_id: str) -> StudyJob:
        job = self.get(job_id)
        if job.status not in {"interrupted", "partial", "error", "cancelled", "waiting"}:
            raise ValueError(f"job {job_id} is not resumable from {job.status}")
        spec, _ = self._validate_spec(job.spec_path)
        job.status = "queued"
        job.error = None
        self._save(job)
        self._submit(job, spec)
        return job

    def artifacts(self, job_id: str) -> list[dict[str, Any]]:
        job = self.get(job_id)
        root = self.output_root / job_id
        values = []
        for relative in job.artifacts:
            path = (root / relative).resolve()
            if _inside(path, (root,)) and path.is_file():
                values.append({"path": relative, "bytes": path.stat().st_size})
        return values


__all__ = ["StudyJob", "StudyJobManager"]
