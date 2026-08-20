"""Build an auditable, source-order spine from HUDOC HTML or plain text.

The parser preserves typed source blocks before deriving legal sections. HUDOC
HTML is especially suitable because each Word paragraph is already a ``<p>``
element and its generated CSS retains useful heading evidence.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from typing import Any, Literal, cast

from bs4 import BeautifulSoup, NavigableString, Tag

from ..models.common import (
    BlockType,
    DocumentBlock,
    DocumentFootnote,
    DocumentSpine,
    FootnoteReference,
    InlineTextRun,
    SegmentationDiagnostic,
)

_PARA_NUM_RE = re.compile(r"^\s*(\d{1,4})\.\s+\S")
_CSS_RULE_RE = re.compile(r"\.([A-Za-z0-9_-]+)\s*\{([^}]*)\}")
_CSS_DECL_RE = re.compile(r"([A-Za-z-]+)\s*:\s*([^;]+)")
_FONT_SIZE_RE = re.compile(r"(-?\d+(?:\.\d+)?)pt", re.IGNORECASE)
_BLOCK_RE = re.compile(
    r"\S(?:.*?\S)?(?=(?:\r\r\n|\r?\n[ \t]*\r?\n)|\Z)", re.DOTALL
)
_FOOTNOTE_ANCHOR_RE = re.compile(r"^_?ftn(?P<label>\d+)$", re.IGNORECASE)

_CANONICAL_HEADING_RE = re.compile(
    r"^(?:"
    r"(?:THE\s+)?PROCEDURE|LA\s+PROC[ÉE]DURE|PROC[ÉE]DURE|"
    r"FACTS\s+AND\s+PROCEDURE|PROCEDURE\s+AND\s+FACTS|"
    r"PROC[ÉE]DURE\s+ET\s+FAITS|FAITS\s+ET\s+PROC[ÉE]DURE|"
    r"PROCEEDINGS\s+BEFORE\s+THE\s+COMMISSION(?:\s+AND\s+THE\s+COURT)?|"
    r"(?:AS\s+TO\s+)?THE\s+FACTS|EN\s+FAIT|(?:LES\s+)?FAITS|"
    r"(?:THE\s+)?COMPLAINTS?|(?:LES\s+)?GRIEFS?|"
    r"(?:AS\s+TO\s+)?THE\s+LAW|EN\s+DROIT|SUR\s+LE\s+DROIT|"
    r"QUESTIONS?\s+TO\s+THE\s+PARTIES|QUESTIONS?\s+AUX\s+PARTIES|"
    r"SUBJECT\s+MATTER\s+OF\s+THE\s+CASE|OBJET\s+DE\s+L[’']AFFAIRE|"
    r"THE\s+COURT[’']S\s+ASSESSMENT|"
    r"FOR\s+THESE\s+REASONS(?:,?\s+(?:THE\s+COURT|THE\s+COMMISSION))?"
    r"(?:,?\s+(?:UNANIMOUSLY|BY\s+\w+.*))?,?|"
    r"ON\s+THESE\s+GROUNDS(?:,?\s+THE\s+COURT)?,?|"
    r"PAR\s+CES\s+MOTIFS(?:,?\s+(?:LA\s+COUR|LA\s+COMMISSION))?"
    r"(?:,?\s+(?:A\s+L[’']UNANIMITE|À\s+L[’']UNANIMITÉ|À\s+LA\s+MAJORITÉ))?,?|"
    r"POUR\s+CES\s+MOTIFS(?:,?\s+(?:LA\s+COUR|LA\s+COMMISSION))?,?|"
    r"APPENDIX(?:ES)?|ANNEX(?:ES|E)?|"
    r"SEPARATE\s+OPINIONS?|OPINIONS?\s+S[ÉE]PAR[ÉE]ES?"
    r")$",
    re.IGNORECASE,
)
_ROMAN_HEADING_RE = re.compile(r"^(?:I{1,3}|IV|V|VI{0,3}|IX|X)\.\s+\S")
_LETTER_HEADING_RE = re.compile(r"^[A-Z]\.\s+\S")
_PAREN_HEADING_RE = re.compile(r"^\((?:[a-z]|[ivxlcdm]+)\)\s+\S", re.IGNORECASE)
_OPINION_HEADING_RE = re.compile(
    r"^(?:"
    r"(?:JOINT\s+)?(?:PARTLY|PARTIALLY)\s+(?:CONCURRING|DISSENTING),\s*"
    r"(?:PARTLY|PARTIALLY)\s+(?:CONCURRING|DISSENTING)\s+OPINION|"
    r"(?:JOINT\s+)?(?:PARTLY\s+|PARTIALLY\s+)?(?:CONCURRING|DISSENTING)\s+OPINION|"
    r"SEPARATE\s+OPINION|DECLARATION\s+OF\s+JUDGE|"
    r"OPINION\s+(?:COMMUNE\s+)?(?:EN\s+PARTIE\s+|PARTIELLEMENT\s+)?"
    r"(?:CONCORDANTE|DISSIDENTE|S[ÉE]PAR[ÉE]E)|"
    r"D[ÉE]CLARATION\s+(?:DU|DE\s+LA)\s+JUGE"
    r")\b",
    re.IGNORECASE,
)


def _parse_styles(html: str) -> dict[str, dict[str, str]]:
    """Parse the small generated CSS subset used by HUDOC's DOCX converter."""
    styles: dict[str, dict[str, str]] = {}
    for class_name, declarations in _CSS_RULE_RE.findall(html):
        styles[class_name] = {
            key.strip().lower(): value.strip() for key, value in _CSS_DECL_RE.findall(declarations)
        }
    return styles


