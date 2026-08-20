"""Language-version and acquisition-manifest models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DocumentVersion(BaseModel):
    """One independently retrievable HUDOC language record."""

    model_config = ConfigDict(extra="ignore")

    itemid: str
    language: str = ""
    appno: list[str] = Field(default_factory=list)
    ecli: str | None = None
    docname: str | None = None
    doctype: str | None = None
    typedescription: str | None = None
    document_collection: str | None = None
    document_kind: Literal[
        "judgment", "decision", "advisory_opinion", "resolution", "legal_summary", "other"
    ] = "other"
    rendition_kind: Literal[
        "official_text", "official_summary", "translation", "translated_summary", "other"
    ] = "other"
    is_official_language: bool = False
    is_official_text: bool = False
    is_translation: bool = False
    is_summary: bool = False
    translation_attribution: str | None = None
    published_by: str | None = None
    external_sources: str | None = None


class ManifestFile(BaseModel):
    """One downloaded file with integrity metadata."""

    path: str
    format: str
    bytes: int
    sha256: str


class FormatOutcome(BaseModel):
    """Auditable outcome for one requested representation."""

    format: str
    status: Literal["downloaded", "derived", "missing", "error"]
    url: str | None = None
    http_status: int | None = None
    content_type: str | None = None
    attempts: int = 0
    retrieved_at: datetime | None = None
    error: str | None = None
    file: ManifestFile | None = None


class AcquisitionManifest(BaseModel):
    """Portable, deterministic record of a multilingual acquisition run."""

    schema_version: str = "hudoc-acquisition.v2"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    package_version: str
    source_service: str = "HUDOC"
    source_base_url: str = "https://hudoc.echr.coe.int"
    query: dict[str, str] = Field(default_factory=dict)
    selection_mode: Literal["exact_document", "application_records"]
    requested_formats: list[str] = Field(default_factory=list)
    discovered_count: int = 0
    selected_count: int = 0
    versions: list[DocumentVersion] = Field(default_factory=list)
    files: dict[str, list[ManifestFile]] = Field(default_factory=dict)
    outcomes: dict[str, list[FormatOutcome]] = Field(default_factory=dict)
    failures: list[str] = Field(default_factory=list)
    rights_notice: str = (
        "Non-official translations may be copyrighted by third parties. "
        "Verify the notice attached to each HUDOC record before redistribution."
    )
