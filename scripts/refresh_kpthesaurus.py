"""Refresh the vendored ECHR keypoint (kpthesaurus) label map.

HUDOC stores each case's keywords as numeric ``kpthesaurus`` IDs. This script
pulls the canonical English keyword taxonomy from HUDOC's OData Taxonomy
endpoint and writes it to ``hudoc_py/data/kpthesaurus_eng.json`` as an
``{id: {"label", "parent"}}`` map. Re-run when the Court revises the thesaurus.

    python scripts/refresh_kpthesaurus.py

The endpoint (discovered from HUDOC's frontend) is::

    /app/odata/resources?endpoint=Taxonomy
        &select=TermParent,TermName,TermValue,ItemOrder,Site
        &filter=Site[ECHR] and (Title eq 'ECHR Keywords')
        &order=siteDesc

which returns every keyword in ~10 languages; ``Site == "echreng"`` is English.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

ODATA = "https://hudoc.echr.coe.int/app/odata/resources"
OUT = Path(__file__).resolve().parent.parent / "hudoc_py" / "data" / "kpthesaurus_eng.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://hudoc.echr.coe.int/eng",
}


def fetch(site: str = "echreng") -> dict[str, dict]:
    select = "TermParent,TermName,TermValue,ItemOrder,Site"
    filt = "Site[ECHR] and (Title eq 'ECHR Keywords')"
    url = (
        f"{ODATA}?endpoint=Taxonomy&select={urllib.parse.quote(select)}"
        f"&filter={urllib.parse.quote(filt)}&order=siteDesc"
    )
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=90) as r:
        rows = json.loads(r.read())

    out: dict[str, dict] = {}
    for row in sorted((r for r in rows if r.get("Site") == site),
                      key=lambda r: r.get("ItemOrder", 0)):
        tid = str(row["TermValue"]).strip()
        parent = str(row.get("TermParent") or "").strip() or None
        out[tid] = {"label": row["TermName"].strip(), "parent": parent}
    return out


def main() -> int:
    keypoints = fetch()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(keypoints, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(keypoints)} keypoints → {OUT}")
    # Spot-check a few well-known IDs.
    for tid in ("350", "445", "449", "451"):
        print(f"  {tid}: {keypoints.get(tid, {}).get('label', '<missing>')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
