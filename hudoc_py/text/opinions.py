"""Split a judgment's separate-opinions block into individual opinions.

The parser recognises English and French Court heading templates, keeps
authors distinct from judges who merely joined an opinion, and fails visibly
through diagnostics when a block cannot be parsed confidently.  Bibliographic
tables of contents and repeated running headers are treated as layout noise,
not as new opinions.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from ..models.common import Opinion, OpinionSplit, OpinionType

_HEAD_NUM = r"(?:(?:[IVXLC]+|\d{1,2})\s*[.)]\s+)?"

# --- English heading grammar -------------------------------------------------

_EN_TYPE = r"""
    (?P<joint>JOINT\s+)?
    (?P<type_full>
        (?:PARTLY\s+|PARTIALLY\s+)?(?:DISSENTING|CONCURRING)
        (?:[,\s]+(?:AND\s+)?(?:PARTLY\s+|PARTIALLY\s+)?(?:DISSENTING|CONCURRING))*
        |SEPARATE(?:\s+CONCURRING|\s+DISSENTING)?
        |DECLARATION
    )
"""

_EN_TITLE = r"""
    (?: JUDGES?\s+
      | (?:MR\.?|MRS\.?|MS\.?)\s+
      | SIR\s+
      | LORD\s+
      | LADY\s+JUSTICE\s+
      | AD\s+HOC\s+JUDGE\s+
      | THE\s+PRESIDENT[,\s]+(?:MR\.?\s+)?
    )
"""

_EN_TITLE_BARE = r"""
    (?: JUDGES?
      | MR\.?|MRS\.?|MS\.?
      | SIR
      | LORD
      | LADY\s+JUSTICE
      | AD\s+HOC\s+JUDGE
      | THE\s+PRESIDENT,?
    )
"""

_EN_HEADER_RE = re.compile(
    rf"^\s*{_HEAD_NUM}{_EN_TYPE}(?:\s+OPINION)?\s+(?:OF|BY)\s+"
    rf"{_EN_TITLE}(?P<names>.+?)\s*$",
    re.IGNORECASE | re.VERBOSE,
)
_EN_HEADER_OPEN_RE = re.compile(
    rf"^\s*{_HEAD_NUM}{_EN_TYPE}(?:\s+OPINION)?"
    rf"(?:\s+(?:OF|BY)(?:\s+{_EN_TITLE_BARE})?)?\s*$",
    re.IGNORECASE | re.VERBOSE,
)
_EN_CONTINUATION_RE = re.compile(
    rf"^\s*(?:OF\s+)?(?:{_EN_TITLE})?(?P<names>[A-ZÀ-Þ].*?)\s*$",
    re.VERBOSE,
)

# Legacy conversions lowercase the names tail. Matched only through
# :func:`_continuation_names`, which requires roster anchoring for that form.
_EN_CONTINUATION_LOWER_RE = re.compile(
    rf"^\s*(?:OF\s+)?(?:{_EN_TITLE})?(?P<names>\w.*?)\s*$",
    re.VERBOSE | re.IGNORECASE,
)

# --- French heading grammar --------------------------------------------------

_FR_TYPE = r"""
    (?:OPINION\s+
        (?P<fr_type>
            (?:EN\s+PARTIE\s+|PARTIELLEMENT\s+)?(?:DISSIDENTE|CONCORDANTE)
            (?:[,\s]+(?:ET\s+)?(?:EN\s+PARTIE\s+|PARTIELLEMENT\s+)?
               (?:DISSIDENTE|CONCORDANTE))*
            |S[ÉE]PAR[ÉE]E
        )
        (?P<fr_joint>\s+COMMUNE)?
     |(?P<fr_decl>D[ÉE]CLARATION)
    )
