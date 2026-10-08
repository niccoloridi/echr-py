"""Read document structure from a HUDOC DOCX rendition (opt-in, advisory).

HUDOC's HTML is generated from the Registry's Word files and keeps only
hashed formatting classes, so sections, separate opinions, quotations and the
bench have to be reconstructed from heading grammars. The DOCX keeps the
Registry template's named paragraph styles (``Ju_H_Head``, ``Ju_Para``,
``Ju_Quot``, ``Ju_Judges``, ``Opi_H_Head``, ``Opi_Para`` and, in the
2013-2020 template, ``ECHRPara``, ``ECHRHeading1-6``, ``ECHRParaQuote``),
present from the first judgment of 1961 onward. This module records those
labels as :class:`~hudoc_py.models.docx_structure.DocxStructure`.

The layer is advisory. HTML remains the canonical rendition for offsets,
citation loci and benchmarks; nothing here alters an HTML-derived output.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from collections import Counter
from typing import TYPE_CHECKING, cast

from lxml import etree

from ..models.common import CanonicalSection
from ..models.docx_structure import (
    DocxAgreement,
    DocxBlock,
    DocxFootnote,
    DocxOpinion,
    DocxRole,
    DocxSection,
    DocxStructure,
)

if TYPE_CHECKING:
    from ..models import Case

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W = f"{{{W}}}"

#: Registry style ids, normalised (lower-case alphanumerics, trailing digits and
#: ``Char`` repeats stripped), mapped to structural roles. Unknown ids are kept
#: verbatim on the block and counted in ``unmapped_styles``.
ROLE_MAP: dict[str, DocxRole] = {
    # paragraphs
    "jupara": "paragraph",
    "juparalast": "paragraph_last",
    "echrpara": "paragraph",
    "echrdecisionbody": "paragraph",
    "normaljustified": "paragraph",
    # quotations and lists
    "juquot": "quotation",
    "juquotlist": "quotation",
    "juquotsub": "quotation",
    "echrparaquote": "quotation",
    "julist": "list",
    "julista": "list",
    "julisti": "list",
    "julistlist": "list",
    "listparagraph": "list",
    # headings
    "juhhead": "section_heading",
    "juhiroman": "heading",
    "juha": "heading",
    "juh": "heading",
    "juhalpha": "heading",
    "juhi": "heading",
    "echrheading": "heading",
    "echrtitlecentre": "heading",
    "echrtitle": "section_heading",
    "juharticle": "heading_article",
    # front matter
    "jujudges": "judges",
    "jucourt": "court",
    "junames": "names",
    "jusigned": "signed",
    "jusignedright": "signed",
    "juinitialled": "signed",
    "jucase": "case_title",
    "jutitle": "case_title",
    "dechcase": "case_title",
    "echrcovertitle": "case_title",
    "dechtitle": "decision_title",
    # separate opinions
    "opihhead": "opinion_heading",
    "opihopinion": "opinion_heading",
    "opiha": "opinion_subheading",
    "opih": "opinion_subheading",
    "opipara": "opinion_paragraph",
    "opiquot": "opinion_quotation",
    "opitranslation": "opinion_note",
    # Registry summaries (1990s judgments)
    "supara": "summary",
    "suhi": "summary",
    "suha": "summary",
    "suhref": "summary",
    "susummary": "summary",
    "sujudgment": "summary",
    "sucountry": "summary",
    "suconclusion": "summary",
    # apparatus
    "toc": "toc",
    "tocheading": "toc",
    "footnotetext": "footnote_text",
}

#: Sub-sections the HTML segmenter promotes only when they stand on a line of their
#: own; their presence depends on typed versus automatic numbering, so they are
#: listed in the agreement report but do not decide ``sections_agree``.
SUBSECTIONS = frozenset({"subject_matter", "complaints", "court_assessment"})

_NUMBERED = re.compile(r"^\s*(\d{1,4})\.\s")
_ENUMERATION = re.compile(r"^\s*(?:[A-Z]|\d{1,2}|[IVX]{1,5}|[a-z]|\([a-z0-9]{1,3}\))[.)]\s+")


def normalise_style_id(style_id: str | None) -> str:
    """Reduce a Word style id to a template key: ``JuParaCharCharCharChar`` → ``jupara``."""
    if not style_id:
        return ""
    key = re.sub(r"[^a-z0-9]", "", style_id.lower())
    key = re.sub(r"^style", "", key)
    while key.endswith("char"):
        key = key[:-4]
    key = re.sub(r"\d+$", "", key)
    return key


def role_for_style(style_id: str | None) -> DocxRole:
    return ROLE_MAP.get(normalise_style_id(style_id), "other")


def _paragraph_text(paragraph: etree._Element) -> str:
    parts: list[str] = []
    for node in paragraph.iter():
        tag = node.tag
        if tag == f"{_W}t":
            parts.append(node.text or "")
        elif tag in (f"{_W}br", f"{_W}tab", f"{_W}cr"):
            parts.append(" ")
        elif tag == f"{_W}delText":
            continue
    return " ".join("".join(parts).split())


def _footnote_ids(paragraph: etree._Element) -> list[str]:
    return [
        ref.get(f"{_W}id") or ""
        for ref in paragraph.iter(f"{_W}footnoteReference")
        if ref.get(f"{_W}id")
    ]


def _in_table(paragraph: etree._Element) -> bool:
    return any(ancestor.tag == f"{_W}tc" for ancestor in paragraph.iterancestors())


def _read_footnotes(archive: zipfile.ZipFile) -> dict[str, str]:
    if "word/footnotes.xml" not in archive.namelist():
        return {}
    root = etree.fromstring(archive.read("word/footnotes.xml"))
    out: dict[str, str] = {}
    for note in root.iter(f"{_W}footnote"):
        if note.get(f"{_W}type") in ("separator", "continuationSeparator", "continuationNotice"):
            continue
        fid = note.get(f"{_W}id")
        if fid is None:
            continue
        text = " ".join(_paragraph_text(p) for p in note.iter(f"{_W}p")).strip()
        out[fid] = text
    return out


def _canonical_section(text: str) -> CanonicalSection | None:
    """Return the canonical section a heading opens, using the HTML segmenter's vocabulary."""
    from .segmentation import _CANONICAL_ORDER, _RICH_PATTERNS

    probe = f"\n{text}\n"
    for name in _CANONICAL_ORDER:
        pattern = _RICH_PATTERNS.get(name)
        if pattern is not None and pattern.search(probe):
            return cast(CanonicalSection, name)
    return None


