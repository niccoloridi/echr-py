"""Parse free-text SCL references into stable bibliographic mentions."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date

from ..bilingual.ecli import normalize_docname, normalize_ecli
from ..models import Case
from .models import CitationMention, ProceduralPhase, ReporterLocator

APPNO_REGEX = re.compile(r"\b(\d{1,6}/\d{2,4})\b")
ECLI_REGEX = re.compile(r"\b(ECLI:CE:ECHR:\d{4}:\d{4}(?:JUD|DEC|ADV|REP)\d+)\b", re.I)
ITEMID_REGEX = re.compile(r"\b(001-\d+|003-\d{4,}-\d{4,})\b")
ADVISORY_REQUEST_REGEX = re.compile(r"\b(P16[-‐‑–\u2014]\d{4}[-‐‑–\u2014]\d{3})\b", re.I)
_PARA_RE = re.compile(
    r"(?:§{1,2}|par(?:a(?:graph)?s?)?\.?|paragraphes?)\s*"
    r"([\d–\u2014-]+(?:\s*(?:,|and|et)\s*[\d–\u2014-]+)*)",
    re.I,
)

_MONTHS = {
    "january": 1,
    "janvier": 1,
    "february": 2,
    "fevrier": 2,
    "février": 2,
    "march": 3,
    "mars": 3,
    "april": 4,
    "avril": 4,
    "may": 5,
    "mai": 5,
    "june": 6,
    "juin": 6,
    "july": 7,
    "juillet": 7,
    "august": 8,
    "aout": 8,
    "août": 8,
    "september": 9,
    "septembre": 9,
    "october": 10,
    "octobre": 10,
    "november": 11,
    "novembre": 11,
    "december": 12,
    "decembre": 12,
    "décembre": 12,
}
_DATE_RE = re.compile(
    r"\b(?P<day>\d{1,2})(?:er)?\s+(?P<month>"
    + "|".join(map(re.escape, _MONTHS))
    + r")\s+(?P<year>19\d{2}|20\d{2})\b",
    re.I,
)
_DATE_LIKE_RE = re.compile(
    r",\s*\d{1,2}(?:er)?\s+(?:" + "|".join(map(re.escape, _MONTHS)) + r")(?:\s+\d{1,4})?",
    re.I,
)
_PARTIAL_DATE_RE = re.compile(
    r"\b(?P<month>" + "|".join(map(re.escape, _MONTHS)) + r")\s+(?P<year>19\d{2}|20\d{2})\b",
    re.I,
)

_SERIES_RE = re.compile(
    r"(?:Series|s[ée]rie)\s+A\s+n\s*(?:o\.?|°|º)\s*(?P<number>\d+)"
    r"(?:(?:[-‑–]|\s+)(?P<suffix>[A-Z]))?",
    re.I,
)
_REPORTS_RE = re.compile(
    r"(?:Reports(?:\s+of\s+Judgments\s+and\s+Decisions)?|Recueil(?:\s+des\s+arr[êe]ts\s+et\s+d[ée]cisions)?)"
    r"\s+(?P<year>19\d{2})(?:(?:[-‑–]|\s+)(?P<volume>[IVX]+))?",
    re.I,
)
_ECHR_RE = re.compile(
    r"(?:ECHR|CEDH)\s+(?P<year>19\d{2}|20\d{2})"
    r"(?:(?:[-‑–]|\s+)(?P<volume>[IVX]+))?",
    re.I,
)
_DR_RE = re.compile(
    r"(?:Decisions?\s+and\s+Reports?\s*(?:\(\s*D\.?\s*R\.?\s*\))?"
    r"|\(?\s*D\.?\s*R\.?\s*\)?|D[ée]cisions?\s+et\s+rapports?)"
    r"\s*(?P<volume>\d+)(?:[-‑–](?P<suffix>[A-Z]))?"
    r"(?:\s*,?\s*p\.?\s*(?P<page>\d+))?",
    re.I,
)
_COMMISSION_REPORT_RE = re.compile(
    r"(?:Commission\s+Report|report\s+of\s+the\s+Commission|rapport\s+de\s+la\s+Commission)"
    r"\s*(?P<volume>\d+)?",
    re.I,
)
_COMMISSION_COLLECTION_RE = re.compile(
    r"(?:decision\s+of\s+the\s+Commission|d[ée]cision\s+de\s+la\s+Commission)"
    r".*?\bReports?\s+(?P<volume>\d+)",
    re.I,
)

_NAME_RE = re.compile(
    r"^\s*(?P<name>.+?\s+(?:v\.|c\.|contre|against)\s+.+?)"
    r"(?=\s*(?:,|\[GC\]|\(|\bn(?:o|os|°)|\brequ[êe]te|\bapplication))",
    re.I,
)
_HISTORICAL_NAME_RE = re.compile(
    r"^\s*(?:(?:the\s+)?(?:case\s+of\s+)?|arr[êe]t\s+|d[ée]cision\s+)"
    r"(?P<name>.+?)\s+(?:judgment|decision|arr[êe]t|d[ée]cision)\s+(?:of|du|de)\s+",
    re.I,
)
_FRENCH_LEADING_NAME_RE = re.compile(
    r"^\s*(?:arr[êe]t|d[ée]cision)\s+(?P<name>.+?)\s+du\s+"
    r"\d{1,2}(?:er)?\s+",
    re.I,
)


def normalize_reference(value: str) -> str:
    """Normalize spacing and typography without discarding bibliographic signals."""
    value = unicodedata.normalize("NFKC", value)
    value = (
        value.replace("‐", "-")
        .replace("‑", "-")
        .replace("–", "-")
        .replace("\u2014", "-")
        .replace("−", "-")
    )
    return re.sub(r"\s+", " ", value).strip()


def normalize_reference_key(value: str) -> str:
    """Normalize bibliographic identity while excluding paragraph pointers.

    The official master list contains the insertion marker ``§ ...`` in every
    row. Judgment text may contain a real pointer such as ``§ 22``, while SCL
    may omit it. Paragraphs are mention-level evidence, not document identity.
    """
    normalized = normalize_reference(value)
    normalized = re.sub(r"\s*,?\s*§{1,2}\s*\.{2,}", "", normalized)
    normalized = re.sub(
        r"\s*,?\s*§{1,2}\s*[\d-]+(?:\s*(?:,|and|et)\s*[\d-]+)*",
        "",
        normalized,
        flags=re.I,
    )
    normalized = re.sub(r"\s*,\s*,+", ", ", normalized)
    return normalize_reference(normalized).strip(" ,").casefold()


def _citation_fragment(raw: str) -> str:
    """Stop a malformed SCL fragment before adjacent domestic legislation."""
    boundary = re.search(
        r"(?<=[.)])\s+(?=(?:Law|Act|Code|Loi|D[ée]cret|Gesetz)\s+"
        r"n(?:o|os|°|º)\b)",
        raw,
        re.I,
    )
    return raw[: boundary.start()].rstrip() if boundary else raw


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def parse_reference_date(raw: str) -> date | None:
    matches = list(_DATE_RE.finditer(raw))
    if not matches:
        return None
    match = matches[-1]
    month = _MONTHS[match.group("month").lower()]
    try:
        return date(int(match.group("year")), month, int(match.group("day")))
    except ValueError:
        return None


def parse_reference_date_parts(raw: str) -> tuple[int | None, int | None, int | None]:
    """Return a full or partial printed date without using reporter years."""
    parsed = parse_reference_date(raw)
    if parsed:
        return parsed.year, parsed.month, parsed.day
    if match := _PARTIAL_DATE_RE.search(raw):
        return (
            int(match.group("year")),
            _MONTHS[match.group("month").lower()],
            None,
        )
    reporter = parse_reporter(raw)
    years = [int(value) for value in re.findall(r"\b(?:19|20)\d{2}\b", raw)]
    if len(years) == 1 and not (reporter and reporter.year == years[0]):
        return years[0], None, None
    return None, None, None


def parse_reporter(raw: str) -> ReporterLocator | None:
    """Parse the supported official Court and Commission reporter families."""
    if match := _SERIES_RE.search(raw):
        return ReporterLocator(
            family="series_a",
            number=match.group("number"),
            suffix=match.group("suffix"),
            raw=match.group(0),
        )
    if match := _REPORTS_RE.search(raw):
        return ReporterLocator(
            family="reports",
            year=int(match.group("year")),
            volume=match.group("volume"),
            extracts="extract" in raw.lower() or "extrait" in raw.lower(),
            raw=match.group(0),
        )
    if match := _ECHR_RE.search(raw):
        return ReporterLocator(
            family="echr",
            year=int(match.group("year")),
            volume=match.group("volume"),
            extracts="extract" in raw.lower() or "extrait" in raw.lower(),
            raw=match.group(0),
        )
    if match := _DR_RE.search(raw):
        return ReporterLocator(
            family="dr",
            volume=match.group("volume"),
            suffix=match.group("suffix"),
            page=int(match.group("page")) if match.group("page") else None,
            raw=match.group(0),
        )
    if match := _COMMISSION_COLLECTION_RE.search(raw):
        return ReporterLocator(
            family="commission_collection", volume=match.group("volume"), raw=match.group(0)
        )
    if match := _COMMISSION_REPORT_RE.search(raw):
        return ReporterLocator(
            family="commission_report", volume=match.group("volume"), raw=match.group(0)
        )
    return None


def publication_reporter_key(reporter: ReporterLocator) -> str:
    """Return an exact key for per-document publication metadata.

    HUDOC's ``publishedby`` spells the modern reporter as ``Reports of
    Judgments and Decisions``, while judgments normally print ``ECHR`` (or
    ``CEDH``).  These are two names for the same publication.  The key keeps
    the volume and extracts flag, so the equivalence cannot degrade into a
    year-only match.
    """
    family = "reports" if reporter.family in {"echr", "reports"} else reporter.family
    return ":".join(
        (
            family.upper(),
            str(reporter.year or ""),
            (reporter.volume or "").upper(),
            reporter.number or "",
            (reporter.suffix or "").upper(),
            str(reporter.page or ""),
            "EXTRACTS" if reporter.extracts else "FULL",
        )
    )


def parse_published_reporter(raw: str) -> ReporterLocator | None:
    """Parse HUDOC's per-document ``publishedby`` field.

    Unlike historical printed ``Reports`` citations, the metadata spelling is
    also used for the 2000-onward ECHR series.  Keeping this extension local to
    publication metadata avoids changing how free-form authority text is
    interpreted.
    """
    if match := re.search(
        r"(?:Reports(?:\s+of\s+Judgments\s+and\s+Decisions)?|"
        r"Recueil(?:\s+des\s+arr[êe]ts\s+et\s+d[ée]cisions)?)"
        r"\s+(?P<year>(?:19|20)\d{2})"
        r"(?:(?:[-‑–]|\s+)(?P<volume>[IVX]+))?",
        raw,
        re.I,
    ):
        lowered = raw.casefold()
        return ReporterLocator(
            family="reports",
            year=int(match.group("year")),
            volume=match.group("volume"),
            extracts="extract" in lowered or "extrait" in lowered,
            raw=match.group(0),
        )
    return parse_reporter(raw)


def infer_procedural_phase(raw: str) -> ProceduralPhase:
    text = normalize_reference(raw).lower()
    if "preliminary objection" in text or "exceptions préliminaires" in text:
        return "preliminary_objections"
    if "article 50" in text or "ancien article 50" in text:
        return "article_50"
    if "article 41" in text or "just satisfaction" in text or "satisfaction équitable" in text:
        return "just_satisfaction"
    if "friendly settlement" in text or "règlement amiable" in text:
        return "friendly_settlement"
    if "striking out" in text or "struck out" in text or "radiation" in text:
        return "striking_out"
    if "revision" in text or "révision" in text:
        return "revision"
    if "interpretation" in text or "interprétation" in text:
        return "interpretation"
    if "advisory opinion" in text or "avis consultatif" in text:
        return "advisory_opinion"
    if (
        "commission report" in text
        or "report of the commission" in text
        or "rapport de la commission" in text
    ):
        return "commission_report"
    if (
        "commission decision" in text
        or "decision of the commission" in text
        or "décision de la commission" in text
    ):
        return "commission_decision"
    if re.search(r"\((?:dec\.?|d[ée]c\.?)\)|\bdecision\b|\bd[ée]cision\b", text):
        return "admissibility"
    return "merits" if re.search(r"\bjudgment\b|\barr[êe]t\b", text) else "unknown"


def infer_document_kind(raw: str) -> str:
    phase = infer_procedural_phase(raw)
    if phase in {"commission_decision", "commission_report"}:
        return "commission"
    if phase == "advisory_opinion":
        return "advisory"
    if phase == "admissibility":
        return "decision"
    if phase in {
        "merits",
        "article_50",
        "just_satisfaction",
        "revision",
        "interpretation",
        "striking_out",
        "friendly_settlement",
    }:
        return "judgment"
    return "unknown"


def extract_reference_name(raw: str) -> str | None:
    # Historical SCL rows often omit the comma between the respondent and
    # ``judgment of DATE``.  Try that grammar before the permissive modern
    # name matcher, whose next comma may be the one after the printed date.
    if match := _FRENCH_LEADING_NAME_RE.match(raw):
        name = _strip_name_bibliography(match.group("name").strip(" ,"))
        return _strip_institutional_prefix(name)
    if match := _HISTORICAL_NAME_RE.match(raw):
        name = _strip_name_bibliography(match.group("name").strip(" ,"))
        return _strip_institutional_prefix(name)
    match = _NAME_RE.match(raw)
    if match:
        return _strip_institutional_prefix(
            _strip_name_bibliography(match.group("name").strip(" ,"))
        )
    head = raw.split(",", 1)[0].strip()
    if re.match(r"^(?:Case\s+[‘\"“]|Advisory opinion|Decision on|Inter-State)", head, re.I):
        return head
    return head if re.search(r"\s(?:v\.|c\.|contre|against)\s", head, re.I) else None


def _strip_institutional_prefix(name: str) -> str:
    """Keep Commission labels out of the cited party title."""
    return re.sub(
        r"^(?:(?:European\s+)?Commission\s+of\s+Human\s+Rights|"
        r"Commission\s+europ[ée]enne\s+des\s+droits\s+de\s+l['’]homme)\s*,\s*",
        "",
        name,
        flags=re.I,
    ).strip()


def _strip_name_bibliography(name: str) -> str:
    """Remove an app-number tail absorbed before a later historical date cue."""
    return re.sub(
        r"\s*,?\s*(?:applications?|requ[êe]tes?)?\s*"
        r"n(?:o|os|°|º)\.?\s*\d{1,6}/\d{2,4}.*$",
        "",
        name,
        flags=re.I,
    ).strip(" ,")


def extract_respondent(name: str | None) -> str | None:
    """Return the printed respondent portion without attempting state coding."""
    if not name:
        return None
    parts = re.split(r"\s+(?:v\.|c\.|contre|against)\s+", name, maxsplit=1, flags=re.I)
    return parts[1].strip() if len(parts) == 2 else None


def parse_scl_mentions(case: Case) -> list[CitationMention]:
    """Parse every SCL fragment for one source document, preserving order."""
    if not case.scl:
        return []
    fragments = [part.strip() for part in str(case.scl).split(";") if part.strip()]
    mentions: list[CitationMention] = []
    source_key = normalize_ecli(case.ecli) or case.itemid or ";".join(case.appno) or "unknown"
    for ordinal, fragment in enumerate(fragments):
        raw = fragment
        parsed = _citation_fragment(normalize_reference(fragment))
        normalized = normalize_reference_key(parsed)
        reference_hash = _hash(normalized)
        mention_id = _hash(f"{source_key}|{ordinal}|{parsed.casefold()}")
        target_date = parse_reference_date(parsed)
        target_year, target_month, target_day = parse_reference_date_parts(parsed)
        ecli_match = ECLI_REGEX.search(parsed)
        itemid_match = ITEMID_REGEX.search(parsed)
        advisory_match = ADVISORY_REQUEST_REGEX.search(parsed)
        cited_name = extract_reference_name(parsed)
        mentions.append(
            CitationMention(
                mention_id=mention_id,
                reference_hash=reference_hash,
                source_itemid=case.itemid,
                source_ecli=normalize_ecli(case.ecli),
                source_appnos=list(case.appno),
                source_language=case.language,
                source_date=case.kp_date,
                ordinal=ordinal,
                raw_ref=raw,
                normalized_ref=normalized,
                cited_name=cited_name,
                respondent=extract_respondent(cited_name),
                explicit_ecli=normalize_ecli(ecli_match.group(1)) if ecli_match else None,
                explicit_itemid=itemid_match.group(1) if itemid_match else None,
                advisory_request_id=(
                    normalize_reference(advisory_match.group(1)).upper() if advisory_match else None
                ),
                explicit_appnos=list(dict.fromkeys(APPNO_REGEX.findall(parsed))),
                scl_appno_candidates=list(dict.fromkeys(case.sclappnos)),
                target_date=target_date,
                target_year=target_year,
                target_month=target_month,
                target_day=target_day,
                document_kind=infer_document_kind(parsed),  # type: ignore[arg-type]
                procedural_phase=infer_procedural_phase(parsed),
                grand_chamber="[GC]" in parsed.upper() or "GRANDE CHAMBRE" in parsed.upper(),
                reporter=parse_reporter(parsed),
                # Remove the printed decision date first so ``§ 51, 11 March``
                # cannot misread the day of the month as a second paragraph.
                target_paragraphs=[
                    m.group(1)
                    for m in _PARA_RE.finditer(_DATE_LIKE_RE.sub("", _DATE_RE.sub("", parsed)))
                ],
            )
        )
    return mentions


def locate_source_context(
    text: str | None, mention: CitationMention, *, radius: int = 500
) -> str | None:
    """Return source-text context around the strongest printable reference token."""
    if not text:
        return None
    needles = [mention.raw_ref, mention.cited_name or ""]
    if mention.reporter:
        needles.append(mention.reporter.raw)
    needles.extend(mention.explicit_appnos)
    folded = text.casefold()
    for needle in needles:
        needle = needle.casefold().strip()
        if not needle:
            continue
        index = folded.find(needle)
        if index >= 0:
            return text[max(0, index - radius) : index + len(needle) + radius]
        # HUDOC text and PDF-derived references frequently disagree only on
        # whitespace or hyphen glyphs. Keep indices in the original text by
        # matching a flexible expression directly against it.
        pattern = re.escape(normalize_reference(needle))
        pattern = pattern.replace(r"\ ", r"\s+")
        pattern = pattern.replace(r"\-", r"[-‑–\u2014]")
        match = re.search(pattern, text, re.I)
        if match:
            return text[max(0, match.start() - radius) : match.end() + radius]
    return None


def normalized_title(value: str | None) -> str:
    return normalize_docname(value)