def _combined_style(classes: Iterable[str], styles: dict[str, dict[str, str]]) -> dict[str, str]:
    combined: dict[str, str] = {}
    for class_name in classes:
        combined.update(styles.get(class_name, {}))
    return combined


def _inline_style(value: object) -> dict[str, str]:
    return {
        key.strip().lower(): item.strip()
        for key, item in _CSS_DECL_RE.findall(str(value or ""))
    }


def _element_text(element: Tag) -> str:
    """Return rendered inline text without inventing tag-boundary spaces.

    BeautifulSoup's ``separator=" "`` inserts a space between every text
    node.  In Word-derived HUDOC HTML that turns ``(<em>re Crimea</em>)`` into
    ``( re Crimea )`` and breaks both exact offsets and citation envelopes.
    Collapsing whitespace after concatenating the source nodes preserves
    whitespace that is actually present in the document while keeping inline
    punctuation attached to the styled text it encloses.
    """
    return " ".join(element.get_text(separator="", strip=False).split())


def _style_flags(node: NavigableString, root: Tag, styles: dict[str, dict[str, str]]) -> tuple[bool, bool]:
    """Return inherited bold/italic flags without promoting them to block style."""
    bold = italic = False
    parent = node.parent
    while isinstance(parent, Tag):
        tag = (parent.name or "").lower()
        bold = bold or tag in {"b", "strong"}
        italic = italic or tag in {"em", "i"}
        style = _combined_style(parent.get("class") or [], styles)
        style.update(_inline_style(parent.get("style")))
        weight = style.get("font-weight", "").lower()
        bold = bold or weight in {"bold", "600", "700", "800", "900"}
        italic = italic or style.get("font-style", "").lower() in {"italic", "oblique"}
        if parent is root:
            break
        parent = parent.parent
    return bold, italic


def _dom_text_ranges(element: Tag, text: str) -> list[tuple[NavigableString, int, int]]:
    """Map each DOM text node onto the canonical block text in source order."""
    found: list[tuple[NavigableString, int, int]] = []
    cursor = 0
    for node in element.descendants:
        if not isinstance(node, NavigableString) or node.parent is None:
            continue
        value = " ".join(str(node).split())
        if not value:
            continue
        start = text.find(value, cursor)
        if start < 0:
            start = text.find(value)
        if start < 0:
            continue
        end = start + len(value)
        found.append((node, start, end))
        cursor = end
    return found


