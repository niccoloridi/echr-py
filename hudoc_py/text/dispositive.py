"""Deterministic retrieval of individual operative-part rulings."""

from __future__ import annotations

import re

from ..models import DispositiveParagraph, Sections

_VOTE_RE = re.compile(
    r"\b(unanimously|by\s+\w+(?:\s+\w+){0,8}\s+votes?\s+to\s+\w+|"
    r"à\s+l['’]unanimité|par\s+\w+(?:\s+\w+){0,8}\s+voix?\s+contre\s+\w+)\b",
    re.IGNORECASE,
)
_LEADING_NUMBER_RE = re.compile(r"^\s*\d{1,3}[.)]\s*")
_CONTINUATION_RE = re.compile(r"^\s*(?:\([a-zivxlcdm]+\)|[a-z][.)]|[-–\u2014])\s+", re.IGNORECASE)
_FORMAL_END_RE = re.compile(
    r"^\s*(?:done\s+in|fait\s+en|signed|president|registrar|greffier)\b", re.IGNORECASE
)


def extract_dispositive_paragraphs(sections: Sections) -> list[DispositiveParagraph]:
    """Return operative rulings in source order with stable spine addresses."""
    spine = sections.spine
    if spine is None:
        return []
    span = next((value for value in sections.spans if value.section == "operative"), None)
    if span is not None:
        candidates = spine.blocks[span.start_block : span.end_block]
    else:
        candidates = [block for block in spine.blocks if block.section == "operative"]

    rulings: list[DispositiveParagraph] = []
    for block in candidates:
        if block.type == "heading" or not block.text.strip():
            continue
        if _FORMAL_END_RE.match(block.text):
            break
        is_continuation = bool(
            rulings
            and block.para_num is None
            and (
                block.type == "list_item"
                or _CONTINUATION_RE.match(block.text)
                or rulings[-1].text.rstrip().endswith(":")
            )
        )
        if is_continuation:
            ruling = rulings[-1]
            ruling.block_ids.append(block.block_id)
            if block.para_id:
                ruling.paragraph_ids.append(block.para_id)
            ruling.text = f"{ruling.text}\n{block.text}"
            ruling.decision = _LEADING_NUMBER_RE.sub("", ruling.text).strip()
            if ruling.vote is None:
                vote_match = _VOTE_RE.search(block.text)
                ruling.vote = vote_match.group(0) if vote_match else None
            continue
        order = len(rulings) + 1
        vote_match = _VOTE_RE.search(block.text)
        decision = _LEADING_NUMBER_RE.sub("", block.text).strip()
        local_id = block.para_id or block.block_id
        rulings.append(
            DispositiveParagraph(
                disposition_id=f"{spine.document_id or 'document'}:operative:{local_id}",
                document_id=spine.document_id,
                block_id=block.block_id,
                block_ids=[block.block_id],
                para_id=block.para_id,
                paragraph_ids=[block.para_id] if block.para_id else [],
                order=order,
                text=block.text,
                decision=decision or None,
                vote=vote_match.group(0) if vote_match else None,
            )
        )
    return rulings
