"""Deterministic extraction of the deciding bench from HUDOC front matter."""

from __future__ import annotations

import re

from ..models import BenchComposition, BenchMember, DocumentSpine
from .judges import (
    is_ad_hoc_judge,
    judge_country,
    judge_region,
    judge_years,
    normalise_judge_name,
)

_START_RE = re.compile(
    r"(?i)\b(?:composed\s+of|compos[ée]e?\s+de|si[ée]geaient|members\s+being\s+present|following\s+judges)\b\s*:?"
)
_END_RE = re.compile(
    r"(?i)\b(?:registrar|deputy\s+registrar|section\s+registrar|greffi(?:er|ère)|"
    r"having\s+deliberated|apr[eè]s\s+en\s+avoir\s+d[ée]lib[ée]r[ée]|delivers\s+the\s+following)\b"
)
_CLERK_LINE_RE = re.compile(
    r"(?i)^(?:and\s+(?:also\s+)?of|et\s+(?:aussi\s+)?de|assist[ée]s?\s+de)\b"
)
_ROLE_ONLY_RE = re.compile(
    r"(?i)^(?:president|président(?:e)?|vice[- ]president|vice[- ]président(?:e)?|"
    r"section\s+president|président(?:e)?\s+de\s+section|judges?|juges?|"
    r"ad\s+hoc\s+judge|juge\s+ad\s+hoc|members?|membres?)\.?$"
)
_ROLE_SUFFIX_RE = re.compile(
    r"(?i),?\s*(?P<role>president|président(?:e)?|vice[- ]president|vice[- ]président(?:e)?|"
    r"section\s+president|président(?:e)?\s+de\s+section|ad\s+hoc\s+judge|"
    r"juge\s+ad\s+hoc|judges?|juges?)\s*,?\s*$"
)
_TITLE_RE = re.compile(
    r"(?i)^(?:the\s+right\s+honourable\s+|MM?\.\s*|Mr\.?\s+|Mrs\.?\s+|Ms\.?\s+|"
    r"Mme\.?\s+|Mlle\.?\s+|Sir\s+|Lord\s+|Lady\s+(?:Justice\s+)?|Baroness\s+)"
)


def _role(value: str) -> str:
    folded = value.casefold().replace("é", "e")
    if "ad hoc" in folded:
        return "ad_hoc_judge"
    if "vice" in folded:
        return "vice_president"
    if "section" in folded:
        return "section_president"
    if "president" in folded:
        return "president"
    return "judge"


def _clean_candidate(value: str) -> tuple[str, str]:
    value = re.sub(r"^[\s•*\-–\u2014]+", "", value).strip()
    value = re.sub(r"\s+", " ", value)
    role = "judge"
    match = _ROLE_SUFFIX_RE.search(value)
    if match:
        role = _role(match.group("role"))
        value = value[: match.start()].rstrip(" ,")
    while (stripped := _TITLE_RE.sub("", value)) != value:
        value = stripped
    value = re.sub(r"^(?:and|et)\s+", "", value, flags=re.IGNORECASE).strip(" ,.;")
    return value, role


def _looks_like_name(value: str) -> bool:
    if not value or _ROLE_ONLY_RE.fullmatch(value):
        return False
    if re.search(r"(?i)\b(?:court|cour|chamber|chambre|section|registrar|greffier)\b", value):
        return False
    words = re.findall(r"[^\W\d_][^\W\d_'.-]*", value, flags=re.UNICODE)
    if not 1 <= len(words) <= 7:
        return False
    return all(len(word) > 1 or (len(word) == 1 and word.isupper()) for word in words)


def _canonical_bench_name(raw_name: str) -> str:
    """Prefer a verified roster form while retaining genuinely unknown names."""
    canonical = normalise_judge_name(raw_name)
    if judge_country(canonical) is not None or is_ad_hoc_judge(canonical):
        return canonical
    parts = raw_name.split()
    for start in range(1, len(parts)):
        candidate = normalise_judge_name(" ".join(parts[start:]))
        if judge_country(candidate) is not None or is_ad_hoc_judge(candidate):
            return candidate
    return canonical


