"""Citation model – a single reference parsed from a HUDOC ``scl`` field."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    import pandas as pd


class Citation(BaseModel):
    """One case-to-case reference parsed from the ``scl`` field.

    HUDOC's ``scl`` is a semicolon-delimited list of free-text citations
    like::

        Klass and Others v. Germany, 6 September 1978, § 41, Series A no. 28
        Aquilina v. Malta [GC], no. 25642/94, § 49, ECHR 1999-III

    The extractor:

    * splits the field on ``;``
    * regex-extracts application numbers (``\\d{3,5}/\\d{2}``) from each ref
    * captures a best-effort ``cited_name`` (the case name)
    * stores the raw fragment in ``raw_ref`` so users can inspect it
    * sets ``resolved=True`` only when at least one ``cited_appno`` was
      matched against a case in the corpus

    Pre-1998 Series-A judgments often have no appno in their citation form,
    so a non-trivial fraction of refs land unresolved. See
    :class:`hudoc_py.citations.CitationGraph` for resolution and the
    ``missing_refs`` bucket.
    """

    model_config = ConfigDict(extra="ignore")

    # Source: the citing case
    source_itemid: str | None = None
    source_appno: list[str] = Field(default_factory=list)

    # The reference as it appeared in scl
    raw_ref: str

    # What we parsed out of the ref
    cited_name: str | None = None
    cited_appnos: list[str] = Field(default_factory=list)

    # What we resolved against the corpus (None if unresolved)
    cited_itemid: str | None = None
    cited_ecli: str | None = None
    cited_docname: str | None = None

    # Measurement-grade resolver metadata. Legacy extraction leaves these unset.
    source_ecli: str | None = None
    mention_id: str | None = None
    reference_hash: str | None = None
    target_node_id: str | None = None
    resolution_status: str | None = None
    resolution_method: str | None = None

    resolved: bool = False


class CitationCollection(list[Citation]):
    """A list of :class:`Citation` with pandas helpers."""

    def to_dataframe(self) -> pd.DataFrame:
        import pandas as pd

        return pd.DataFrame([c.model_dump(mode="json") for c in self])

    @property
    def resolved(self) -> CitationCollection:
        return CitationCollection(c for c in self if c.resolved)

    @property
    def unresolved(self) -> CitationCollection:
        return CitationCollection(c for c in self if not c.resolved)