_OPINION_WORD = re.compile(r"\b(?:OPINION|D[ÉE]CLARATION|DECLARATION)\b", re.IGNORECASE)
_JUDGE_WORD = re.compile(r"\b(?:JUDGES?|JUGES?|MR\.?|MRS\.?|M\.|MME|SIR)\b", re.IGNORECASE)


def _opens_opinion(text: str) -> bool:
    """A heading names a separate opinion (not a sub-heading inside one)."""
    stripped = text.strip()
    if not stripped or not _OPINION_WORD.search(stripped):
        return False
    letters = [c for c in stripped if c.isalpha()]
    upper_share = sum(c.isupper() for c in letters) / max(1, len(letters))
    return upper_share > 0.6


def _names_judges(text: str) -> bool:
    return bool(_JUDGE_WORD.search(text))


def _parses_as_opinion(text: str) -> bool:
    from .opinions import split_opinions_report

    return bool(split_opinions_report(f"{text}\n\nbody.").opinions)


def build_docx_structure(data: bytes, *, itemid: str | None = None) -> DocxStructure:
    """Parse a HUDOC DOCX payload into an advisory :class:`DocxStructure`."""
    from .opinions import split_opinions_report

    digest = hashlib.sha256(data).hexdigest()
    diagnostics: list[str] = []
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        document = etree.fromstring(archive.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, etree.XMLSyntaxError) as exc:
        return DocxStructure(
            itemid=itemid,
            sha256=digest,
            byte_count=len(data),
            diagnostics=[f"unreadable_docx: {exc}"],
        )
    body = document.find(f"{_W}body")
    paragraphs = list(body.iter(f"{_W}p")) if body is not None else []

    blocks: list[DocxBlock] = []
    style_counts: Counter[str] = Counter()
    unmapped: Counter[str] = Counter()
    for index, paragraph in enumerate(paragraphs):
        style_node = paragraph.find(f"{_W}pPr/{_W}pStyle")
        style_id = style_node.get(f"{_W}val") if style_node is not None else None
        role = role_for_style(style_id)
        if style_id:
            style_counts[style_id] += 1
            if role == "other":
                unmapped[style_id] += 1
        text = _paragraph_text(paragraph)
        para_num = None
        if role in ("paragraph", "paragraph_last"):
            match = _NUMBERED.match(text)
            if match:
                para_num = int(match.group(1))
        blocks.append(
            DocxBlock(
                index=index,
                style_id=style_id,
                role=role,
                text=text,
                para_num=para_num,
                in_table=_in_table(paragraph),
                footnote_ids=_footnote_ids(paragraph),
            )
        )

    # Sections: canonical headings in canonical order. When the template marks
    # section titles (``Ju_H_Head`` / ``ECHRTitle``), only those blocks can open
    # a section; otherwise any heading block can. A canonical heading that
    # appears out of order (``PROCEEDINGS BEFORE THE COMMISSION`` inside the
    # facts of a 1979 judgment) is a sub-heading and is recorded as a diagnostic.
    from .segmentation import _CANONICAL_ORDER

    order = {name: i for i, name in enumerate(_CANONICAL_ORDER)}
    has_titles = any(b.role == "section_heading" for b in blocks)
    candidate_roles = (
        {"section_heading", "decision_title"}
        if has_titles
        else {"section_heading", "heading", "heading_article", "decision_title"}
    )
    sections: list[DocxSection] = []
    last_rank = -1
    for block in blocks:
        if block.in_table or not block.text:
            continue
        if block.role in candidate_roles:
            name = _canonical_section(block.text)
        elif block.role in ("heading", "heading_article"):
            # Sub-headings that the HTML segmenter also treats as sections,
            # with any leading enumeration ("B. ", "2. ", "(a) ") removed.
            name = _canonical_section(_ENUMERATION.sub("", block.text, count=1))
            if name not in ("subject_matter", "complaints", "court_assessment"):
                name = None
        elif block.role == "opinion_heading" and _opens_opinion(block.text):
            name = cast(CanonicalSection, "separate_opinion")
        else:
            continue
        if name is None:
            continue
        rank = order.get(name, -1)
        if rank <= last_rank:
            if not (sections and sections[-1].name == name):
                diagnostics.append(f"section_heading_out_of_order:{name}:{block.index}")
            continue
        if sections:
            sections[-1] = sections[-1].model_copy(update={"end_index": block.index})
        sections.append(
            DocxSection(
                name=name, heading_text=block.text, start_index=block.index, end_index=len(blocks)
            )
        )
        last_rank = rank

    # Opinions: a block opens an opinion when its style is the opinion-heading
    # style and it names an opinion, or when any heading block parses as an
    # opinion heading (the 2013-2020 template centres opinion titles with
    # ``ECHRTitleCentre``). Opinion-styled blocks that do not name an opinion
    # (``I. The issue of applicability``) are sub-headings inside the current
    # opinion. A heading split over two consecutive opinion-styled blocks
    # (``JOINT DISSENTING OPINION`` / ``OF JUDGES X AND Y``) is read as one.
    opinions: list[DocxOpinion] = []
    heads: list[tuple[int, str]] = []
    skip_next = False
    for pos, block in enumerate(blocks):
        if skip_next:
            skip_next = False
            continue
        if block.in_table or not block.text:
            continue
        text = block.text
        if block.role == "opinion_heading":
            nxt = blocks[pos + 1] if pos + 1 < len(blocks) else None
            if (
                nxt is not None
                and nxt.role == "opinion_heading"
                and _opens_opinion(text)
                and not _names_judges(text)
                and _names_judges(nxt.text)
            ):
                text = f"{text} {nxt.text}"
                skip_next = True
            if _opens_opinion(text):
                heads.append((block.index, text))
        elif (
            block.role in ("heading", "section_heading")
            and _opens_opinion(text)
            and _parses_as_opinion(text)
        ):
            heads.append((block.index, text))
    appendix_start = next((s.start_index for s in sections if s.name == "appendix"), len(blocks))
    for ordinal, (start, heading) in enumerate(heads, start=1):
        following = [h for h, _ in heads if h > start]
        end = (
            following[0]
            if following
            else (appendix_start if appendix_start > start else len(blocks))
        )
        body_text = "\n\n".join(
            b.text
            for b in blocks[start + 1 : end]
            if b.text and b.index not in {h for h, _ in heads}
        )
        report = split_opinions_report(f"{heading}\n\n{body_text}")
        parsed = report.opinions[0] if report.opinions else None
        if parsed is None:
            diagnostics.append(f"opinion_heading_not_parsed:{start}:{heading[:60]}")
        opinions.append(
            DocxOpinion(
                ordinal=ordinal,
                heading_text=heading,
                start_index=start,
                end_index=end,
                opinion_type=parsed.opinion_type if parsed else None,
                authors=list(parsed.authors) if parsed else [],
                joined_by=list(parsed.joined_by) if parsed else [],
                joint=getattr(parsed, "joint", None) if parsed else None,
            )
        )

    # A template that centres opinion titles without the opinion-heading style
    # (2013-2020) yields opinions but no separate_opinion section: open one at
    # the first opinion title.
    if heads and not any(s.name == "separate_opinion" for s in sections):
        first = heads[0][0]
        if sections and sections[-1].start_index < first:
            sections[-1] = sections[-1].model_copy(update={"end_index": first})
        appendix = [s for s in sections if s.name == "appendix"]
        end = (
            appendix[0].start_index if appendix and appendix[0].start_index > first else len(blocks)
        )
        sections.append(
            DocxSection(
                name=cast(CanonicalSection, "separate_opinion"),
                heading_text=heads[0][1],
                start_index=first,
                end_index=end,
            )
        )
        sections.sort(key=lambda s: s.start_index)

    notes = _read_footnotes(archive)
    footnotes: list[DocxFootnote] = []
    for fid, text in notes.items():
        invoking = [b.index for b in blocks if fid in b.footnote_ids]
        if not invoking:
            diagnostics.append(f"footnote_without_reference:{fid}")
        footnotes.append(DocxFootnote(footnote_id=fid, text=text, invoking_block_indexes=invoking))

    bench_lines = [b.text for b in blocks if b.role == "judges" and b.text]
    if not any(b.role in ("paragraph", "paragraph_last") for b in blocks):
        diagnostics.append("no_registry_paragraph_styles")
    return DocxStructure(
        itemid=itemid,
        sha256=digest,
        byte_count=len(data),
        blocks=blocks,
        sections=sections,
        opinions=opinions,
        footnotes=footnotes,
        bench_lines=bench_lines,
        style_counts=dict(style_counts),
        unmapped_styles=dict(unmapped),
        diagnostics=diagnostics,
    )