def extract_bench_composition(
    text: str, *, spine: DocumentSpine | None = None
) -> BenchComposition | None:
    """Parse the deciding judges, not merely authors of separate opinions.

    English and French modern templates are supported together with two common
    historical English markers. Unknown names are retained; roster enrichment
    is evidence added after parsing and is never a gate on membership.
    """
    if not text:
        return None
    start_match = _START_RE.search(text[: min(len(text), 20_000)])
    if not start_match:
        return None
    tail = text[start_match.end() : min(len(text), start_match.end() + 8_000)]
    end_match = _END_RE.search(tail)
    if end_match:
        tail = tail[: end_match.start()]
        char_end = start_match.end() + end_match.start()
    else:
        # Front matter must be bounded. Refuse to consume a whole judgment.
        paragraph_break = re.search(r"\n\s*\n", tail)
        if not paragraph_break:
            return BenchComposition(
                language="FR" if "compos" in start_match.group().casefold() else "EN",
                char_start=start_match.start(),
                confidence=0.0,
                diagnostics=["composition_end_not_found"],
            )
        tail = tail[: paragraph_break.start()]
        char_end = start_match.end() + paragraph_break.start()

    # Modern composition blocks normally terminate the member list with a
    # plural role immediately before naming the Registrar. Bound at that role
    # so the Registrar is not mistaken for an eighteenth Grand Chamber judge,
    # and so a final judge followed by `judges, and Mr ... Registrar` remains a
    # clean candidate.
    plural_roles = list(re.finditer(r"(?i)\b(?:judges|juges|members|membres)\b", tail))
    if plural_roles:
        member_end = plural_roles[-1].start()
        tail = tail[:member_end]
        char_end = start_match.end() + member_end

    chunks: list[str] = []
    for line in tail.replace(";", "\n").splitlines():
        line = line.strip()
        if not line:
            continue
        if _CLERK_LINE_RE.match(line):
            break
        chunks.extend(
            part.strip() for part in re.split(r",(?=\s*(?:[A-ZÀ-ÖØ-Þ]|M(?:r|me)?\.?\s))", line)
        )

    parsed: list[tuple[str, str]] = []
    for chunk in chunks:
        role_only = chunk.strip(" ,.;")
        if parsed and _ROLE_ONLY_RE.fullmatch(role_only):
            parsed[-1] = (parsed[-1][0], _role(role_only))
            continue
        candidate, role = _clean_candidate(chunk)
        if not _looks_like_name(candidate):
            continue
        parsed.append((candidate, role))

    members: list[BenchMember] = []
    seen: set[str] = set()
    cursor = start_match.end()
    source_blocks: list[str] = []
    for raw_name, role in parsed:
        name = _canonical_bench_name(raw_name)
        if not name or name in seen:
            continue
        seen.add(name)
        found = text.find(raw_name, cursor, char_end)
        if found < 0:
            found = text.find(raw_name, start_match.end(), char_end)
        block_id = None
        if spine is not None and found >= 0:
            block = next(
                (block for block in spine.blocks if block.char_start <= found < block.char_end),
                None,
            )
            block_id = block.block_id if block else None
            if block_id and block_id not in source_blocks:
                source_blocks.append(block_id)
        tenure_start, tenure_end = judge_years(name)
        ad_hoc = role == "ad_hoc_judge" or is_ad_hoc_judge(name)
        members.append(
            BenchMember(
                name=name,
                raw_name=raw_name,
                role="ad_hoc_judge" if ad_hoc else role,  # type: ignore[arg-type]
                country=judge_country(name),
                region=judge_region(name),
                tenure_start=tenure_start,
                tenure_end=tenure_end,
                is_ad_hoc=ad_hoc,
                source_block_id=block_id,
                char_start=found if found >= 0 else None,
                char_end=found + len(raw_name) if found >= 0 else None,
            )
        )
        if found >= 0:
            cursor = found + len(raw_name)

    diagnostics: list[str] = []
    if not members:
        diagnostics.append("composition_members_not_parsed")
    elif len(members) < 3:
        diagnostics.append("composition_unusually_small")
    unknown = [member.name for member in members if member.country is None and not member.is_ad_hoc]
    if unknown:
        diagnostics.append("composition_unknown_roster_names:" + "|".join(unknown))
    confidence = 0.95 if len(members) >= 7 and not unknown else 0.8 if len(members) >= 3 else 0.4
    return BenchComposition(
        members=members,
        language="FR"
        if re.search(r"(?i)\b(?:composée\s+de|siégeaient)\b", start_match.group())
        else "EN",
        raw_text=text[start_match.start() : char_end].strip(),
        source_block_ids=source_blocks,
        char_start=start_match.start(),
        char_end=char_end,
        confidence=confidence,
        diagnostics=diagnostics,
    )


__all__ = ["extract_bench_composition"]
