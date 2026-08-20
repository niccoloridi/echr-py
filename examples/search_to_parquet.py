"""Run a HUDOC search and persist the results to a Parquet file for analysis.

Run::

    python examples/search_to_parquet.py
"""

from __future__ import annotations

from pathlib import Path

from hudoc_py import search


def main() -> int:
    results = search(
        article="3",
        respondent="ITA",
        date_from="2020-01-01",
        limit=200,
    )
    df = results.to_dataframe()
    out = Path("article3_italy_since_2020.parquet").resolve()
    df.to_parquet(out, index=False)
    print(f"Wrote {len(df)} rows to {out}")
    print(df[["itemid", "docname", "kp_date", "articles", "importance"]].head())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