def _inline_runs(element: Tag, text: str, styles: dict[str, dict[str, str]]) -> list[InlineTextRun]:
    """Map DOM text nodes back onto the existing normalized block text."""
    found: list[InlineTextRun] = []
    for node, start, end in _dom_text_ranges(element, text):
        bold, italic = _style_flags(node, element, styles)
        found.append(InlineTextRun(text=text[start:end], start=start, end=end, bold=bold, italic=italic))

    # Fill separator/punctuation gaps so runs form a complete, auditable view.
    complete: list[InlineTextRun] = []
    cursor = 0
    for run in found:
        if run.start > cursor:
            complete.append(InlineTextRun(text=text[cursor:run.start], start=cursor, end=run.start))
        complete.append(run)
        cursor = run.end
    if cursor < len(text):
        complete.append(InlineTextRun(text=text[cursor:], start=cursor, end=len(text)))
    return complete


def _footnote_anchor(value: object) -> tuple[str, str] | None:
    raw = str(value or "").lstrip("#")
    match = _FOOTNOTE_ANCHOR_RE.fullmatch(raw)
    if not match:
        return None
    label = match.group("label")
    return f"ftn{label}", label


def _footnote_references(element: Tag, text: str) -> list[FootnoteReference]:
    references: list[FootnoteReference] = []
    node_ranges = _dom_text_ranges(element, text)
    for anchor in element.find_all("a", href=True):
        parsed = _footnote_anchor(anchor.get("href"))
        if parsed is None:
            continue
        footnote_id, default_label = parsed
        printed = _element_text(anchor)
        label = printed.strip("[]() ") or default_label
        anchor_ranges = [
            (start, end)
            for node, start, end in node_ranges
            if node is anchor or anchor in node.parents
        ]
        if not anchor_ranges:
            continue
        start = anchor_ranges[0][0]
        end = anchor_ranges[-1][1]
        references.append(FootnoteReference(
            footnote_id=footnote_id,
            label=label,
            start=start,
            end=end,
            target_anchor=str(anchor.get("href")),
        ))
    return references


def _font_size(style: dict[str, str]) -> float | None:
    match = _FONT_SIZE_RE.search(style.get("font-size", ""))
    return float(match.group(1)) if match else None


def _uppercase_ratio(text: str) -> float:
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return 0.0
    return sum(char.isupper() for char in letters) / len(letters)


def _outline_title_starts_upper(text: str) -> bool:
    remainder = re.sub(
        r"^(?:[IVXLCDM]+\.|[A-Z]\.|\((?:[a-z]|[ivxlcdm]+)\))\s+",
        "",
        text,
        count=1,
        flags=re.IGNORECASE,
    )
    return bool(remainder and remainder[0].isupper())


def _heading_evidence(
    text: str,
    *,
    source_tag: str | None,
    style: dict[str, str],
) -> tuple[int | None, list[str]]:
    """Return a conservative heading level and the evidence supporting it."""
    clean = " ".join(text.split())
    evidence: list[str] = []
    level: int | None = None

    if source_tag and re.fullmatch(r"h[1-6]", source_tag):
        level = int(source_tag[1])
        evidence.append("native_html_heading")
    if _CANONICAL_HEADING_RE.fullmatch(clean):
        level = level or 1
        evidence.append("canonical_ecthr_heading")
    if re.match(r"^(?:APPENDIX(?:ES)?|ANNEX(?:ES|E)?)\s*\n", text, re.IGNORECASE):
        level = level or 1
        evidence.append("appendix_heading_with_subtitle")
    if (
        len(clean) <= 180
        and _uppercase_ratio(clean) >= 0.95
        and len(clean.split()) >= 2
        and not clean.endswith(",")
    ):
        level = level or 2
        evidence.append("short_all_caps")
    if style.get("font-weight", "").lower() in {"bold", "600", "700", "800", "900"}:
        evidence.append("source_bold")
    size = _font_size(style)
    if size is not None and size >= 13.5:
        level = level or 2
        evidence.append("source_large_font")
    if style.get("text-align", "").lower() == "center":
        evidence.append("source_centered")
    if _OPINION_HEADING_RE.match(clean):
        level = level or 1
        evidence.append("opinion_heading_grammar")
    if _ROMAN_HEADING_RE.match(clean) and (
        _uppercase_ratio(clean) >= 0.7 or not clean.endswith((".", ";", ","))
    ):
        level = level or 2
        evidence.append("roman_outline_marker")
    elif (
        _LETTER_HEADING_RE.match(clean)
        and len(clean) <= 120
        and not clean.endswith((".", ";", ",", ":"))
        and _outline_title_starts_upper(clean)
    ):
        level = level or 3
        evidence.append("letter_outline_marker")
    elif (
        _PAREN_HEADING_RE.match(clean)
        and len(clean) <= 120
        and not clean.endswith((".", ";", ",", ":"))
        and _outline_title_starts_upper(clean)
    ):
        level = level or 4
        evidence.append("parenthesized_outline_marker")

    # Formatting alone is insufficient. A bold name or centred date in the
    # cover material remains a paragraph unless another structural signal is
    # present.
    if (
        level is None
        and {"source_bold", "source_large_font"} <= set(evidence)
        and len(clean) <= 140
        and not clean.endswith((".", ";"))
    ):
        level = 2
        evidence.append("combined_source_formatting")
    return level, evidence


