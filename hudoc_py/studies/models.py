"""Versioned public models for reproducible HUDOC research studies."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

StageKind = Literal[
    "select",
    "acquire",
    "retrieve",
    "rerank",
    "triage",
    "extract",
    "verify",
    "synthesize",
    "export",
]
UnitKind = Literal[
    "document",
    "section",
    "paragraph",
    "opinion",
    "citation_occurrence",
]


class SourceSpec(BaseModel):
    kind: Literal["local", "hudoc"] = "local"
    path: str | None = None
    query: str | None = None
    filters: dict[str, Any] = Field(default_factory=dict)
    limit: int | None = None

    @model_validator(mode="after")
    def require_source(self) -> SourceSpec:
        if self.kind == "local" and not self.path:
            raise ValueError("local study sources require path")
        return self


class RetrievalSpec(BaseModel):
    mode: Literal["lexical", "semantic", "hybrid"] = "lexical"
    database: str | None = None
    embeddings: str | None = None
    query: str | None = None
    top_k: int = Field(default=25, ge=1)
    candidate_k: int = Field(default=100, ge=1)
    rrf_k: int = Field(default=60, ge=1)
    filters: dict[str, Any] = Field(default_factory=dict)


class BudgetSpec(BaseModel):
    max_requests: int | None = Field(default=None, ge=1)
    max_input_tokens: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    max_usd: float | None = Field(default=None, gt=0)


class OutputSpec(BaseModel):
    parquet: bool = True
    jsonl: bool = True
    report: Literal["none", "markdown", "html", "both"] = "none"


class StageSpec(BaseModel):
    id: str
    kind: StageKind
    provider: str | None = None
    model: str | None = None
    prompt: str | None = None
    prompt_path: str | None = None
    response_schema: dict[str, Any] | None = None
    schema_path: str | None = None
    required_evidence: bool = False
    evidence_fields: list[str] = Field(default_factory=list)
    max_output_tokens: int = Field(default=2048, ge=1)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    concurrency: int = Field(default=1, ge=1, le=64)
    batch: bool = False
    options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_model_stage(self) -> StageSpec:
        model_stage = self.kind in {"triage", "extract", "synthesize"} or (
            self.kind in {"rerank", "verify"} and bool(self.provider or self.model)
        )
        if model_stage and not (self.provider and self.model):
            raise ValueError(f"model-backed stage {self.id!r} requires provider and model")
        if not (self.prompt or self.prompt_path) and model_stage:
            raise ValueError(f"model-backed stage {self.id!r} requires prompt or prompt_path")
        return self


class StudySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["hudoc-study/v1"] = "hudoc-study/v1"
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    version: str
    description: str = ""
    source: SourceSpec
    unit: UnitKind = "document"
    section: str | None = None
    stages: list[StageSpec]
    response_schema: dict[str, Any] | None = None
    schema_path: str | None = None
    retrieval: RetrievalSpec | None = None
    budget: BudgetSpec = Field(default_factory=BudgetSpec)
    output: OutputSpec = Field(default_factory=OutputSpec)
    hook: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_study(self) -> StudySpec:
        ids = [stage.id for stage in self.stages]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("study stages must be non-empty and have unique ids")
        if any(stage.kind == "retrieve" for stage in self.stages) and self.retrieval is None:
            raise ValueError("retrieve stages require a retrieval configuration")
        if any(stage.kind == "rerank" and not stage.provider for stage in self.stages) and not self.hook:
            raise ValueError("deterministic rerank stages require an installed study hook")
        if self.unit == "section" and not self.section:
            raise ValueError("section units require section")
        unsupported_batch = [
            stage.id
            for stage in self.stages
            if stage.batch and str(stage.provider).lower() not in {"gemini", "openai"}
        ]
        if unsupported_batch:
            raise ValueError(
                "native batch is supported only for Gemini and official OpenAI; "
                f"unsupported stages: {unsupported_batch}"
            )
        return self


class EvidenceRef(BaseModel):
    field_path: str = ""
    itemid: str | None = None
    paragraph_id: str | None = None
    section: str | None = None
    source_component: str | None = None
    opinion_id: str | None = None
    quote: str
    start: int | None = None
    end: int | None = None


class StudyRecord(BaseModel):
    schema_version: Literal["hudoc-study-record/v1"] = "hudoc-study-record/v1"
    task_id: str
    attempt_id: str
    study_id: str
    study_version: str
    source_id: str
    stage_id: str
    status: Literal["ok", "invalid", "error", "skipped", "interrupted"]
    data: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class StudyRun(BaseModel):
    schema_version: Literal["hudoc-study-run/v1", "hudoc-study-run/v2"] = "hudoc-study-run/v2"
    run_id: str
    study_id: str
    study_version: str
    status: Literal[
        "planned", "running", "waiting", "complete", "partial", "cancelled", "interrupted"
    ]
    output_dir: str
    spec_sha256: str
    source_sha256: str | None = None
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    records: int = 0
    errors: int = 0
    invalid: int = 0
    completed_stages: list[str] = Field(default_factory=list)
    waiting_stage: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)


class ProviderCapabilities(BaseModel):
    structured_generation: bool = True
    embeddings: bool = False
    native_batch: bool = False
    token_counting: bool = True


class BatchStageManifest(BaseModel):
    schema_version: Literal["hudoc-study-batch/v1"] = "hudoc-study-batch/v1"
    stage_id: str
    provider: Literal["gemini", "openai"]
    model: str
    job_id: str
    state: Literal["submitted", "running", "succeeded", "failed", "cancelled"]
    task_ids: list[str]
    requests_path: str
    requests_sha256: str
    submitted_at: str
    updated_at: str
    provider_input_file_id: str | None = None
    provider_output_file_id: str | None = None
    results_path: str | None = None
    results_sha256: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    diagnostics: list[str] = Field(default_factory=list)


__all__ = [
    "BudgetSpec",
    "BatchStageManifest",
    "EvidenceRef",
    "OutputSpec",
    "ProviderCapabilities",
    "RetrievalSpec",
    "SourceSpec",
    "StageSpec",
    "StudyRecord",
    "StudyRun",
    "StudySpec",
]
