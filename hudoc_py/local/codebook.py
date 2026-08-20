"""Auto-generate a CODEBOOK.md documenting the corpus tables' variables.

Adapted from cjeu-py's ``codebook.py``. Field definitions are hand-authored
(descriptions and enumerated values are domain knowledge the model can't
provide); the ``cases`` skeleton is seeded from the :class:`Case` model so new
model fields surface as "(undocumented)" until annotated here.
"""

from __future__ import annotations

from pathlib import Path

# table -> list of (variable, type, description, values-or-None)
CODEBOOK: dict[str, list[tuple[str, str, str, list[str] | None]]] = {
    "cases": [
        ("itemid", "text", "HUDOC document identifier (universal join key).", None),
        ("ecli", "text", "European Case Law Identifier.", None),
        ("appno", "list", "Application number(s).", None),
        ("docname", "text", "Case title, e.g. 'CASE OF X v. COUNTRY'.", None),
        (
            "doctype",
            "text",
            "HUDOC document type.",
            ["HEJUD", "HEDEC", "HEADO", "HFJUD", "HFDEC", "HFADO"],
        ),
        ("doctype_branch", "text", "Bench composition.", ["GRANDCHAMBER", "CHAMBER", "COMMITTEE"]),
        ("respondent", "list", "Respondent state ISO code(s).", None),
        ("articles", "list", "Convention articles invoked.", None),
        ("conclusion", "text", "Outcome text, e.g. 'Violation of Article 3'.", None),
        ("importance", "text", "Case-importance level (1 highest → 4).", ["1", "2", "3", "4"]),
        ("kp_date", "date", "Keypoint (judgment/decision) date, ISO format.", None),
        ("language", "text", "HUDOC three-letter document-language code.", None),
        ("represented_by", "text", "Applicant's representative(s) as printed.", None),
        ("is_placeholder", "bool", "True if this row has no downloadable text.", None),
        ("french_itemid", "text", "itemid of the French sibling (set by reconcile/rescue).", None),
        ("text_source_itemid", "text", "Document the loaded text came from.", None),
        ("text_source_language", "text", "Language of the loaded text.", None),
    ],
    "texts": [
        ("itemid", "text", "Case identifier (join key to cases).", None),
        ("source_itemid", "text", "Document the text was fetched from.", None),
        ("source_language", "text", "Language of the fetched text.", None),
        ("format", "text", "Text rendering.", ["text", "md", "html"]),
        ("text", "text", "Full document body.", None),
    ],
    "citation_mentions": [
        ("mention_id", "text", "Stable source-document and ordinal reference identifier.", None),
        ("reference_hash", "text", "Reusable normalized-reference hash.", None),
        ("raw_ref", "text", "Unmodified SCL fragment.", None),
        (
            "status",
            "text",
            "Auditable resolution status.",
            [
                "resolved_identifier",
                "resolved_authority",
                "resolved_metadata",
                "resolved_override",
                "ambiguous_document",
                "unresolved_reference",
                "target_not_in_hudoc",
            ],
        ),
        ("target_node_id", "text", "Canonical ECLI/itemid graph target.", None),
    ],
    "citation_edges": [
        ("source", "text", "Canonical source document node.", None),
        ("target", "text", "Canonical cited document node.", None),
        ("citation_count", "number", "Number of SCL mentions aggregated into the edge.", None),
        ("mention_ids", "list", "Underlying auditable citation mentions.", None),
    ],
}


def _seed_cases_from_model() -> set[str]:
    """Field names on the Case model, to flag any missing from the codebook."""
    from ..models import Case

    return set(Case.model_fields)


def generate_codebook_markdown() -> str:
    documented = {v[0] for v in CODEBOOK["cases"]}
    missing = sorted(_seed_cases_from_model() - documented)

    lines = ["# echr-py Corpus Codebook", ""]
    for table, fields in CODEBOOK.items():
        lines.append(f"## `{table}`")
        lines.append("")
        lines.append("| Variable | Type | Description | Values |")
        lines.append("|---|---|---|---|")
        for name, typ, desc, values in fields:
            if values is None:
                vstr = ""
            elif len(values) <= 6:
                vstr = ", ".join(f"`{v}`" for v in values)
            else:
                vstr = f"{len(values)} categories"
            lines.append(f"| {name} | {typ} | {desc} | {vstr} |")
        lines.append("")

    if missing:
        lines.append("## Undocumented `cases` fields")
        lines.append("")
        lines.append(
            "These exist on the model but lack a codebook entry: "
            + ", ".join(f"`{m}`" for m in missing)
        )
        lines.append("")
    return "\n".join(lines)


def write_codebook(output_path: str | Path) -> Path:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(generate_codebook_markdown(), encoding="utf-8")
    return out