def _make_blocks(
    records: list[dict[str, Any]],
    *,
    document_id: str | None,
    source_format: Literal["hudoc_html", "plain_text"],
) -> DocumentSpine:
    blocks: list[DocumentBlock] = []
    cursor = 0
    for index, record in enumerate(records):
        text = str(record["text"])
        start = int(record.get("char_start", cursor))
        end = int(record.get("char_end", start + len(text)))
        source_tag = record.get("source_tag")
        style = dict(record.get("source_style", {}))
        is_footnote = record.get("type") == "footnote"
        heading_level, evidence = (None, []) if is_footnote else _heading_evidence(
            text, source_tag=str(source_tag) if source_tag else None, style=style
        )
        para_match = None if is_footnote else _PARA_NUM_RE.match(text)
        block_type = cast(
            BlockType, "heading" if heading_level is not None else str(record["type"])
        )
        if heading_level is None and (
            _PAREN_HEADING_RE.match(text) or text.lstrip().startswith(("- ", "– ", "\u2014 "))
        ):
            block_type = "list_item"
        blocks.append(
            DocumentBlock(
                block_id=f"b{index + 1:06d}",
                type=block_type,
                text=text,
                char_start=start,
                char_end=end,
                para_num=int(para_match.group(1)) if para_match else None,
                heading_level=heading_level,
                heading_source=evidence,
                source_tag=str(source_tag) if source_tag else None,
                source_classes=list(record.get("source_classes", [])),
                source_style=style,
                inline_runs=list(record.get("inline_runs", [])),
                footnote_id=record.get("footnote_id"),
                footnote_references=list(record.get("footnote_references", [])),
            )
        )
        cursor = end + 2

    # A short title-like block immediately before a numbered paragraph is a
    # useful secondary heading signal in HUDOC output. Terminal punctuation is
    # required to be absent so ordinary prose is not promoted.
    for index, block in enumerate(blocks[:-1]):
        if block.type != "paragraph" or blocks[index + 1].para_num is None:
            continue
        clean = " ".join(block.text.split())
        if (
            1 <= len(clean.split()) <= 12
            and len(clean) <= 120
            and not clean.endswith((".", ",", ";", ":", "?", "!"))
            and _PARA_NUM_RE.match(clean) is None
        ):
            block.type = "heading"
            block.heading_level = 3
            block.heading_source.append("followed_by_numbered_paragraph")

    first_substantive = next(
        (index for index, block in enumerate(blocks) if block.para_num is not None),
        None,
    )
    pre_count = unnumbered_count = 0
    number_counts: Counter[int] = Counter()
    footnote_counts: Counter[str] = Counter()
    diagnostics: list[SegmentationDiagnostic] = []
    for index, block in enumerate(blocks):
        if block.type == "heading":
            block.heading_role = (
                "frontmatter"
                if first_substantive is None or index < first_substantive
                else "subsection"
            )
            continue
        if block.type == "footnote":
            local_id = block.footnote_id or str(index + 1)
            footnote_counts[local_id] += 1
            suffix = f"-{footnote_counts[local_id]}" if footnote_counts[local_id] > 1 else ""
            block.para_id = f"fn-{local_id}{suffix}"
            continue
        if block.para_num is not None:
            number_counts[block.para_num] += 1
            suffix = (
                f"-{number_counts[block.para_num]}" if number_counts[block.para_num] > 1 else ""
            )
            block.para_id = f"{block.para_num}{suffix}"
            if suffix:
                diagnostics.append(
                    SegmentationDiagnostic(
                        code="duplicate_paragraph_number",
                        severity="info",
                        message=(
                            f"Paragraph number {block.para_num} occurs more than once; "
                            "the later local ID was suffixed."
                        ),
                        block_index=index,
                        char_start=block.char_start,
                        char_end=block.char_end,
                    )
                )
        elif first_substantive is not None and index < first_substantive:
            pre_count += 1
            block.para_id = f"pre-{pre_count:03d}"
        else:
            unnumbered_count += 1
            block.para_id = f"u-{unnumbered_count:03d}"

    # HUDOC sometimes serialises one numbered legal paragraph as several
    # adjacent HTML ``<p>`` elements.  Keep each physical block and its local
    # ``para_id`` intact, while giving consecutive unnumbered paragraph blocks
    # the legal address of the preceding numbered paragraph.  A heading, a
    # non-paragraph block, or the next numbered paragraph is a hard boundary.
    legal_para_id: str | None = None
    legal_para_num: int | None = None
    for block in blocks:
        if block.type == "paragraph" and block.para_num is not None:
            legal_para_id = block.para_id
            legal_para_num = block.para_num
            block.legal_para_id = legal_para_id
            block.legal_para_num = legal_para_num
        elif block.type == "paragraph" and legal_para_id is not None:
            block.legal_para_id = legal_para_id
            block.legal_para_num = legal_para_num
        else:
            legal_para_id = None
            legal_para_num = None

    return DocumentSpine(
        document_id=document_id,
        source_format=source_format,
        blocks=blocks,
        first_substantive_block_index=first_substantive,
        diagnostics=diagnostics,
    )


