"""ExecutionCase model – a case record from HUDOC-EXEC."""

from __future__ import annotations

from datetime import date as date_t
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .common import parse_bool_loose, parse_hudoc_date, split_semicolon_list
from .exec_document import ExecutionDocument

if TYPE_CHECKING:
    import pandas as pd


class ExecutionCase(BaseModel):
    """A case-level record from HUDOC-EXEC (``execdocumenttypecollection=CEC``)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    # Identifiers
    execidentifier: str | None = None
    sharepoint_id: str | None = Field(default=None, validation_alias="sharepointid")
    document_reference: str | None = Field(default=None, validation_alias="execdocumentreference")
    item_id_from_echr: str | None = Field(default=None, validation_alias="execitemidfromechr")
    docname_from_echr: str | None = Field(default=None, validation_alias="execdocnamefromechr")
    master_group_id: str | None = Field(default=None, validation_alias="execmastergroupid")

    # Classification
    document_type: str | None = Field(default=None, validation_alias="execdocumenttype")
    document_type_collection: str | None = Field(
        default=None, validation_alias="execdocumenttypecollection"
    )
    content_site: str | None = Field(default=None, validation_alias="contentsitename")
    title: str | None = Field(default=None, validation_alias="exectitle")
    case_type: str | None = Field(default=None, validation_alias="exectype")
    class_indicator: str | None = Field(default=None, validation_alias="execclassindicator")
    theme_domain: str | None = Field(default=None, validation_alias="execthemedomain")
    short_desc: str | None = Field(default=None, validation_alias="execshortdesc")

    # Application & state
    appno: list[str] = Field(default_factory=list, validation_alias="execappno")
    state: str | None = Field(default=None, validation_alias="execstate")
    state_sort_order: str | None = Field(default=None, validation_alias="statesortordereng")
    language: str | None = Field(default=None, validation_alias="execlanguage")

    # Supervision / status
    supervision: str | None = Field(default=None, validation_alias="execsupervision")
    is_closed: bool | None = Field(default=None, validation_alias="execisclosed")
    ap_status: str | None = Field(default=None, validation_alias="execapstatus")
    short_status_execution: str | None = Field(
        default=None, validation_alias="execshortstatusexecution"
    )

    # Violations
    violations: str | None = Field(default=None, validation_alias="execviolations")
    violations_from_echr: str | None = Field(
        default=None, validation_alias="execviolationsfromechr"
    )

    # Precedents
    is_precedent: bool | None = Field(default=None, validation_alias="execisprecedent")
    precedent_cases: str | None = Field(default=None, validation_alias="execprecedentcases")
    precedent_appnos: list[str] = Field(
        default_factory=list, validation_alias="execprecedentappnos"
    )

    # Payment
    payment_status: str | None = Field(default=None, validation_alias="execpaymentstatus")
    payment_date: date_t | None = Field(default=None, validation_alias="execpaymentdate")
    payment_date_text: str | None = Field(default=None, validation_alias="execpaymentdateastext")

    # Just satisfaction
    fs: str | None = Field(default=None, validation_alias="execfs")
    fs_with_ut: str | None = Field(default=None, validation_alias="execfswithut")

    # Resolutions
    resolution_number: str | None = Field(default=None, validation_alias="execresolutionnumber")
    final_resolution_date: date_t | None = Field(
        default=None, validation_alias="execfinalresolutiondate"
    )
    final_resolution_date_text: str | None = Field(
        default=None, validation_alias="execfinalresolutiondateastext"
    )

    # Storage pointers
    content_store_id: str | None = Field(default=None, validation_alias="execcontentstoreid")
    content_store_type: str | None = Field(default=None, validation_alias="execcontentstoretype")
    cm_meeting_number: str | None = Field(default=None, validation_alias="execcmmeetingnumber")

    # Judgment dates
    published_date: date_t | None = Field(default=None, validation_alias="execpublisheddate")
    published_date_text: str | None = Field(
        default=None, validation_alias="execpublisheddateastext"
    )
    judgment_date: date_t | None = Field(default=None, validation_alias="execjudgmentdate")
    judgment_date_text: str | None = Field(
        default=None, validation_alias="execjudgmentdateastext"
    )
    final_judgment_date: date_t | None = Field(
        default=None, validation_alias="execfinaljudgmentdate"
    )
    final_judgment_date_text: str | None = Field(
        default=None, validation_alias="execfinaljudgmentdateastext"
    )

    # Misc
    rank: str | None = None
    ranking: str | None = Field(default=None, validation_alias="execranking")
    is_placeholder: bool | None = Field(default=None, validation_alias="isplaceholder")

    # Linked documents – populated by fetch_case when the caller asks for them
    action_plans: list[ExecutionDocument] = Field(default_factory=list)
    action_reports: list[ExecutionDocument] = Field(default_factory=list)
    cm_decisions: list[ExecutionDocument] = Field(default_factory=list)
    communications: list[ExecutionDocument] = Field(default_factory=list)
    resolutions: list[ExecutionDocument] = Field(default_factory=list)

    @field_validator("appno", "precedent_appnos", mode="before")
    @classmethod
    def _split_lists(cls, v: Any) -> list[str]:
        return split_semicolon_list(v)

    @field_validator(
        "payment_date",
        "final_resolution_date",
        "published_date",
        "judgment_date",
        "final_judgment_date",
        mode="before",
    )
    @classmethod
    def _parse_dates(cls, v: Any) -> date_t | None:
        return parse_hudoc_date(v)

    @field_validator("is_closed", "is_precedent", "is_placeholder", mode="before")
    @classmethod
    def _parse_bools(cls, v: Any) -> bool | None:
        return parse_bool_loose(v)


class ExecutionCaseCollection(list[ExecutionCase]):
    def to_dataframe(self) -> pd.DataFrame:
        import pandas as pd

        return pd.DataFrame([c.model_dump(mode="json") for c in self])