"""

_FR_TITLE = r"""
    (?: DU\s+JUGE\s+
      | DES\s+JUGES\s+
      | DE\s+LA\s+JUGE\s+
      | DE\s+M(?:ME|M)?\.?\s+(?:LE\s+JUGE\s+|LA\s+JUGE\s+)?
      | (?:AUX|AU)\s+JUGES?\s+
      | [ÀA]\s+M(?:M)?\.?\s+(?:LES?\s+JUGES?\s+|LA\s+JUGE\s+)?
      | [ÀA]\s+MME\s+(?:LA\s+JUGE\s+)?
      | DU\s+PR[ÉE]SIDENT\s+
    )
"""

_FR_TITLE_BARE = r"""
    (?: DU\s+JUGE | DES\s+JUGES | DE\s+LA\s+JUGE
      | DE\s+M(?:ME|M)?\.?(?:\s+(?:LE|LA)\s+JUGE)?
      | (?:AUX|AU)\s+JUGES?
      | [ÀA]\s+M(?:M)?\.?(?:\s+LES?\s+JUGES?)?
      | [ÀA]\s+MME(?:\s+LA\s+JUGE)?
      | DU\s+PR[ÉE]SIDENT
    )
"""

_FR_HEADER_RE = re.compile(
    rf"^\s*{_HEAD_NUM}{_FR_TYPE}\s+{_FR_TITLE}(?P<names>.+?)\s*$",
    re.IGNORECASE | re.VERBOSE,
)
_FR_HEADER_OPEN_RE = re.compile(
    rf"^\s*{_HEAD_NUM}{_FR_TYPE}(?:\s+{_FR_TITLE_BARE})?\s*$",
    re.IGNORECASE | re.VERBOSE,
)
_FR_CONTINUATION_RE = re.compile(
    rf"^\s*(?:{_FR_TITLE})?(?P<names>[A-ZÀ-Þ].*?)\s*$",
    re.VERBOSE,
)

# --- Name parsing and layout guards -----------------------------------------

_JOINER = (
    r"JOINED\s+BY"
    r"|APPROUV(?:ÉE?|EE?|E)S?\s+PAR"
    r"|[ÀA]\s+LAQUELLE\s+(?:(?:SE\s+)?RALLI(?:E|ENT)|SE\s+JOI(?:NT|GNENT))"
    r"|[ÀA]\s+LAQUELLE\s+S[’']EST\s+RALLI(?:ÉE?|EE?|E)"
    r"|RALLI(?:ÉE?|EE?|E)S?\s+PAR"
    r"|SE\s+RALLIE\s+[ÀA]"
)
_TITLE_ANY = (
    r"(?:JUDGES?|JUGES?|SIR|LORD|LADY\s+JUSTICE|LADY|BARONESS|"
    r"MR\.?|MRS\.?|MS\.?|M(?:ME|M)?\.?|LES?|LA|LE\s+JUGE|LA\s+JUGE|"
    r"LES\s+JUGES|AD\s+HOC\s+JUDGE)\s+"
)
_JOINED_BY_RE = re.compile(
    rf",?\s+(?:{_JOINER})\s+(?:{_TITLE_ANY})*", re.IGNORECASE
)
_PAREN_JOINER_RE = re.compile(rf"\(\s*({_JOINER})\b", re.IGNORECASE)
_PARENTHETICAL_RE = re.compile(r"\([^)]*\)")
_AD_HOC_TRAIL_RE = re.compile(r",?\s*AD\s+HOC\s+JUDGES?\s*$", re.IGNORECASE)
_JUDGE_LABEL_TRAIL_RE = re.compile(r",?\s*JUGES?\s*$", re.IGNORECASE)
_FOOTNOTE_RE = re.compile(r"\[\d+\]")
_TITLE_PREFIX_RE = re.compile(
    r"^(?:(?:JUDGES?|JUGES?|SIR|LORD|LADY\s+JUSTICE|LADY|BARONESS|"
    r"MR\.?|MRS\.?|MS\.?|MME|MM\.?|M\.?|LE\s+JUGE|LA\s+JUGE|"
    r"LES\s+JUGES|AD\s+HOC\s+JUDGE)\s+)+",
    re.IGNORECASE,
)
_INITIALS_RE = re.compile(r"^(?:[A-Z]\.[\s\-]*)+")
_TOC_LINE_RE = re.compile(
    r"(?:\.\s*){2,}\s*\d+\s*$|\s{3,}\d{1,4}\s*$|\t+\d{1,4}\s*$"
)
_HEADER_WORD_RE = re.compile(r"\b(?:OPINION|DECLARATION|D[ÉE]CLARATION)\b", re.I)
_HEADER_START_RE = re.compile(
    rf"^\s*{_HEAD_NUM}(?:JOINT\s+|PARTLY\s+|PARTIALLY\s+|SEPARATE\s+)?"
    r"(?:CONCURRING|DISSENTING|DECLARATION|OPINION|D[ÉE]CLARATION)\b",
    re.IGNORECASE | re.VERBOSE,
)


@dataclass(frozen=True)
class _Header:
    start: int
    end: int
    opinion_type: OpinionType
    joint_heading: bool
    names: str
    language: str


def _parse_name_list(names: str) -> list[str]:
    from .judges import normalise_judge_name

    names = _AD_HOC_TRAIL_RE.sub("", names)
    names = _JUDGE_LABEL_TRAIL_RE.sub("", names)
    names = _FOOTNOTE_RE.sub("", names)
    parts = re.split(r"\s+(?:AND|ET)\s+|,\s*", names, flags=re.IGNORECASE)
    judges: list[str] = []
    for part in parts:
        part = part.strip().strip(". ;:)")
        part = _TITLE_PREFIX_RE.sub("", part).strip()
        part = _INITIALS_RE.sub("", part).strip()
        if not part or part.isdigit():
            continue
        judges.append(normalise_judge_name(part))
    return judges


def _split_authors_joiners(names: str) -> tuple[list[str], list[str]]:
    """Return literal authors and later joiners as separate canonical lists."""
    cleaned = _PAREN_JOINER_RE.sub(r", \1", names)
    cleaned = _PARENTHETICAL_RE.sub(" ", cleaned).replace(")", " ")
    split = _JOINED_BY_RE.split(cleaned, maxsplit=1)
    authors = _parse_name_list(split[0])
    joined_by = _parse_name_list(split[1]) if len(split) == 2 else []
    return authors, joined_by


def _parse_judges(names: str) -> list[str]:
    """Backwards-compatible union of authors and judges joining later."""
    authors, joined_by = _split_authors_joiners(names)
    return [*authors, *joined_by]


def _classify_en(type_full: str) -> OpinionType:
    text = re.sub(r"\s+", " ", type_full.upper())
    partly = "PARTLY" in text or "PARTIALLY" in text
    dissent = "DISSENTING" in text
    concur = "CONCURRING" in text
    if text == "DECLARATION":
        return "declaration"
    if dissent and concur:
        # Older templates omit both instances of "PARTLY", but a single
        # opinion that both concurs and dissents is necessarily partial/partial.
        return "partly_concurring_partly_dissenting"
    if text.startswith("SEPARATE") and not (dissent or concur):
        return "separate"
    if dissent:
        return "partly_dissenting" if partly else "dissenting"
    if concur:
        return "partly_concurring" if partly else "concurring"
    return "separate"


def _classify_fr(type_full: str) -> OpinionType:
    text = re.sub(r"\s+", " ", type_full.upper())
    partly = "EN PARTIE" in text or "PARTIELLEMENT" in text
    dissent = "DISSIDENTE" in text
    concur = "CONCORDANTE" in text
    if dissent and concur:
        return "partly_concurring_partly_dissenting"
    if dissent:
        return "partly_dissenting" if partly else "dissenting"
    if concur:
        return "partly_concurring" if partly else "concurring"
    return "separate"


def _looks_like_heading_tail(names: str) -> bool:
    """Accept a title-case heading but reject a sentence-shaped names tail."""
    tail = names.strip()
    if not tail or tail.endswith((".", ";", ":")):
        return False
    # The length ceiling separates a names tail from a wrapped prose sentence.
    # A joint opinion of six or more judges legitimately exceeds it, so an
    # over-long tail is not rejected outright: it must clear the roster anchor
    # below, which prose cannot.
    if len(tail) >= 90:
        return _names_all_rostered(tail)
    # A proper name may be title case and contain connectors/particles, but a
    # prose continuation contains other wholly lowercase words.
    without_name_words = re.sub(
        r"\b(?:and|et|of|de|du|des|van|von|der|den|del|di|la|le)\b",
        " ",
        tail,
        flags=re.IGNORECASE,
    )
    if re.search(r"\b[a-zà-ÿ]{3,}\b", without_name_words) is None:
        return True
    # Legacy HUDOC conversions lowercase an otherwise exact names tail, which
    # is indistinguishable from prose by casing alone. Accept that form only
    # when every parsed name is independently anchored in the verified
    # elected/ad-hoc roster, exactly as :func:`_heading_case_allowed` does for
    # a lowercased single-line heading. Prose tails still fail closed.
    return _names_all_rostered(tail)


def _names_all_rostered(tail: str) -> bool:
    """True when every name in *tail* resolves in the canonical judge roster."""
    from .judges import is_ad_hoc_judge, judge_country

    stripped = re.sub(
        r"^\s*(?:of|by|des|du|de)\s+(?:the\s+)?(?:judges?|juges?|mr|mrs|ms|sir)\b\.?\s*",
        "",
        tail,
        flags=re.IGNORECASE,
    )
    authors, joined_by = _split_authors_joiners(stripped)
    judges = [*authors, *joined_by]
    return bool(judges) and all(
        judge_country(judge) is not None or is_ad_hoc_judge(judge) for judge in judges
    )


def _heading_case_allowed(prefix: str, names: str) -> bool:
    if prefix == prefix.upper() or _looks_like_heading_tail(names):
        return True
    # Some legacy HUDOC conversions lowercase an otherwise exact heading.
    # Accept that form only when every parsed name is independently anchored
    # in the verified elected/ad-hoc roster; prose tails still fail closed.
    from .judges import is_ad_hoc_judge, judge_country

    authors, joined_by = _split_authors_joiners(names)
    judges = [*authors, *joined_by]
    return bool(judges) and all(
        judge_country(judge) is not None or is_ad_hoc_judge(judge) for judge in judges
    )


def _next_continuation(lines: list[str], i: int) -> tuple[int, str] | None:
    """Find a names continuation after at most two blank lines."""
    j = i + 1
    blanks = 0
    while j < len(lines) and not lines[j].strip() and blanks < 2:
        j += 1
        blanks += 1
    if j >= len(lines):
        return None
    line = lines[j].strip()
    if (
        not line
        or _TOC_LINE_RE.search(line)
        or _HEADER_START_RE.match(line)
        or not _looks_like_heading_tail(line)
    ):
        return None
    return j, line


def _extend_names(lines: list[str], names: str, end: int, connectors: str) -> tuple[str, int]:
    while end < len(lines) and re.search(connectors, names, re.IGNORECASE):
        next_line = lines[end].strip()
        if (
            not next_line
            or _HEADER_START_RE.match(next_line)
            or not _looks_like_heading_tail(next_line)
        ):
            break
        names = f"{names} {next_line}"
        end += 1
    return names, end



def _continuation_names(strict: re.Pattern[str], lenient: re.Pattern[str], line: str) -> str | None:
    """Names from a heading continuation line, or ``None``.

    The strict pattern carries the ordinary uppercase contract. The lenient
    pattern additionally accepts a lowercased tail, which legacy HUDOC
    conversions produce, but only when every parsed name is anchored in the
    verified elected/ad-hoc roster.
    """
    match = strict.match(line)
    if match:
        return match.group("names")
    match = lenient.match(line)
    if match and _names_all_rostered(match.group("names")):
        return match.group("names")
    return None


def _match_header(lines: list[str], i: int) -> _Header | None:
    line = lines[i].strip()
    if not line or _TOC_LINE_RE.search(line):
        return None

    match = _EN_HEADER_RE.match(line)
    if match and _heading_case_allowed(line[: match.start("names")], match.group("names")):
        names, end = _extend_names(
            lines,
            match.group("names"),
            i + 1,
            r"(?:,|\bAND|\bBY|\bOF)\s*$",
        )
        return _Header(
            i,
            end,
            _classify_en(match.group("type_full")),
            bool(match.group("joint")),
            names,
            "EN",
        )

    match = _FR_HEADER_RE.match(line)
    if match and _heading_case_allowed(line[: match.start("names")], match.group("names")):
        type_full = match.group("fr_type") or "DECLARATION"
        names, end = _extend_names(
            lines, match.group("names"), i + 1, r"(?:,|\bET)\s*$"
        )
        opinion_type: OpinionType = (
            "declaration" if match.group("fr_decl") else _classify_fr(type_full)
        )
        return _Header(
            i,
            end,
            opinion_type,
            bool(match.group("fr_joint")),
            names,
            "FR",
        )

    match = _EN_HEADER_OPEN_RE.match(line)
    continuation = _next_continuation(lines, i) if match else None
    if match and continuation:
        j, continuation_line = continuation
        cont_names = _continuation_names(
            _EN_CONTINUATION_RE, _EN_CONTINUATION_LOWER_RE, continuation_line
        )
        if cont_names is not None and _heading_case_allowed(line, cont_names):
            names, end = _extend_names(lines, cont_names, j + 1, r"(?:,|\bAND)\s*$")
            return _Header(
                i,
                end,
                _classify_en(match.group("type_full")),
                bool(match.group("joint")),
                names,
                "EN",
            )

    match = _FR_HEADER_OPEN_RE.match(line)
    continuation = _next_continuation(lines, i) if match else None
    if match and continuation:
        j, continuation_line = continuation
        cont = _FR_CONTINUATION_RE.match(continuation_line)
        if cont and _heading_case_allowed(line, cont.group("names")):
            names, end = _extend_names(
                lines, cont.group("names"), j + 1, r"(?:,|\bET)\s*$"
            )
            type_full = match.group("fr_type") or "DECLARATION"
            opinion_type = (
                "declaration" if match.group("fr_decl") else _classify_fr(type_full)
            )
            return _Header(
                i,
                end,
                opinion_type,
                bool(match.group("fr_joint")),
                names,
                "FR",
            )
    return None


def split_opinions_report(text: str | None) -> OpinionSplit:
    """Split opinions and return confidence plus auditable diagnostics."""
    if not text or not text.strip():
        return OpinionSplit()

    normalized = unicodedata.normalize("NFC", text)
    lines = normalized.splitlines()
    diagnostics: list[str] = []
    headers: list[_Header] = []

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if _TOC_LINE_RE.search(line) and _HEADER_WORD_RE.search(line):
            diagnostics.append(f"dropped_toc:{i + 1}")
            i += 1
            continue
        # PDF extraction often wraps a ToC entry so the opinion type is on
        # one line and the judge plus dot leaders/page number are on the next.
        if _EN_HEADER_OPEN_RE.match(line) or _FR_HEADER_OPEN_RE.match(line):
            continuation_index = i + 1
            blanks = 0
            while (
                continuation_index < len(lines)
                and not lines[continuation_index].strip()
                and blanks < 2
            ):
                continuation_index += 1
                blanks += 1
            if continuation_index < len(lines) and _TOC_LINE_RE.search(
                lines[continuation_index].strip()
            ):
                diagnostics.append(f"dropped_toc:{i + 1}")
                i = continuation_index + 1
                continue
        header = _match_header(lines, i)
        if header:
            headers.append(header)
            i = header.end
        else:
            i += 1

    if not headers:
        return OpinionSplit(confidence=0.0, diagnostics=[*diagnostics, "no_headings_in_block"])

    # A heading immediately followed by another heading has no opinion body.
    # This is the shape of a converted annex index after page numbers vanish.
    body_headers: list[_Header] = []
    prior_substantive_header = False
    for index, header in enumerate(headers):
        next_start = headers[index + 1].start if index + 1 < len(headers) else len(lines)
        body = lines[header.end:next_start]
        if not any(line.strip() for line in body):
            # Some Court judgments end with a declaration of vote that has no
            # explanatory prose. It is distinguishable from an index entry
            # when it follows at least one genuine, body-bearing opinion.
            if header.opinion_type == "declaration" and prior_substantive_header:
                diagnostics.append(f"bodyless_declaration:{header.start + 1}")
                body_headers.append(header)
                continue
            diagnostics.append(f"dropped_index_block:{header.start + 1}")
            continue
        prior_substantive_header = True
        body_headers.append(header)

    # Repeated page headers represent one continuous opinion. Keep the first
    # and recompute spans from the surviving list so neither body part is lost.
    surviving: list[tuple[_Header, list[str], list[str]]] = []
    duplicate_header_lines: set[int] = set()
    seen: set[tuple[OpinionType, tuple[str, ...]]] = set()
    for header in body_headers:
        authors, joined_by = _split_authors_joiners(header.names)
        key = (header.opinion_type, tuple([*authors, *joined_by]))
        if key in seen:
            diagnostics.append(
                f"dropped_duplicate:{header.opinion_type}:{'|'.join(key[1])}"
            )
            duplicate_header_lines.update(range(header.start, header.end))
            continue
        seen.add(key)
        surviving.append((header, authors, joined_by))

    if not surviving:
        return OpinionSplit(confidence=0.0, diagnostics=[*diagnostics, "no_headings_in_block"])

    opinions: list[Opinion] = []
    for index, (header, authors, joined_by) in enumerate(surviving):
        body_end = surviving[index + 1][0].start if index + 1 < len(surviving) else len(lines)
        judges = [*authors, *joined_by]
        raw_header = " ".join(
            line.strip() for line in lines[header.start : header.end] if line.strip()
        )
        opinion_text = "\n".join(lines[header.start:body_end]).strip()
        opinion_body = "\n".join(
            line
            for line_index, line in enumerate(
                lines[header.end:body_end], start=header.end
            )
            if line_index not in duplicate_header_lines
        ).strip()
        opinions.append(
            Opinion(
                opinion_type=header.opinion_type,
                joint=header.joint_heading or len(judges) > 1,
                joint_heading=header.joint_heading,
                authors=authors,
                joined_by=joined_by,
                judges=judges,
                raw_header=raw_header,
                text=opinion_text,
                body=opinion_body,
                language=header.language,
            )
        )

    confidence = 1.0
    from .judges import is_ad_hoc_judge, judge_country

    for judge in {judge for opinion in opinions for judge in opinion.judges}:
        if judge_country(judge) is None and not is_ad_hoc_judge(judge):
            diagnostics.append(f"unknown_judge:{judge}")
            confidence -= 0.15

    first_start = surviving[0][0].start
    preamble_chars = len("\n".join(lines[:first_start]).strip())
    if preamble_chars > 200:
        diagnostics.append(f"unparsed_preamble:{preamble_chars}")
        confidence -= 0.1

    covered = sum(len(opinion.text) for opinion in opinions)
    coverage = min(1.0, covered / max(1, len(normalized.strip())))
    if coverage < 0.9:
        diagnostics.append(f"low_coverage:{coverage:.1%}")
        confidence -= 0.9 - coverage

    for index, opinion in enumerate(opinions):
        if len(opinion.body) < 40 and not (
            opinion.opinion_type == "declaration" and not opinion.body
        ):
            diagnostics.append(f"short_body:{index}")
            confidence -= 0.1

    return OpinionSplit(
        opinions=opinions,
        confidence=round(max(0.0, min(1.0, confidence)), 3),
        diagnostics=diagnostics,
    )


def split_opinions(text: str | None) -> list[Opinion]:
    """Backwards-compatible convenience returning only parsed opinions."""
    return split_opinions_report(text).opinions