def build_spine_from_html(html: str, *, document_id: str | None = None) -> DocumentSpine:
    """Build a spine from HUDOC HTML without discarding source structure."""
    soup = BeautifulSoup(html, "lxml")
    styles = _parse_styles(html)
    for element in soup(["style", "script"]):
        element.decompose()

    records: list[dict[str, Any]] = []
    cursor = 0
    for element in soup.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote"]):
        text = _element_text(element)
        if not text:
            continue
        classes: list[str] = []
        for node in [element, *element.find_all(True)]:
            for class_name in node.get("class") or []:
                if class_name not in classes:
                    classes.append(class_name)
        # Descendant classes remain available as provenance, but only the
        # block element's own style may classify the whole paragraph.
        style = _combined_style(element.get("class") or [], styles)
        style.update(_inline_style(element.get("style")))
        footnote_container = element.find_parent(
            id=lambda value: _footnote_anchor(value) is not None
        )
        parsed_footnote = (
            _footnote_anchor(footnote_container.get("id"))
            if isinstance(footnote_container, Tag)
            else None
        )
        block_type = (
            "footnote"
            if parsed_footnote is not None
            else
            "list_item"
            if element.name == "li"
            else "blockquote"
            if element.name == "blockquote"
            else "paragraph"
        )
        records.append(
            {
                "text": text,
                "type": block_type,
                "char_start": cursor,
                "char_end": cursor + len(text),
                "source_tag": element.name,
                "source_classes": classes,
                "source_style": style,
                "inline_runs": _inline_runs(element, text, styles),
                "footnote_id": parsed_footnote[0] if parsed_footnote else None,
                "footnote_references": _footnote_references(element, text),
            }
        )
        cursor += len(text) + 2
    spine = _make_blocks(records, document_id=document_id, source_format="hudoc_html")
    references: dict[str, list[str]] = {}
    for block in spine.blocks:
        for reference in block.footnote_references:
            references.setdefault(reference.footnote_id, []).append(block.block_id)
    body_groups: dict[str, list[DocumentBlock]] = {}
    for block in spine.blocks:
        if block.type == "footnote" and block.footnote_id is not None:
            body_groups.setdefault(block.footnote_id, []).append(block)
    for footnote_id, block_ids in references.items():
        if footnote_id not in body_groups:
            source = next(block for block in spine.blocks if block.block_id == block_ids[0])
            spine.diagnostics.append(SegmentationDiagnostic(
                code="footnote_body_missing",
                severity="warning",
                message=f"Footnote reference {footnote_id} has no body.",
                block_index=spine.blocks.index(source),
            ))
    footnotes: list[DocumentFootnote] = []
    for footnote_id, bodies in body_groups.items():
        label = footnote_id.removeprefix("ftn")
        invoking_ids = list(dict.fromkeys(references.get(footnote_id, [])))
        invoking_blocks = [
            block for block in spine.blocks if block.block_id in set(invoking_ids)
        ]
        invoking_para_ids = list(dict.fromkeys(
            para_id
            for block in invoking_blocks
            if (para_id := block.legal_para_id or block.para_id) is not None
        ))
        for block in bodies:
            block.referenced_by_block_ids = invoking_ids
            block.referenced_by_para_ids = invoking_para_ids
        first_text = re.sub(
            rf"^\s*\[?{re.escape(label)}\]?[.)]?\s*", "", bodies[0].text, count=1
        )
        body_text = "\n\n".join([first_text, *(block.text for block in bodies[1:])])
        if not invoking_ids:
            spine.diagnostics.append(SegmentationDiagnostic(
                code="footnote_reference_missing",
                severity="warning",
                message=f"Footnote body {label} has no inline reference.",
                block_index=spine.blocks.index(bodies[0]),
            ))
        footnotes.append(DocumentFootnote(
            footnote_id=footnote_id,
            label=label,
            text=body_text,
            body_block_id=bodies[0].block_id,
            body_block_ids=[block.block_id for block in bodies],
            reference_block_ids=invoking_ids,
            reference_para_ids=invoking_para_ids,
        ))
    spine.footnotes = footnotes
    return spine


