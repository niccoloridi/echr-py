import asyncio

from hudoc_py.main.client import AsyncHudocClient


def test_fetch_by_itemids_retries_bulk_omissions_and_preserves_order():
    class FakeClient(AsyncHudocClient):
        def __init__(self):
            super().__init__(rate_limit_seconds=0)
            self.queries: list[tuple[str, int]] = []

        async def _fetch_page(self, *, query, start, length, select, sort=""):
            self.queries.append((query, length))
            if length > 1:
                return [{"itemid": "old-1", "docname": "first"}]
            if '"old-2"' in query:
                return [{"itemid": "old-2", "docname": "second"}]
            return []

    client = FakeClient()
    rows = asyncio.run(client.fetch_by_itemids(["old-2", "old-1", "missing"], chunk_size=3))

    assert [row["itemid"] for row in rows] == ["old-2", "old-1"]
    assert [length for _, length in client.queries] == [3, 1, 1]


def test_fetch_by_itemids_deduplicates_requested_ids():
    class FakeClient(AsyncHudocClient):
        async def _fetch_page(self, *, query, start, length, select, sort=""):
            return [{"itemid": "001-1"}]

    rows = asyncio.run(FakeClient(rate_limit_seconds=0).fetch_by_itemids(["001-1", "001-1"]))
    assert rows == [{"itemid": "001-1"}]
