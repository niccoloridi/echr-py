"""Tests for French rescue."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from hudoc_py.bilingual import (
    apply_rescue_mapping,
    export_rescue_csv,
    rescue_candidates,
    rescue_french,
)
from hudoc_py.main.client import AsyncHudocClient
from hudoc_py.models import Case, CaseCollection


class FakeRescueClient(AsyncHudocClient):
    """Client whose search() returns rows keyed by the appno in the query.

    ``appno_rows`` maps an application number to the list of raw ``columns``
    dicts HUDOC would return for it.
    """

    def __init__(self, appno_rows: dict[str, list[dict[str, Any]]]):
        super().__init__(rate_limit_seconds=0.0)
        self.appno_rows = appno_rows
        self.queried_appnos: list[str] = []

    async def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        # Rows live on the first page only; later pages are empty so pagination
        # terminates (as the real endpoint does).
        if params.get("start", 0) != 0:
            return {"results": []}
        query = params.get("query", "")
        m = re.search(r'appno:"([^"]+)"', query)
        appno = m.group(1) if m else ""
        if appno:
            self.queried_appnos.append(appno)
        rows = self.appno_rows.get(appno, [])
        return {"results": [{"columns": r} for r in rows]}


def _eng(itemid, appno, *, placeholder=True) -> Case:
    return Case.model_validate(
        {
            "itemid": itemid,
            "languageisocode": "ENG",
            "appno": appno,
            "isplaceholder": "TRUE" if placeholder else "FALSE",
        }
    )


def _fre_row(itemid, appno, doctype="HFJUD", placeholder="FALSE") -> dict[str, Any]:
    return {
        "itemid": itemid,
        "languageisocode": "FRE",
        "appno": appno,
        "doctype": doctype,
        "isplaceholder": placeholder,
    }


def test_rescue_candidates_filters():
    coll = CaseCollection(
        [
            _eng("e1", "1/00"),  # placeholder, has appno → candidate
            _eng("e2", ""),  # no appno → skip
            Case.model_validate(  # already has french_itemid → skip
                {"itemid": "e3", "appno": "3/00", "isplaceholder": "TRUE",
                 "french_itemid": "f3"}
            ),
            Case.model_validate(  # has text → skip
                {"itemid": "e4", "appno": "4/00", "text": "body"}
            ),
        ]
    )
    cands = rescue_candidates(coll)
    assert [c.itemid for c in cands] == ["e1"]


def test_compound_appno_tried_in_order(tmp_path):
    coll = CaseCollection([_eng("e1", "1/00;2/00")])
    client = FakeRescueClient(
        {
            "1/00": [],  # first appno: no sibling
            "2/00": [_fre_row("f1", "2/00")],  # second appno: sibling found
        }
    )
    ckpt = tmp_path / "rescue.jsonl"
    stats = asyncio.run(
        rescue_french(coll, checkpoint_path=ckpt, client=client, concurrency=1)
    )
    assert stats.matched == 1
    assert client.queried_appnos == ["1/00", "2/00"]  # order preserved
    assert coll[0].french_itemid == "f1"


def test_hfado_sibling_accepted(tmp_path):
    coll = CaseCollection([_eng("e1", "1/00")])
    client = FakeRescueClient({"1/00": [_fre_row("f1", "1/00", doctype="HFADO")]})
    ckpt = tmp_path / "rescue.jsonl"
    stats = asyncio.run(rescue_french(coll, checkpoint_path=ckpt, client=client))
    assert stats.matched == 1
    assert coll[0].french_itemid == "f1"


def test_placeholder_sibling_skipped(tmp_path):
    coll = CaseCollection([_eng("e1", "1/00")])
    client = FakeRescueClient(
        {
            "1/00": [
                _fre_row("fp", "1/00", placeholder="TRUE"),  # placeholder → skip
                _fre_row("f1", "1/00", placeholder="FALSE"),  # real → accept
            ]
        }
    )
    ckpt = tmp_path / "rescue.jsonl"
    asyncio.run(rescue_french(coll, checkpoint_path=ckpt, client=client))
    assert coll[0].french_itemid == "f1"


def test_self_itemid_excluded(tmp_path):
    coll = CaseCollection([_eng("e1", "1/00")])
    # The only "sibling" HUDOC returns is the English case itself → no match.
    client = FakeRescueClient({"1/00": [_fre_row("e1", "1/00")]})
    ckpt = tmp_path / "rescue.jsonl"
    stats = asyncio.run(rescue_french(coll, checkpoint_path=ckpt, client=client))
    assert stats.no_sibling == 1
    assert coll[0].french_itemid is None


def test_resume_skips_processed(tmp_path):
    coll = CaseCollection([_eng("e1", "1/00")])
    client = FakeRescueClient({"1/00": [_fre_row("f1", "1/00")]})
    ckpt = tmp_path / "rescue.jsonl"
    asyncio.run(rescue_french(coll, checkpoint_path=ckpt, client=client))
    # Second run: e1 already processed → not re-queried.
    client2 = FakeRescueClient({"1/00": [_fre_row("f1", "1/00")]})
    coll2 = CaseCollection([_eng("e1", "1/00")])
    stats = asyncio.run(rescue_french(coll2, checkpoint_path=ckpt, client=client2))
    assert stats.attempted == 0
    assert stats.skipped_resume == 1
    assert client2.queried_appnos == []
    # But the mapping still applies from the existing checkpoint.
    assert coll2[0].french_itemid == "f1"


def test_retry_errors_reattempts_failed(tmp_path):
    from hudoc_py.utils.jsonl import append_jsonl

    ckpt = tmp_path / "rescue.jsonl"
    append_jsonl(ckpt, {"itemid": "e1", "status": "error", "error": "boom"})
    coll = CaseCollection([_eng("e1", "1/00")])
    client = FakeRescueClient({"1/00": [_fre_row("f1", "1/00")]})
    stats = asyncio.run(
        rescue_french(coll, checkpoint_path=ckpt, client=client, retry_errors=True)
    )
    assert stats.attempted == 1  # the error row is retried
    assert coll[0].french_itemid == "f1"


def test_apply_mapping_idempotent_and_non_overwriting(tmp_path):
    from hudoc_py.utils.jsonl import append_jsonl

    ckpt = tmp_path / "rescue.jsonl"
    append_jsonl(ckpt, {"itemid": "e1", "status": "ok", "french_itemid": "f1"})
    append_jsonl(ckpt, {"itemid": "e2", "status": "ok", "french_itemid": "f2"})
    coll = CaseCollection(
        [
            _eng("e1", "1/00"),
            Case.model_validate(  # already set → must not be overwritten
                {"itemid": "e2", "appno": "2/00", "french_itemid": "PRE"}
            ),
        ]
    )
    applied = apply_rescue_mapping(coll, ckpt)
    assert applied == 1
    assert coll[0].french_itemid == "f1"
    assert coll[1].french_itemid == "PRE"


def test_export_csv(tmp_path):
    from hudoc_py.utils.jsonl import append_jsonl

    ckpt = tmp_path / "rescue.jsonl"
    append_jsonl(ckpt, {"itemid": "e1", "status": "ok", "french_itemid": "f1", "appno_used": "1/00"})
    append_jsonl(ckpt, {"itemid": "e2", "status": "no_sibling"})
    csv_path = tmp_path / "map.csv"
    n = export_rescue_csv(ckpt, csv_path)
    assert n == 1
    text = csv_path.read_text(encoding="utf-8")
    assert "eng_itemid,french_itemid,appno" in text
    assert "e1,f1,1/00" in text
    assert "e2" not in text  # non-ok rows excluded
