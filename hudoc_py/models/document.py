"""Generic document model – used for separate opinions, advisory texts, etc."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Document(BaseModel):
    """A retrievable document body (HTML / text / markdown)."""

    model_config = ConfigDict(extra="ignore")

    itemid: str
    html: str | None = None
    text: str | None = None
    markdown: str | None = None
