"""Models for HUDOC-EXEC document-level records (action plans, CM decisions, etc.)."""

from __future__ import annotations

from datetime import date as date_t
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .common import parse_bool_loose, parse_hudoc_date, split_semicolon_list

if TYPE_CHECKING:
    import pandas as pd


class ExecutionDocument(BaseModel):
    """A non-case HUDOC-EXEC document (action plan, report, communication, CM decision)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    # Identifiers
    execidentifier: str | None = None
    sharepoint_id: str | None = Field(default=None, validation_alias="sharepointid")
    document_reference: str | None = Field(default=None, validation_alias="execdocumentreference")
    item_id_from_echr: str | None = Field(default=None, validation_alias="execitemidfromechr")
    master_group_id: str | None = Field(default=None, validation_alias="execmastergroupid")

    # Classification
    document_type: str | None = Field(default=None, validation_alias="execdocumenttype")
    document_type_collection: str | None = Field(
        default=None, validation_alias="execdocumenttypecollection"
    )
    content_site: str | None = Field(default=None, validation_alias="contentsitename")
    title: str | None = Field(default=None, validation_alias="exectitle")

    # Linkage
    appno: list[str] = Field(default_factory=list, validation_alias="execappno")
    state: str | None = Field(default=None, validation_alias="execstate")
    language: str | None = Field(default=None, validation_alias="execlanguage")

    # Storage pointers
    content_store_id: str | None = Field(default=None, validation_alias="execcontentstoreid")
    content_store_type: str | None = Field(default=None, validation_alias="execcontentstoretype")
    cm_meeting_number: str | None = Field(default=None, validation_alias="execcmmeetingnumber")

    # Dates
    published_date: date_t | None = Field(default=None, validation_alias="execpublisheddate")
    published_date_text: str | None = Field(
        default=None, validation_alias="execpublisheddateastext"
    )

    is_placeholder: bool | None = Field(default=None, validation_alias="isplaceholder")

    # Optional content – populated when the caller asks for it
    text: str | None = None

    @field_validator("appno", mode="before")
    @classmethod
    def _split_lists(cls, v: Any) -> list[str]:
        return split_semicolon_list(v)

    @field_validator("published_date", mode="before")
    @classmethod
    def _parse_dates(cls, v: Any) -> date_t | None:
        return parse_hudoc_date(v)

    @field_validator("is_placeholder", mode="before")
    @classmethod
    def _parse_bools(cls, v: Any) -> bool | None:
        return parse_bool_loose(v)


class ExecutionDocumentCollection(list[ExecutionDocument]):
    def to_dataframe(self) -> pd.DataFrame:
        import pandas as pd

        return pd.DataFrame([d.model_dump(mode="json") for d in self])
