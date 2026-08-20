"""HTML→text and HTML→Markdown conversions."""

from __future__ import annotations

import re

import html2text
from bs4 import BeautifulSoup, Tag

_FOOTNOTE_RE = re.compile(r"^_?ftn(?P<label>\d+)$", re.IGNORECASE)


def _footnote_label(value: object) -> str | None:
    match = _FOOTNOTE_RE.fullmatch(str(value or "").lstrip("#"))
    return match.group("label") if match else None


def _element_text(element: Tag) -> str:
    """Flatten inline nodes while preserving source whitespace semantics."""
    return " ".join(element.get_text(separator="", strip=False).split())


def _markdown_footnote_html(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for anchor in soup.find_all("a", href=True):
        label = _footnote_label(anchor.get("href"))
        if label:
            anchor.replace_with(f"HUDOCFOOTNOTEREF{label}")
    for body in soup.find_all(id=lambda value: _footnote_label(value) is not None):
        label = _footnote_label(body.get("id"))
        if label is None:
            continue
        parts = [
            _element_text(element)
            for element in body.find_all(["p", "li"], recursive=True)
            if _element_text(element)
        ] or [_element_text(body)]
        parts[0] = re.sub(
            rf"^\s*\[?{re.escape(label)}\]?[.)]?\s*", "", parts[0], count=1
        )
        wrapper = soup.new_tag("div")
        for index, text in enumerate(parts):
            paragraph = soup.new_tag("p")
            prefix = (
                f"HUDOCFOOTNOTEBODY{label} "
                if index == 0
                else f"HUDOCFOOTNOTECONT{label} "
            )
            paragraph.string = f"{prefix}{text}"
            wrapper.append(paragraph)
        body.replace_with(wrapper)
    return str(soup)


def html_to_text(html: str) -> str:
    """Extract plain text from HUDOC HTML.

    HUDOC main documents are paragraph-structured (``<p>`` / ``<h1-6>``) and
    we walk those tags to preserve logical block boundaries. HUDOC-EXEC
    documents come from a PDF→HTML conversion that uses absolutely-positioned
    ``<div>`` / ``<span>`` blocks with zero paragraph tags; for those we
    fall back to a whole-body extraction.
    """
    soup = BeautifulSoup(html, "lxml")
    for el in soup(["style", "script"]):
        el.decompose()

    paragraph_blocks = soup.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6", "li"])
    if paragraph_blocks:
        parts = [
            _element_text(el)
            for el in paragraph_blocks
            if _element_text(el)
        ]
        return "\n\n".join(parts)

    return soup.get_text(separator="\n", strip=True)


def html_to_md(html: str) -> str:
    """Convert HUDOC HTML to GitHub-flavored Markdown via ``html2text``.

    Word-style HUDOC footnotes become GFM-compatible ``[^n]`` references and
    ``[^n]:`` bodies instead of losing the reference/body relationship.
    """
    h = html2text.HTML2Text()
    h.body_width = 0
    h.unicode_snob = True
    h.ignore_emphasis = False
    h.ignore_links = False
    h.ignore_images = True
    markdown = h.handle(_markdown_footnote_html(html))
    markdown = re.sub(r"HUDOCFOOTNOTEREF(\d+)", r"[^\1]", markdown)
    markdown = re.sub(r"\[\[\^(\d+)\]\]", r"[^\1]", markdown)
    markdown = re.sub(r"HUDOCFOOTNOTEBODY(\d+)\s*", r"[^\1]: ", markdown)
    return re.sub(r"HUDOCFOOTNOTECONT\d+\s*", "    ", markdown)
