from __future__ import annotations

from hudoc_py.citations import (
    build_historical_catalog,
    load_historical_catalog,
    verify_historical_catalog,
    write_historical_catalog,
)
from hudoc_py.citations.models import CitationAuthority, CitationAuthorityEntry
from hudoc_py.citations.reporter import parse_reporter


def test_historical_catalog_is_deterministic_and_checksum_verified(tmp_path):
    reporter = parse_reporter("Series A no. 20")
    assert reporter is not None
    authority = CitationAuthority(
        source_url="https://example.test/official",
        updated_through="2026-07-10",
        source_sha256="abc",
        entries=[
            CitationAuthorityEntry(
                entry_id="one",
                citation="Example v. State, Series A no. 20",
                normalized_citation="example",
                title="Example v. State",
                normalized_title="EXAMPLE v. STATE",
                reporter=reporter,
            )
        ],
    )

    first = build_historical_catalog(authority=authority)
    second = build_historical_catalog(authority=authority)
    path = write_historical_catalog(first, tmp_path / "catalog.json")
    loaded = load_historical_catalog(path)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert verify_historical_catalog(loaded)
    assert loaded.entries[0].reporter_key == "SERIES_A:::20::"