def compare_docx_structure(case: Case, structure: DocxStructure | None = None) -> DocxAgreement:
    """Describe where the HTML-based segmentation and the DOCX structure agree or differ."""
    structure = structure or case.docx_structure
    if structure is None:
        raise ValueError("case has no docx_structure; call fetch with docx_structure=True first")
    sections = case.sections
    from .segmentation import _CANONICAL_ORDER

    html_sections = (
        sorted(name for name in _CANONICAL_ORDER if getattr(sections, name, None))
        if sections is not None
        else []
    )
    docx_sections = sorted({str(s.name) for s in structure.sections})
    html_opinions = list(sections.opinions) if sections is not None else []
    html_authors = [sorted(o.authors) for o in html_opinions]
    docx_authors = [sorted(o.authors) for o in structure.opinions]
    bench = getattr(sections, "bench", None) if sections is not None else None
    html_bench = (
        len(bench.members) if bench is not None and getattr(bench, "members", None) else None
    )
    docx_bench = len(structure.bench_lines) or None
    differences: list[str] = []
    main_html = [n for n in html_sections if n not in SUBSECTIONS]
    main_docx = [n for n in docx_sections if n not in SUBSECTIONS]
    for name in sorted(set(html_sections) ^ set(docx_sections)):
        side = "html_only" if name in html_sections else "docx_only"
        kind = "subsection" if name in SUBSECTIONS else "section"
        differences.append(f"{kind}:{name}:{side}")
    if len(html_opinions) != len(structure.opinions):
        differences.append(
            f"opinion_count:html={len(html_opinions)}:docx={len(structure.opinions)}"
        )
    for i, (a, b) in enumerate(zip(html_authors, docx_authors, strict=False), start=1):
        if a != b:
            differences.append(f"opinion_authors:{i}:html={a}:docx={b}")
    return DocxAgreement(
        html_sections=html_sections,
        docx_sections=docx_sections,
        sections_agree=main_html == main_docx,
        html_opinion_count=len(html_opinions),
        docx_opinion_count=len(structure.opinions),
        opinion_counts_agree=len(html_opinions) == len(structure.opinions),
        html_opinion_authors=html_authors,
        docx_opinion_authors=docx_authors,
        opinion_authors_agree=html_authors == docx_authors,
        html_bench_count=html_bench,
        docx_bench_count=docx_bench,
        differences=differences,
    )


__all__ = [
    "ROLE_MAP",
    "build_docx_structure",
    "compare_docx_structure",
    "normalise_style_id",
    "role_for_style",
]
