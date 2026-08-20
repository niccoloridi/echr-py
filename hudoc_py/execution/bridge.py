"""Link HUDOC judgments and HUDOC-EXEC records by application number."""

from __future__ import annotations

from pydantic import BaseModel

from ..models import Case, ExecutionCase


class LinkedCase(BaseModel):
    appno: str
    judgment: Case | None = None
    execution: ExecutionCase | None = None


async def link_application(
    appno: str, *, with_judgment_text: bool = False, with_execution_documents: bool = True
) -> LinkedCase:
    """Fetch both database records for an application number."""
    from .. import _aio as main_aio
    from . import aio as exec_aio

    judgment = await main_aio.fetch_case(appno=appno, with_text=with_judgment_text)
    execution = await exec_aio.fetch_case(appno, with_documents=with_execution_documents)
    return LinkedCase(appno=appno, judgment=judgment, execution=execution)


async def link_applications(
    appnos: list[str], *, with_judgment_text: bool = False
) -> list[LinkedCase]:
    """Link several applications sequentially to respect public endpoints."""
    return [
        await link_application(appno, with_judgment_text=with_judgment_text) for appno in appnos
    ]


__all__ = ["LinkedCase", "link_application", "link_applications"]
