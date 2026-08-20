"""Fetch metadata and full text for a single ECHR case, then print key fields.

Run::

    python examples/fetch_case.py 46221/99
"""

from __future__ import annotations

import sys

from hudoc_py import fetch_case


def main(appno: str) -> int:
    case = fetch_case(appno=appno, with_text=True, segment=True)
    if case is None:
        print(f"Case {appno} not found", file=sys.stderr)
        return 1

    print(f"=== {case.docname} ===")
    print(f"appno:        {case.appno}")
    print(f"ecli:         {case.ecli}")
    print(f"date:         {case.kp_date}")
    print(f"respondent:   {case.respondent}")
    print(f"articles:     {case.articles}")
    print(f"importance:   {case.importance}")
    print(f"doctype:      {case.doctype}")
    print()
    if case.conclusion:
        print(f"--- Conclusion ---\n{case.conclusion}\n")
    if case.sections and case.sections.dispositif:
        print("--- Dispositif ---")
        print(case.sections.dispositif)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python examples/fetch_case.py <appno>", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