def link_footnote_context(spine: DocumentSpine) -> None:
    """Attach a footnote body to its unique invoking section/opinion context."""
    by_id = {block.block_id: block for block in spine.blocks}
    for footnote in spine.footnotes:
        bodies = [
            by_id[value]
            for value in footnote.body_block_ids or [footnote.body_block_id]
            if value in by_id
        ]
        references = [by_id[value] for value in footnote.reference_block_ids if value in by_id]
        if not bodies or not references:
            continue
        contexts = {
            (
                reference.section,
                reference.opinion_id,
                reference.opinion_ordinal,
                reference.opinion_type,
                tuple(reference.opinion_authors),
                tuple(reference.opinion_joined_by),
            )
            for reference in references
        }
        if len(contexts) != 1:
            spine.diagnostics.append(SegmentationDiagnostic(
                code="footnote_multiple_source_contexts",
                severity="warning",
                message=f"Footnote {footnote.label} is invoked from multiple contexts.",
                block_index=spine.blocks.index(bodies[0]),
            ))
            # A physically terminal note may otherwise inherit the last
            # opinion's metadata even when it is also invoked by the majority.
            # Preserve every backlink, but keep the source component neutral.
            for body in bodies:
                body.section = None
                body.opinion_id = None
                body.opinion_ordinal = None
                body.opinion_type = None
                body.opinion_authors = []
                body.opinion_joined_by = []
            continue
        section, opinion_id, ordinal, opinion_type, authors, joined_by = contexts.pop()
        for body in bodies:
            body.section = section
            body.opinion_id = opinion_id
            body.opinion_ordinal = ordinal
            body.opinion_type = opinion_type
            body.opinion_authors = list(authors)
            body.opinion_joined_by = list(joined_by)


def build_spine_from_text(text: str, *, document_id: str | None = None) -> DocumentSpine:
    """Build a best-effort spine from blank-line-delimited plain text."""
    records: list[dict[str, Any]] = []
    # Ignore terminal whitespace for matching purposes. Without this, a final
    # paragraph followed by a newline does not satisfy the regex's ``\Z``
    # branch and silently disappears from the spine.
    for match in _BLOCK_RE.finditer(text.rstrip()):
        block_text = match.group(0)
        records.append(
            {
                "text": block_text,
                "type": "paragraph",
                "char_start": match.start(),
                "char_end": match.end(),
            }
        )
    return _make_blocks(records, document_id=document_id, source_format="plain_text")


def spine_text(spine: DocumentSpine) -> str:
    """Reconstruct the canonical plain-text view used by HTML segmentation."""
    return "\n\n".join(block.text for block in spine.blocks)
