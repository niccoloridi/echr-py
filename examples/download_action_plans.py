"""Find action plans for a state and download their text to disk.

Run::

    python examples/download_action_plans.py ITA
"""

from __future__ import annotations

import sys
from pathlib import Path

from hudoc_py.execution import fetch_text, search_documents


def main(state: str) -> int:
    docs = search_documents(collection="acp", state=state, limit=20)
    out_dir = Path(f"action_plans_{state}").resolve()
    out_dir.mkdir(exist_ok=True)

    print(f"Found {len(docs)} action plans for {state}; downloading to {out_dir}")
    for d in docs:
        # The HUDOC-EXEC HTML endpoint looks up by content_store_id (storage
        # UUID), not by execidentifier. Skip docs without one (rare).
        if not d.content_store_id:
            continue
        text = fetch_text(d.content_store_id, format="text")
        if text is None:
            print(f"  skip: {d.execidentifier} (no body)")
            continue
        safe = (d.execidentifier or d.content_store_id).replace("/", "_")
        path = out_dir / f"{safe}.txt"
        path.write_text(text, encoding="utf-8")
        print(f"  {d.execidentifier} -> {path.name} ({len(text):,} chars)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python examples/download_action_plans.py <STATE_CODE>", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
