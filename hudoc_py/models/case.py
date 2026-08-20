"""The :class:`Case` model – a row from HUDOC's main search, typed."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .common import (
    Sections,
    TextProvenance,
    parse_bool_loose,
    parse_float_loose,
    parse_hudoc_date,
    split_semicolon_list,
)

if TYPE_CHECKING:
    import pandas as pd


def _scrub_nans(row: dict[Any, Any]) -> dict[str, Any]:
    """Turn pandas NaN / the literal string ``"nan"`` back into ``None``.

    A Parquet→pandas→dict round-trip renders missing string cells as the
    float ``nan`` (which ``!= itself``) or, after CSV, the string ``"nan"``.
    Either would poison ``Case`` string fields, so normalise them to ``None``.
    """
    out: dict[str, Any] = {}
    for key, value in row.items():
        if value is None:
            continue
        if isinstance(value, float) and value != value:  # NaN
            continue
        if isinstance(value, str) and value.strip().lower() == "nan":
            continue
        out[str(key)] = value
    return out


class Case(BaseModel):
    """A HUDOC case record.

    All fields are optional because HUDOC's ``select`` payload varies by document
    type and language. Use ``Case.model_validate(row)`` to construct from a raw
    HUDOC search ``columns`` dict.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    # Identifiers
    itemid: str | None = None
    ecli: str | None = None
    appno: list[str] = Field(default_factory=list)
    appno_parts: list[str] = Field(default_factory=list, validation_alias="appnoparts")
    extracted_appno: list[str] = Field(default_factory=list, validation_alias="extractedappno")
    sclappnos: list[str] = Field(default_factory=list)
    casecitation: str | None = None

    # Names & description
    docname: str | None = None
    doctype: str | None = None
    doctype_branch: str | None = Field(default=None, validation_alias="doctypebranch")
    typedescription: str | None = None
    originatingbody: str | None = None
    document_collection_id: str | None = Field(default=None, validation_alias="documentcollectionid")
    document_collection_id2: str | None = Field(default=None, validation_alias="documentcollectionid2")
    is_placeholder: bool | None = Field(default=None, validation_alias="isplaceholder")

    # Substance
    respondent: list[str] = Field(default_factory=list)
    articles: list[str] = Field(default_factory=list, validation_alias="article")
    applicability: str | None = None
    conclusion: str | None = None
    importance: str | None = None
    issue: str | None = None
    kp_thesaurus: str | None = Field(default=None, validation_alias="kpthesaurus")
    echr_concepts: str | None = Field(default=None, validation_alias="ECHRConcepts")
    rules_of_court: str | None = Field(default=None, validation_alias="rulesofcourt")
    separate_opinion: bool | None = Field(default=None, validation_alias="separateopinion")
    scl: str | None = None
    represented_by: str | None = Field(default=None, validation_alias="representedby")

    # Dates – ``kp_date`` is HUDOC's primary "keypoint" date (judgment/decision date).
    kp_date: date | None = Field(default=None, validation_alias="kpdate")
    kp_date_text: str | None = Field(default=None, validation_alias="kpdateAsText")
    decision_date: date | None = Field(default=None, validation_alias="decisiondate")
    judgement_date: date | None = Field(default=None, validation_alias="judgementdate")
    resolution_date: date | None = Field(default=None, validation_alias="resolutiondate")
    introduction_date: date | None = Field(default=None, validation_alias="introductiondate")
    reference_date: date | None = Field(default=None, validation_alias="referencedate")
    report_date: date | None = Field(default=None, validation_alias="reportdate")
    meeting_number: str | None = Field(default=None, validation_alias="meetingnumber")
    resolution_number: str | None = Field(default=None, validation_alias="resolutionnumber")

    # Advisory opinions / publication
    advop_identifier: str | None = Field(default=None, validation_alias="advopidentifier")
    advop_status: str | None = Field(default=None, validation_alias="advopstatus")
    published_by: str | None = Field(default=None, validation_alias="publishedby")
    external_sources: str | None = Field(default=None, validation_alias="externalsources")

    # Language & ranking
    language: str | None = Field(default=None, validation_alias="languageisocode")
    language_number: str | None = Field(default=None, validation_alias="languagenumber")
    # Relevance score from HUDOC's ranking model. Only discriminating when the
    # query contains a full-text clause and results are relevance-sorted.
    rank: float | None = Field(default=None, validation_alias="Rank")
    echr_ranking: str | None = Field(default=None, validation_alias="ECHRRanking")
    sharepoint_id: str | None = Field(default=None, validation_alias="sharepointid")

    # Optional content – populated when caller asks for text
    text: str | None = None
    sections: Sections | None = None

    # --- Derived / echr-py-managed fields (NOT HUDOC columns) ---------------
    # Set by the bilingual reconcile/rescue pipeline and text-loading fallback.
    # Flat (not a nested model) so they survive to_dataframe()/parquet/jsonl
    # round-trips as plain string columns.
    #: itemid of the French-language sibling document, if known.
    french_itemid: str | None = None
    #: itemid of the document ``text`` was actually loaded from.
    text_source_itemid: str | None = None
    #: language ("ENG"/"FRE"/...) of the document ``text`` was loaded from.
    text_source_language: str | None = None

    @property
    def text_provenance(self) -> TextProvenance | None:
        """Typed provenance of ``text``; ``None`` if text was never loaded."""
        if self.text_source_itemid is None:
            return None
        return TextProvenance(
            source_itemid=self.text_source_itemid,
            source_language=self.text_source_language or "",
            is_fallback=(
                self.itemid is not None and self.text_source_itemid != self.itemid
            ),
        )

    @field_validator(
        "appno",
        "appno_parts",
        "extracted_appno",
        "sclappnos",
        "respondent",
        "articles",
        mode="before",
    )
    @classmethod
    def _split_lists(cls, v: Any) -> list[str]:
        return split_semicolon_list(v)

    @field_validator(
        "kp_date",
        "decision_date",
        "judgement_date",
        "resolution_date",
        "introduction_date",
        "reference_date",
        "report_date",
        mode="before",
    )
    @classmethod
    def _parse_dates(cls, v: Any) -> date | None:
        return parse_hudoc_date(v)

    @field_validator("separate_opinion", "is_placeholder", mode="before")
    @classmethod
    def _parse_bools(cls, v: Any) -> bool | None:
        return parse_bool_loose(v)

    @field_validator("rank", mode="before")
    @classmethod
    def _parse_rank(cls, v: Any) -> float | None:
        return parse_float_loose(v)

    @field_validator("doctype", "language", mode="before")
    @classmethod
    def _strip_str(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip()
        return v


class CaseCollection(list[Case]):
    """A list of :class:`Case` with a pandas helper for tabular analysis.

    ``result_count`` is the server-reported total number of matches for the
    originating search (may exceed ``len(self)`` when a ``limit`` was set);
    ``None`` when the collection did not come from a search.
    """

    result_count: int | None = None

    @classmethod
    def from_records(cls, rows: Iterable[dict[str, Any]]) -> CaseCollection:
        """Build a collection from dicts (e.g. JSONL rows or DataFrame records).

        Scrubs the ``"nan"`` / NaN artifacts that a Parquet→pandas→dict
        round-trip introduces, so string fields come back as ``None`` rather
        than the literal float ``nan`` or string ``"nan"``.
        """
        return cls(Case.model_validate(_scrub_nans(row)) for row in rows)

    @classmethod
    def from_dataframe(cls, df: pd.DataFrame) -> CaseCollection:
        """Build a collection from a pandas DataFrame (inverse of ``to_dataframe``)."""
        return cls(Case.model_validate(_scrub_nans(row)) for row in df.to_dict("records"))

    def to_dataframe(self) -> pd.DataFrame:
        import pandas as pd

        return pd.DataFrame([c.model_dump(mode="json") for c in self])

    def to_jsonl(self, path: str) -> None:
        import json
        from pathlib import Path

        Path(path).write_text(
            "\n".join(json.dumps(c.model_dump(mode="json"), ensure_ascii=False) for c in self)
            + "\n",
            encoding="utf-8",
        )
