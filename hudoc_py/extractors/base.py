"""Extractor framework: prompt + Pydantic schema + post-validation per extractor.

Each extractor bundles the domain knowledge (system prompt, response schema,
validation rules) and produces provider-agnostic requests:

* ``prepare(item)`` → :class:`~hudoc_py.llm.batch.PreparedRequest` – shared by
  the realtime path (``extract``) and the Batch API path.
* ``validate(data)`` → ``(clean_data, warnings)`` – Pydantic parse plus
  extractor-specific checks (e.g. taxonomy-code validation).
* ``extract(item, provider=...)`` → :class:`ExtractionRecord` – one checkpoint
  record, ready for :func:`hudoc_py.utils.append_jsonl`.

Checkpoint record shape (one JSONL line per item)::

    {"itemid": "004-47097", "extractor": "exec-doc", "status": "ok",
     "source_itemid": "004-47097", "source_language": "ENG",
     "data": {...}, "warnings": [],
     "_meta": {"provider": "gemini", "model": "...", "input_tokens": ...,
               "cost_usd": ..., "error": null, ...}}

Provenance keys are top-level so existing analysis code can consume records
without extractor-specific adapters.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel, Field, ValidationError

from ..llm.base import ExtractResult, Provider
from ..llm.batch import PreparedRequest

__all__ = ["Provenance", "ExtractionRecord", "Extractor", "PreparedRequest"]


class Provenance(BaseModel):
    """Where the text that fed an extraction actually came from."""

    source_itemid: str = ""
    source_language: str = ""
    word_count: int | None = None
    word_limit: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    def as_row(self) -> dict[str, Any]:
        """Flat dict for the batch provenance side-CSV."""
        return {
            "source_itemid": self.source_itemid,
            "source_language": self.source_language,
            "word_count": self.word_count if self.word_count is not None else "",
            "word_limit": self.word_limit if self.word_limit is not None else "",
        }


class ExtractionRecord(BaseModel):
    """One item's extraction result – one JSONL checkpoint line."""

    itemid: str
    extractor: str
    status: str = "ok"  # "ok" | "error"
    data: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    provenance: Provenance = Field(default_factory=Provenance)
    meta: dict[str, Any] = Field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def to_log_record(self) -> dict[str, Any]:
        """Flatten to the checkpoint-line shape (provenance keys top-level)."""
        record: dict[str, Any] = {
            "itemid": self.itemid,
            "extractor": self.extractor,
            "status": self.status,
            "source_itemid": self.provenance.source_itemid,
            "source_language": self.provenance.source_language,
            "data": self.data,
            "warnings": self.warnings,
            "_meta": self.meta,
        }
        if self.provenance.extra:
            record["provenance"] = self.provenance.extra
        return record


class Extractor(ABC):
    """Base class: one LLM extraction task over one document type."""

    name: ClassVar[str]
    id_field: ClassVar[str] = "itemid"
    ResponseModel: ClassVar[type[BaseModel]]

    # --- request construction ------------------------------------------------

    @abstractmethod
    def prepare(self, item: Any, **kwargs: Any) -> PreparedRequest | None:
        """Build the LLM request for one item; ``None`` means skip (no text)."""

    def item_id(self, item: Any) -> str:
        """Extract the checkpoint id from an item (dict, model, or object)."""
        if isinstance(item, dict):
            value = item.get(self.id_field) or item.get("id")
        else:
            value = getattr(item, self.id_field, None)
        return str(value) if value else ""

    # --- validation ----------------------------------------------------------

    def validate(self, data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """Parse ``data`` against :attr:`ResponseModel`.

        Default implementation: strict parse, and on failure a single retry
        after :meth:`repair` (subclasses override that for lenient coercion).
        Returns ``(clean_data, warnings)`` and raises ``ValidationError`` only
        if repair also fails.
        """
        warnings: list[str] = []
        try:
            model = self.ResponseModel.model_validate(data)
        except ValidationError as exc:
            repaired = self.repair(data, exc, warnings)
            model = self.ResponseModel.model_validate(repaired)
        return model.model_dump(), warnings

    def repair(
        self,
        data: dict[str, Any],
        error: ValidationError,
        warnings: list[str],
    ) -> dict[str, Any]:
        """Hook for lenient coercion of near-miss LLM output. Default: re-raise."""
        raise error

    # --- realtime execution -------------------------------------------------------

    def extract(self, item: Any, *, provider: Provider, **kwargs: Any) -> ExtractionRecord:
        """Prepare → call provider → validate → assemble one checkpoint record."""
        itemid = self.item_id(item)
        request = self.prepare(item, **kwargs)
        if request is None:
            return ExtractionRecord(
                itemid=itemid,
                extractor=self.name,
                status="error",
                meta={"error": "No text found"},
            )

        result = provider.extract(
            request.user_text,
            self.ResponseModel,
            system_instruction=request.system_instruction,
        )
        return self.build_record(itemid, request, result)

    def build_record(
        self,
        itemid: str,
        request: PreparedRequest,
        result: ExtractResult,
    ) -> ExtractionRecord:
        """Assemble a record from a provider result (shared by retry paths)."""
        provenance = Provenance(
            source_itemid=str(request.provenance.get("source_itemid", "") or ""),
            source_language=str(request.provenance.get("source_language", "") or ""),
            word_count=request.provenance.get("word_count"),
            word_limit=request.provenance.get("word_limit"),
        )
        if not result.ok:
            return ExtractionRecord(
                itemid=itemid,
                extractor=self.name,
                status="error",
                provenance=provenance,
                meta=result.meta(),
            )
        try:
            data, warnings = self.validate(result.data)
        except ValidationError as exc:
            return ExtractionRecord(
                itemid=itemid,
                extractor=self.name,
                status="error",
                provenance=provenance,
                meta={**result.meta(), "error": f"schema validation failed: {exc}"},
            )
        return ExtractionRecord(
            itemid=itemid,
            extractor=self.name,
            data=data,
            warnings=warnings,
            provenance=provenance,
            meta=result.meta(),
        )

    # --- batch integration -------------------------------------------------------

    def parse_batch_payload(
        self, parsed: dict[str, Any], prov_row: dict[str, str]
    ) -> dict[str, Any]:
        """Map one raw batch result line to a checkpoint record dict.

        Passed as ``parse_payload`` to :func:`hudoc_py.llm.batch.retrieve_batch`.
        """
        from ..llm.batch import extract_response_payload

        key = str(parsed.get("key", "") or "")
        data, error = extract_response_payload(parsed)
        provenance = Provenance(
            source_itemid=prov_row.get("source_itemid", ""),
            source_language=prov_row.get("source_language", ""),
        )
        if error is not None:
            record = ExtractionRecord(
                itemid=key, extractor=self.name, status="error",
                provenance=provenance, meta={"error": error},
            )
            return record.to_log_record()
        try:
            clean, warnings = self.validate(data or {})
        except ValidationError as exc:
            record = ExtractionRecord(
                itemid=key, extractor=self.name, status="error",
                provenance=provenance,
                meta={"error": f"schema validation failed: {exc}"},
            )
            return record.to_log_record()
        record = ExtractionRecord(
            itemid=key, extractor=self.name, data=clean, warnings=warnings,
            provenance=provenance,
        )
        return record.to_log_record()
