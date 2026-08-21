"""Deterministic, paragraph-aware location of resolved SCL authorities."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from ..models.case import Case
from ..models.common import DocumentBlock, DocumentSpine
from ..text.spine import build_spine_from_html, build_spine_from_text
from .models import (
    CitationAuthorityEntry,
    CitationDiscoveryResult,
    CitationMention,
    CitationOccurrence,
    CitationOccurrenceReport,
    CitationOccurrenceResult,
    CitationResolution,
    CitationSourceInvocation,
    HistoricalCatalogEntry,
)
from .reporter import (
    APPNO_REGEX,
    ECLI_REGEX,
    ITEMID_REGEX,
    extract_respondent,
    infer_document_kind,
    infer_procedural_phase,
    normalize_reference,
    parse_reference_date,
    parse_reporter,
    parse_scl_mentions,
)

_PHASE_RE = re.compile(
    r"[,;]?\s*\(?(?:admissibility|recevabilit[ée]|dec\.?|d[ée]c\.?|"
    r"merits|fond|just satisfaction|satisfaction [ée]quitable|friendly settlement|"
    r"r[èe]glement amiable|preliminary objections?|exceptions pr[ée]liminaires|"
    r"revision|r[ée]vision|interpretation|interpr[ée]tation|striking out|radiation|"
    r"\[GC\]|grande chambre)\)?\s*$",
    re.IGNORECASE,
)
_PARTY_RE = re.compile(
    r"\s+(?:v\.?|c\.?(?!\s+(?:and|et|&)\b)|contre|against)\s+",
    re.IGNORECASE,
)
_DATE_TEXT_RE = re.compile(
    r"\b\d{1,2}\s+(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December|janvier|février|mars|avril|mai|"
    r"juin|juillet|août|septembre|octobre|novembre|décembre)\s+\d{4}\b",
    re.IGNORECASE,
)
_UNMATCHED_ANCHORS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "ecli",
        re.compile(r"\bECLI:CE:ECHR:\d{4}:\d{4}(?:JUD|DEC|ADV|REP)\d+\b", re.I),
    ),
    ("application_number", re.compile(r"\b\d{1,6}/\d{2,4}\b")),
    (
        "reporter",
        re.compile(
            r"\b(?:(?:Series|s[ée]rie)\s+A\s+n\s*(?:o\.?|°)\s*\d+(?:[-‑–][A-Z])?"
            r"|(?:ECHR|CEDH)\s+(?:19|20)\d{2}(?:[-‑–][IVX]+)?)\b",
            re.I,
        ),
    ),
    (
        "full_name",
        re.compile(
            r"\b[A-ZÀ-ÖØ-Þ][\wÀ-ÖØ-öø-ÿ'’.-]+"
            r"(?:\s+(?:and|et|[A-ZÀ-ÖØ-Þ][\wÀ-ÖØ-öø-ÿ'’.-]+)){0,5}"
            r"\s+(?:v\.?|c\.?|contre|against)\s+(?:the\s+)?"
            r"[A-ZÀ-ÖØ-Þ][\wÀ-ÖØ-öø-ÿ'’.-]+"
            r"(?:\s+(?:and|et|the|of|[A-ZÀ-ÖØ-Þ][\wÀ-ÖØ-öø-ÿ'’.-]+)){0,5}\b"
        ),
    ),
)
_CUE_RE = re.compile(
    r"(?:§§?|¶|paras?\.?|judg(?:ment|ement)|decision|décision|arrêt|"
    r"cited above|précité(?:e)?|no\.?\s*\d{3,5}/\d{2}|ECHR\s+\d{4})",
    re.IGNORECASE,
)
_ATTACHED_DOCUMENT_CUE_RE = re.compile(
    r"\s+(?:(?:pilot|principal|earlier|former|previous|initial|chamber|"
    r"grand\s+chamber|merits?|admissibility|just\s+satisfaction)\s+)*"
    r"(?:judgment|decision|arr[êe]t|d[ée]cision)\b",
    re.IGNORECASE,
)


def _citation_cue_at(text: str, start: int, end: int, *, max_gap: int = 18) -> bool:
    """Return whether a citation cue is locally attached to this span."""
    window_start = max(0, start - max_gap - 32)
    window_end = min(len(text), end + max_gap + 48)
    for match in _CUE_RE.finditer(text, window_start, window_end):
        if match.end() <= start:
            gap = text[match.end() : start]
        elif match.start() >= end:
            gap = text[end : match.start()]
        else:
            return True
        punctuation_gap = len(gap) <= max_gap and re.fullmatch(r"[\s,;:()\[\]–—-]*", gap)
        corporate_suffix_gap = re.fullmatch(
            r"\s*,\s*(?:(?:spol\.|soci[ée]t[ée])\s+)?"
            r"(?:s\.?\s*r\.?\s*o\.?|s\.?\s*a\.?|ltd\.?|gmbh)\s*,?\s*",
            gap,
            re.I,
        )
        document_modifier_gap = re.fullmatch(
            r"\s+(?:(?:pilot|principal|earlier|former|previous|initial|chamber|"
            r"grand\s+chamber|merits?|admissibility|just\s+satisfaction)\s+)+",
            gap,
            re.I,
        )
        if punctuation_gap or corporate_suffix_gap or document_modifier_gap:
            return True
    return False


_PIN_RE = re.compile(
    r"(?:§§?|¶|par(?:a(?:graph)?s?)?\.?|paragraphes?)\s*"
    r"(?P<labels>\d+(?:\.\d+)?(?:\([a-z]\))?"
    r"(?:\s*(?:[-–\u2014]|to|à)\s*\d+(?:\.\d+)?(?:\([a-z]\))?)?"
    r"(?:\s*(?:,|and|et)\s*\d+(?:\.\d+)?(?:\([a-z]\))?"
    r"(?:\s*(?:[-–\u2014]|to|à)\s*\d+(?:\.\d+)?(?:\([a-z]\))?)?)*)",
    re.IGNORECASE,
)
_CARRY_FORWARD_RE = re.compile(r"\b(?:ibid(?:em)?\.?|loc\.\s*cit\.)", re.I)
_LABEL_RE = re.compile(
    r"\d+(?:\.\d+)?(?:\([a-z]\))?(?:\s*(?:[-–\u2014]|to|à)\s*"
    r"\d+(?:\.\d+)?(?:\([a-z]\))?)?",
    re.IGNORECASE,
)
_GENERIC = {
    "and",
    "another",
    "applicant",
    "application",
    "case",
    "commission",
    "court",
    "decision",
    "government",
    "human",
    "international",
    "judgment",
    "kingdom",
    "law",
    "laws",
    "national",
    "other",
    "others",
    "rights",
    "said",
    "cases",
    "autres",
    "republic",
    "state",
    "states",
}
_STATE_WORDS = {
    "albania",
    "andorra",
    "armenia",
    "austria",
    "azerbaijan",
    "belgium",
    "bulgaria",
    "croatia",
    "cyprus",
    "denmark",
    "estonia",
    "finland",
    "france",
    "georgia",
    "germany",
    "greece",
    "hungary",
    "iceland",
    "ireland",
    "italy",
    "latvia",
    "liechtenstein",
    "lithuania",
    "luxembourg",
    "malta",
    "moldova",
    "monaco",
    "netherlands",
    "norway",
    "poland",
    "portugal",
    "romania",
    "russia",
    "serbia",
    "slovakia",
    "slovenia",
    "spain",
    "sweden",
    "switzerland",
    "turkey",
    "türkiye",
    "ukraine",
    "united kingdom",
    # French HUDOC party names.  These are identity guards and never become
    # stand-alone citation aliases.
    "albanie",
    "allemagne",
    "armenie",
    "autriche",
    "azerbaidjan",
    "belgique",
    "bosnie herzgovine",
    "bosnie herzegovine",
    "bulgarie",
    "chypre",
    "croatie",
    "danemark",
    "espagne",
    "estonie",
    "finlande",
    "georgie",
    "grece",
    "hongrie",
    "irlande",
    "islande",
    "italie",
    "lettonie",
    "lituanie",
    "macedoine du nord",
    "malte",
    "moldavie",
    "montenegro",
    "norvege",
    "pays bas",
    "pologne",
    "republique tcheque",
    "roumanie",
    "royaume uni",
    "russie",
    "saint marin",
    "serbie",
    "slovaquie",
    "slovenie",
    "suede",
    "suisse",
    "tchequie",
    "turquie",
}
_UNICODE_NAME_TOKEN = r"(?-i:(?![a-zà-öø-ÿ])[^\W\d_])[\w'’&().-]*"
_LOWERCASE_PARTY_CONNECTOR = (
    r"(?:&|and|et|others|autres|de|du|des|la|le|communiste|unifi[ée]|"
    r"gr[ée]co-catholique|spol\.?|s\.?r\.?o\.?|a\.?s\.?|"
    r"[mM]\.?b\.?[hH]\.?|ltd\.?|limited|gmbh|ag|inc\.?|co\.?|"
    r"\(\s*(?:n(?:o\.?|°|º)|no\.?)\s*\d+\s*\))"
)
_DISCOVERY_NAME_RE = re.compile(
    rf"(?P<name>{_UNICODE_NAME_TOKEN}"
    rf"(?:\s+(?:{_LOWERCASE_PARTY_CONNECTOR}|{_UNICODE_NAME_TOKEN})){{0,8}}"
    r"\s*\[?\s*(?:v\.?|c\.?|contre|against)\s+"
    r"(?:(?:the|la|le)\s+)?(?:former\s+)?"
    rf"{_UNICODE_NAME_TOKEN}"
    rf"(?:\s+(?:{_LOWERCASE_PARTY_CONNECTOR}|the|of|de|du|des|la|le|"
    rf"{_UNICODE_NAME_TOKEN})){{0,9}})",
)
_HISTORICAL_PARTY_TOKEN = (
    rf"(?:{_LOWERCASE_PARTY_CONNECTOR}|de|den|der|van|von|la|le|du|des|of|"
    rf"{_UNICODE_NAME_TOKEN})"
)
_HISTORICAL_CASE_NAME = (
    rf"{_UNICODE_NAME_TOKEN}"
    rf"(?:\s*(?:,\s*|\s+){_HISTORICAL_PARTY_TOKEN}){{0,12}}"
    r"\s*\[?\s*(?:v\.?|c\.?|contre|against)\s+"
    r"(?:(?:the|la|le)\s+)?(?:former\s+)?"
    rf"{_UNICODE_NAME_TOKEN}"
    rf"(?:\s+{_HISTORICAL_PARTY_TOKEN}){{0,9}}"
)
_HISTORICAL_NAME_RE = re.compile(rf"(?P<name>{_HISTORICAL_CASE_NAME})")
_EXTERNAL_NUMBER_RE = re.compile(
    r"(?:\bCase\s+C\s*[-‑–]?|\bDirective\s+|\bRegulation\s+|"
    r"\b(?:Commission|Council)\s+Decision\s+|\bDecision\s+\d{4}/\d+|"
    r"\bIPT/|\bBvR\s+|\bCommunication\s+(?:no\.?\s*)?|"
    r"\bResolution\s+|\bOC[-‑–])[^.;]{0,45}$",
    re.I,
)
_EXTERNAL_HUMAN_RIGHTS_COMMITTEE_RE = re.compile(
    r"(?:\bviews\s+adopted\s+by\s+(?:the\s+)?Human\s+Rights\s+Committee\b|"
    r"\bHuman\s+Rights\s+Committee\b[^.;\n]{0,180}\b(?:views|communications?)\b)",
    re.I,
)
_MONTH_WORD = (
    r"(?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December|janvier|février|fevrier|mars|avril|mai|juin|juillet|"
    r"août|aout|septembre|octobre|novembre|décembre|decembre)"
)
_HISTORICAL_ENVELOPE_RE = re.compile(
    r"(?P<raw>(?:"
    r"(?:(?:the\s+)?(?:case\s+of\s+)?)"
    rf"(?P<en_name>{_UNICODE_NAME_TOKEN}[^.;\n]{{2,150}}?\s+(?:v\.?|c\.?|contre|against)\s+"
    r"[^.;\n]{2,100}?)\s+(?:judgment|decision)(?:\s+of)?\s+"
    rf"\d{{1,2}}(?:er)?\s+{_MONTH_WORD}\s+(?:19|20)\d{{2}}"
    r"|(?:arr[êe]t|d[ée]cision)\s+"
    rf"(?P<fr_name>{_UNICODE_NAME_TOKEN}[^.;\n]{{2,120}}?\s+"
    r"(?:v\.?|c\.?|contre|against)\s+[^.;\n]{2,100}?)\s+du\s+"
    rf"\d{{1,2}}(?:er)?\s+{_MONTH_WORD}\s+(?:19|20)\d{{2}})"
    r"(?:[^.;\n]{0,160}?(?:Series|s[ée]rie)\s+A\s+n\s*(?:o\.?|°|º)\s*\d+"
    r"(?:[-‑–]\s*[A-Z])?)?)",
    re.I,
)
_HISTORICAL_FALLBACK_ENVELOPE_RE = re.compile(
    r"(?P<raw>(?:"
    r"(?:(?:the\s+)?(?:case\s+of\s+)?)"
    rf"(?P<en_name>{_HISTORICAL_CASE_NAME})\s+\(?(?:judgment|decision)"
    r"(?:\s+of)?\s+"
    rf"\d{{1,2}}(?:er)?\s+{_MONTH_WORD}\s+(?:19|20)\d{{2}}"
    r"|(?:arr[êe]t|d[ée]cision)\s+"
    rf"(?P<fr_name>{_HISTORICAL_CASE_NAME})\s+du\s+"
    rf"\d{{1,2}}(?:er)?\s+{_MONTH_WORD}\s+(?:19|20)\d{{2}})"
    r"(?:[^.;\n]{0,160}?(?:(?:Series|s[ée]rie)\s+A\s+"
    r"n\s*(?:o\.?|°|º)\s*\d+(?:[-‑–]\s*[A-Z])?|"
    r"(?:Reports(?:\s+of\s+Judgments\s+and\s+Decisions)?|ECHR|CEDH)\s+"
    r"(?:19|20)\d{2}(?:[-‑–][IVX]+)?))?\)?)",
    re.I,
)
_NAME_DATE_SERIES_RE = re.compile(
    rf"(?P<raw>(?P<name>{_HISTORICAL_CASE_NAME})\s*,\s*"
    rf"\d{{1,2}}(?:er)?\s+{_MONTH_WORD}\s+(?:19|20)\d{{2}}"
    r"[^.;\n]{0,140}?(?:Series|s[ée]rie)\s+A\s+n\s*(?:o\.?|°|º)\s*\d+"
    r"(?:[-‑–]\s*[A-Z])?)",
    re.I,
)
_REPORTER_TEXT = (
    r"(?:(?:Series|s[ée]rie)\s+A\s+n\s*(?:o\.?|°|º)\s*\d+(?:[-‑–]\s*[A-Z])?"
    r"|(?:Reports(?:\s+of\s+Judgments\s+and\s+Decisions)?|"
    r"Recueil(?:\s+des\s+arr[êe]ts\s+et\s+d[ée]cisions)?|ECHR|CEDH)\s+"
    r"(?:19|20)\d{2}(?:\s*[-‑–]\s*[IVX]+)?"
    r"|(?:Decisions\s+and\s+Reports\s+\(DR\)|DR)\s+\d+)"
)
_NAME_DATE_REPORTER_RE = re.compile(
    rf"(?P<raw>(?P<name>{_HISTORICAL_CASE_NAME})\s*,?\s*"
    r"(?:\[\s*)?(?:\(?\s*)?"
    r"(?:(?:judgment|decision|arr[êe]t|d[ée]cision)(?:\s+of|\s+du)?\s+)?"
    rf"\d{{1,2}}(?:er)?\s+{_MONTH_WORD}\s+(?:19|20)\d{{2}}"
    rf"(?:[^.;\n]{{0,160}}?{_REPORTER_TEXT})?\s*[\])]?"
    r")",
    re.I,
)
_NAME_REPORTER_RE = re.compile(
    rf"(?P<raw>(?P<name>{_HISTORICAL_CASE_NAME})"
    rf"(?:[^.;\n]|n(?:o|os)\.){{0,140}}?{_REPORTER_TEXT})",
    re.I,
)
_REVERSE_DATE_NAME_RE = re.compile(
    rf"(?P<raw>(?:the\s+)?(?:judgment|decision|arr[êe]t|d[ée]cision)\s+"
    rf"(?:of|du)\s+\d{{1,2}}(?:er)?\s+{_MONTH_WORD}\s+(?:19|20)\d{{2}}\s+"
    rf"(?:concerning|concernant)\s+(?:the\s+)?(?:case\s+of|affaire)\s+"
    rf"(?P<name>{_HISTORICAL_CASE_NAME}))",
    re.I,
)
_DISTINCT_PHASE_CUE_RE = re.compile(
    r"\b(?:(?:principal|earlier|former|previous|initial)\s+"
    r"(?:judgment|decision|arr[êe]t|d[ée]cision)|"
    r"(?:judgment|decision|arr[êe]t|d[ée]cision)\s*\(\s*"
    r"(?:preliminary objections?|exceptions pr[ée]liminaires|merits|fond|"
    r"just satisfaction|satisfaction [ée]quitable|admissibility|recevabilit[ée]))",
    re.I,
)
_PRIOR_DOCUMENT_CUE_RE = re.compile(
    rf"\b(?:judgment|decision|arr[êe]t|d[ée]cision|cited\s+above|pr[ée]cit[ée]e?)\b"
    rf"|\b\d{{1,2}}(?:er)?\s+{_MONTH_WORD}\s+(?:19|20)\d{{2}}\b",
    re.I,
)
_PRINTED_PHASE = (
    r"(?:preliminary objections?|exceptions pr[ée]liminaires|merits|fond|"
    r"just satisfaction|satisfaction [ée]quitable|admissibility|recevabilit[ée]|"
    r"friendly settlement|r[èe]glement amiable|revision|r[ée]vision|"
    r"interpretation|interpr[ée]tation)"
)


def _prior_document_cue_at(text: str, start: int, end: int) -> bool:
    """Require a cue owned by this same-party name, not merely nearby prose."""
    raw = text[start:end]
    before = text[max(0, start - 100) : start]
    after = text[end : min(len(text), end + 100)]
    return bool(
        re.match(
            r"\s*(?:\([IVXLC]+\)\s*)?(?:[,()]\s*)?"
            r"(?:(?:judgment|decision|arr[êe]t|d[ée]cision)\b|"
            r"cited\s+above\b|pr[ée]cit[ée]e?\b)",
            after,
            re.I,
        )
        or _DATE_TEXT_RE.search(raw)
        or re.match(rf"\s*[,([]?\s*{_DATE_TEXT_RE.pattern}", after, re.I)
        or re.search(
            r"(?:Commission(?:['’]s)?\s+)?(?:report|rapport)\s+"
            r"(?:on|sur)\s+(?:the\s+|l['’])?(?:application|requ[êe]te)\s+"
            r"(?:of|de|du|des)?\s*$",
            before,
            re.I,
        )
    )


_LEADING_CASE_NOISE_RE = re.compile(
    r"^(?:(?:Convention|ECHR|CEDH|Reports?|Recueil)\s*,\s*"
    r"(?:(?:and|et)\s+)?|[IVX]+\)?\s*,\s*(?:and|et)\s+)",
    re.I,
)
_SERIES_ONLY_RE = re.compile(
    r"\b(?:Series|s[ée]rie)\s+A\s+n\s*(?:o\.?|°|º)\s*\d+(?:[-‑–]\s*[A-Z])?\b",
    re.I,
)
_GROUPED_PHASE_RE = re.compile(
    r"(?P<raw>(?:(?:the|les)\s+)?(?P<name>[A-ZÀ-ÖØ-Þ][\wÀ-ÖØ-öø-ÿ'’.-]{2,80}?)\s+"
    r"(?:judgments?|arr[êe]ts?)\s*\(\s*"
    r"(?P<first>preliminary objections?|exceptions pr[ée]liminaires)\s+"
    r"(?:and|et)\s+(?P<second>merits|fond)\s*\)\s*,?\s*"
    r"(?:at|aux?|respectivement\s+aux?)?\s*§{1,2}\s*(?P<first_para>\d{1,4})\s+"
    r"(?:and|et)\s+§?\s*(?P<second_para>\d{1,4})\s+(?:respectively|respectivement)|"
    r"(?:(?:the|les)\s+)?(?P<name_rev>[A-ZÀ-ÖØ-Þ][\wÀ-ÖØ-öø-ÿ'’.-]{2,80}?)\s+"
    r"(?:judgments?|arr[êe]ts?)\s*\(\s*"
    r"(?P<first_rev>merits|fond)\s+(?:and|et)\s+"
    r"(?P<second_rev>preliminary objections?|exceptions pr[ée]liminaires)\s*\)\s*,?\s*"
    r"(?:at|aux?|respectivement\s+aux?)?\s*§{1,2}\s*(?P<first_para_rev>\d{1,4})\s+"
    r"(?:and|et)\s+§?\s*(?P<second_para_rev>\d{1,4})\s+"
    r"(?:respectively|respectivement))",
    re.I,
)
_COMMISSION_REPORT_REFERENCE_RE = re.compile(
    rf"(?P<raw>(?:report\s+of\s+the\s+Commission\s+of|"
    rf"rapport\s+de\s+la\s+Commission\s+du)\s+"
    rf"\d{{1,2}}(?:er)?\s+{_MONTH_WORD}\s+(?:19|20)\d{{2}}"
    r"(?:\s*,\s*(?:§§?|paras?\.?|paragraphes?)\s*"
    r"\d{1,4}(?:\s*[-–—]\s*\d{1,4})?)?)",
    re.I,
)
_COMMISSION_DECISION_REFERENCE_RE = re.compile(
    rf"(?P<raw>(?:(?:the\s+)?Commission(?:['’]s)?\s+)?"
    rf"(?:decision|d[ée]cision)\s+(?:of|du)\s+"
    rf"\d{{1,2}}(?:er)?\s+{_MONTH_WORD}\s+(?:19|20)\d{{2}}"
    r"(?:\s+on\s+the\s+admissibility\s+of|\s+sur\s+la\s+recevabilit[ée]\s+de)?\s+"
    r"(?:the\s+)?(?:application|requ[êe]te)\s+"
    r"n\s*(?:o\.?|°|º)\s*(?P<appno>\d{1,6}/\d{2,4})"
    r"(?:\s*,\s*(?:D\.?\s*R\.?|DR)\s+\d+\s*,\s*p\.?\s*\d+)?)",
    re.I,
)
_COMMISSION_DR_REFERENCE_RE = re.compile(
    r"(?P<raw>(?:application|requ[êe]te)\s+"
    r"n\s*(?:o\.?|°|º)\s*(?P<appno>\d{1,6}/\d{2,4})\s*,\s*"
    r"(?:D\.?\s*R\.?|DR)\s+\d+\s*,\s*p\.?\s*\d+(?:\s+et\s+seq\.)?)",
    re.I,
)
_APPLICATION_NUMBER_CUE_RE = re.compile(
    r"(?:applications?\s+)?n\s*(?:o(?:s)?\.?|[°º])\s*$",
    re.I,
)
_NAME_METADATA_SUFFIX_RE = re.compile(
    r"(?:\s*,?\s*(?:"
    r"\[(?:GC|Grand Chamber|Section)\]|"
    r"\((?:dec\.?|decision|judgment|arr[êe]t|d[ée]cision|"
    r"admissibility|recevabilit[ée]|merits|fond|no\.?\s*\d+)\)"
    r"))+\s*$",
    re.I,
)
_CITATION_INTRO_RE = re.compile(
    r"\b(?:see(?:\s+also)?|voir(?:\s+aussi)?|cf\.?)\s*"
    r"(?:,\s*(?:(?:among|inter\s+alia|parmi)\s+(?:other\s+)?authorit(?:y|ies)|"
    r"for\s+example|in\s+particular|mutatis\s+mutandis|notably|notamment)\s*)*,?\s*",
    re.I,
)
_BAD_FALLBACK_NAME_START_RE = re.compile(
    r"^(?:the\s+court|la\s+cour|article\s+\d|paragraph\s+\d|"
    r"the\s+law|human\s+rights|rights\b|"
    r"the\s+applicant|the\s+government|le\s+requ[ée]rant|le\s+gouvernement)\b",
    re.I,
)
_ACCENT_EQUIVALENTS = {
    "a": "aàáâãäåāăą",
    "c": "cçćĉċč",
    "d": "dďđ",
    "e": "eèéêëēĕėęě",
    "g": "gĝğġģ",
    "i": "iìíîïĩīĭįı",
    "l": "lĺļľŀł",
    "n": "nñńņň",
    "o": "oòóôõöøōŏő",
    "r": "rŕŗř",
    "s": "sśŝşšș",
    "t": "tţťŧț",
    "u": "uùúûüũūŭůűų",
    "y": "yýÿŷ",
    "z": "zźżž",
}


def _fold(value: str) -> str:
    value = value.replace("‑", "-").replace("–", "-").replace("\u2014", "-")
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(char)
    )


def _unicode_case_name_valid(value: str) -> bool:
    """Require genuinely capitalised parties without limiting Unicode scripts."""
    value = re.sub(r"\[\s*(?=(?:v\.?|c\.?|contre|against)\b)", "", value, flags=re.I)
    parties = _PARTY_RE.split(value, maxsplit=1)
    if len(parties) != 2:
        return False
    for index, party in enumerate(parties):
        candidate = party.strip(" [](),.")
        if index == 1:
            candidate = re.sub(
                r"^(?:(?:the|la|le)\s+)?(?:former\s+)?",
                "",
                candidate,
                flags=re.I,
            )
        first_letter = next((char for char in candidate if char.isalpha()), "")
        if not first_letter or not first_letter.isupper():
            return False
    return True


def _case_name_matches(value: str) -> list[re.Match[str]]:
    """Return valid name matches, preferring the broad historical grammar."""
    matches: dict[tuple[int, int], re.Match[str]] = {}
    for pattern in (_HISTORICAL_NAME_RE, _DISCOVERY_NAME_RE):
        for match in pattern.finditer(value):
            if _unicode_case_name_valid(match.group("name")):
                matches[(match.start(), match.end())] = match
    return sorted(matches.values(), key=lambda match: (match.end(), -match.start()))


@lru_cache(maxsize=1)
def _authority_titles_by_appno() -> dict[str, tuple[str, ...]]:
    """Index the packaged bilingual authority without making it an identity oracle."""
    from .authority import load_authority

    grouped: dict[str, list[str]] = defaultdict(list)
    for entry in load_authority().entries:
        if not entry.title:
            continue
        for appno in entry.appnos:
            if entry.title not in grouped[appno]:
                grouped[appno].append(entry.title)
    return {key: tuple(values) for key, values in grouped.items()}


def _authority_name_before_appno(prefix: str, appno: str) -> tuple[int, int, str] | None:
    """Find an official title exactly where it is printed before an app number.

    The application number selects possible spellings only.  A title is
    returned solely when its complete text is also present in the source.
    """
    candidates: list[tuple[int, int, str]] = []
    for title in _authority_titles_by_appno().get(appno, ()):
        for match in _pattern(title).finditer(prefix):
            candidates.append((match.start(), match.end(), match.group()))
        # HUDOC/SCL occasionally inserts a nested editorial bracket directly
        # before the connector: ``Öcalan ([v. Turkey ...])``.  Accept that
        # punctuation only when both complete official parties and the exact
        # application number are present.
        connector = _PARTY_RE.search(title)
        if connector is not None:
            applicant = title[: connector.start()].strip()
            respondent = title[connector.end() :].strip()
            relaxed = re.compile(
                _pattern(applicant).pattern
                + r"\s*[\[(]*\s*(?:v\.?|c\.?|contre|against)\s+"
                + _pattern(respondent).pattern,
                re.I,
            )
            for match in relaxed.finditer(prefix):
                candidates.append((match.start(), match.end(), match.group()))
    if not candidates:
        return None
    return max(candidates, key=lambda value: (value[1], value[1] - value[0]))


def _authority_applicant_before_appno(
    prefix: str,
    appno: str,
    *,
    language: str | None,
) -> tuple[int, int, str, str] | None:
    """Match a printed applicant-only name against the exact app-number authority.

    Some Court references omit the respondent entirely, for example
    ``Guðmundur Andri Ástráðsson ([GC], no. 26374/18 ...)``.  The application
    number is used only to propose official titles; an applicant alias must be
    printed immediately before the numbered citation envelope.
    """
    candidates: list[tuple[int, int, str, str, int]] = []
    wanted_language = (language or "").upper()
    for title in _authority_titles_by_appno().get(appno, ()):
        connector = _PARTY_RE.search(title)
        if connector is None:
            continue
        applicant = title[: connector.start()].strip(" \"“”')(")
        if len(_key(applicant)) < 4 or _key(applicant) in _GENERIC:
            continue
        for match in _pattern(applicant).finditer(prefix):
            between = prefix[match.end() :]
            if len(between) > 80 or _APPLICATION_NUMBER_CUE_RE.search(between) is None:
                continue
            printed = match.group()
            connector_text = connector.group().casefold()
            language_score = int(
                (wanted_language == "ENG" and connector_text.startswith("v"))
                or (wanted_language == "FRE" and not connector_text.startswith("v"))
            )
            candidates.append(
                (match.start(), match.end(), printed, _strip_phase(title), language_score)
            )
    if not candidates:
        return None
    start, end, printed, title, _ = max(
        candidates,
        key=lambda value: (value[4], value[1], value[1] - value[0], -len(value[3])),
    )
    return start, end, printed, title


def _fallback_name_before_appno(prefix: str) -> tuple[int, int, str] | None:
    """Recover a connector-bounded case name next to ``no.``/``nos.``.

    This deliberately does not broaden the general case-name grammar.  It is
    available only inside a strong application-number envelope and requires
    an explicit party connector plus a conservative citation-introduction or
    structural boundary.
    """
    cue = _APPLICATION_NUMBER_CUE_RE.search(prefix)
    if cue is None:
        return None
    name_end = cue.start()
    before_cue = prefix[:name_end].rstrip(" ,")
    while (metadata := _NAME_METADATA_SUFFIX_RE.search(before_cue)) is not None:
        before_cue = before_cue[: metadata.start()].rstrip(" ,")
    name_end = len(before_cue)
    connectors = list(_PARTY_RE.finditer(before_cue))
    if not connectors:
        return None
    connector = connectors[-1]

    search_floor = max(0, connector.start() - 220)
    leading = before_cue[search_floor : connector.start()]
    intro_matches = list(_CITATION_INTRO_RE.finditer(leading))
    boundaries = [
        match.end() for match in re.finditer(r"[;\n]", before_cue[search_floor : connector.start()])
    ]
    boundaries.extend(match.end() for match in intro_matches)
    boundaries.extend(
        match.end()
        for match in re.finditer(
            r"\(\s*(?=(?:the\s+)?[A-ZÀ-ÖØ-Þ])",
            before_cue[search_floor : connector.start()],
        )
        if ")" not in leading[match.end() :]
    )
    if not boundaries:
        return None
    name_start = search_floor + max(boundaries)

    candidate = before_cue[name_start:name_end]
    trim = re.match(
        r"\s*(?:(?:and|et)\s+)?(?:(?:the\s+)?(?:case|affaire)\s+(?:of|de)\s+)?",
        candidate,
        flags=re.I,
    )
    if trim:
        name_start += trim.end()
    candidate = before_cue[name_start:name_end].strip(" ,[]")
    name_start += len(before_cue[name_start:name_end]) - len(
        before_cue[name_start:name_end].lstrip(" ,[]")
    )
    name_end = name_start + len(candidate)
    applicant = candidate[: max(0, connector.start() - name_start)].strip()
    if (
        not candidate
        or APPNO_REGEX.search(candidate[: max(0, connector.start() - name_start)])
        or len(candidate) > 230
        or len(candidate.split()) > 30
        or _BAD_FALLBACK_NAME_START_RE.search(candidate)
        or not _unicode_case_name_valid(candidate)
        or len(applicant.split()) > 20
    ):
        return None
    return name_start, name_end, candidate


def _fallback_crosses_prior_citation(
    prefix: str,
    fallback: tuple[int, int, str],
    strict_match: re.Match[str],
) -> bool:
    """Reject a broad fallback that swallowed earlier authorities in a list."""
    if fallback[0] >= strict_match.start():
        return False
    between = prefix[fallback[0] : strict_match.start()]
    return bool(
        ";" in between
        or _CUE_RE.search(between)
        or APPNO_REGEX.search(between)
        or _DATE_TEXT_RE.search(between)
        or _PARTY_RE.search(between)
    )


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _fold(value)).strip()


def _match_key(value: str) -> str:
    """Normalize the extra Unicode letters matched by ``re.IGNORECASE``.

    Python treats ASCII ``I`` and Turkish dotless ``ı`` as case-insensitive
    equivalents.  Citation identity keeps using :func:`_key`; this broader key
    is only a necessary-token prefilter before the authoritative regex match.
    """
    return _key(value.replace("ı", "i"))


def _pattern(value: str) -> re.Pattern[str]:
    value = re.sub(r"([\[(])\s+", r"\1", value)
    value = re.sub(r"\s+([])])", r"\1", value)

    def accent_escape(bit: str) -> str:
        output: list[str] = []
        for char in bit:
            folded = _fold(char)
            base = folded if len(folded) == 1 else char.casefold()
            equivalents = _ACCENT_EQUIVALENTS.get(base)
            output.append(f"[{equivalents}]" if equivalents else re.escape(char))
        return "".join(output)

    raw_bits = [bit for bit in re.split(r"\s+", value.strip()) if bit]
    bits = [accent_escape(bit) for bit in raw_bits]
    # Old HUDOC/XMI exports sometimes contain U+FFFD where a non-breaking
    # hyphen was stored as an XML-illegal control byte.  Treat it only as an
    # inter-token separator; it must never manufacture or alter identity.
    name_separator = r"(?:\s+|[-‑–\u2014\ufffd])"
    body_parts: list[str] = []
    for index, bit in enumerate(bits):
        if index:
            previous_raw = raw_bits[index - 1]
            current_raw = raw_bits[index]
            # Official SCL forms commonly insert spaces between initials
            # (``M. R.``) while the rendered judgment prints ``M.R.``.
            # Treat only that typographic boundary as optional.
            dotted_initials = bool(
                re.fullmatch(r"[^\W\d_]\.", previous_raw, flags=re.UNICODE)
                and re.fullmatch(r"[^\W\d_]\.", current_raw, flags=re.UNICODE)
            )
            body_parts.append(r"\s*" if dotted_initials else name_separator)
        body_parts.append(bit)
    body = "".join(body_parts).replace(r"\-", name_separator)
    # Spine v1 used BeautifulSoup's space separator while flattening inline
    # markup.  That could materialise ``( re Crimea )`` even though the HTML
    # renders ``(re Crimea)``.  Keep the delimiters authoritative while
    # accepting optional whitespace immediately inside them so cached v1
    # spines remain searchable.
    body = (
        body.replace(r"\(", r"\(\s*")
        .replace(r"\)", r"\s*\)")
        .replace(r"\[", r"\[\s*")
        .replace(r"\]", r"\s*\]")
    )
    left = r"(?<!\w)" if value[:1].isalnum() else ""
    right = r"(?!\w)" if value[-1:].isalnum() else ""
    return re.compile(left + body + right, re.IGNORECASE)


def _anchor_token(value: str) -> str | None:
    """Return a necessary normalized token for a successful alias match."""
    tokens = _match_key(value).split()
    return max(tokens, key=len) if tokens else None


def _strip_phase(value: str) -> str:
    previous = ""
    while value != previous:
        previous = value
        value = _PHASE_RE.sub("", value).strip(" ,;")
    return value


def _printed_title_alias(raw: str) -> str | None:
    """Retain title parentheticals while dropping bibliographic tail material."""
    head = raw.split(",", 1)[0].strip()
    head = re.sub(
        rf"\s+\(?(?:judgment|decision|arr[êe]t|d[ée]cision)(?:\s+of|\s+du)?\s+"
        rf"\d{{1,2}}(?:er)?\s+{_MONTH_WORD}\s+(?:19|20)\d{{2}}.*$",
        "",
        head,
        flags=re.I,
    ).strip()
    head = re.sub(r"\s*\[GC\]\s*$", "", head, flags=re.I)
    return head if _PARTY_RE.search(head) else None


def _distinctive_short_forms(name: str) -> list[str]:
    party = _PARTY_RE.split(_strip_phase(name), maxsplit=1)[0].strip(" ,")
    party = re.sub(
        r"^(?:l['’]\s*)?(?:arr[êe]t|d[ée]cision|affaire|case(?:\s+of)?)\s+",
        "",
        party,
        flags=re.I,
    ).strip()
    if not party:
        return []
    forms = [party]
    article_stripped = re.sub(r"^(?:the|les|la|le|l['’])\s+", "", party, flags=re.I)
    if article_stripped and article_stripped != party:
        forms.append(article_stripped)
    corporate_head = party.split(",", 1)[0].strip()
    if corporate_head and corporate_head != party:
        forms.append(corporate_head)
    principal = re.sub(
        r"\s+(?:and\s+Others|et\s+autres)$",
        "",
        party,
        flags=re.I,
    ).strip()
    if principal and principal != party:
        forms.append(principal)
    words = re.findall(r"[\wÀ-ÖØ-öø-ÿ'’-]+", party, re.UNICODE)
    if words:
        forms.append(words[-1])
    out: list[str] = []
    for value in forms:
        folded = _key(value)
        if len(folded) < 3 or folded in _GENERIC or folded in _STATE_WORDS:
            continue
        if value not in out:
            out.append(value)
    return out


def _authority_short_identity(entry: CitationAuthorityEntry) -> str:
    """Group bilingual authority rows without inventing target identity."""
    if entry.appnos:
        return "appno:" + "|".join(sorted(entry.appnos))
    reporter = entry.reporter.key if entry.reporter is not None else ""
    applicant = _key(_PARTY_RE.split(_strip_phase(entry.title or ""), maxsplit=1)[0])
    return (
        f"legacy:{applicant}:{entry.date or ''}:{reporter}:"
        f"{entry.document_kind}:{entry.procedural_phase}"
    )


@lru_cache(maxsize=1)
def _authority_short_aliases() -> tuple[
    tuple[str, tuple[CitationAuthorityEntry, ...], re.Pattern[str], str | None], ...
]:
    """Return official applicant aliases usable only with explicit cues.

    These aliases are never used as free-standing finders. They are consulted
    only for an adjacent cited-above/précité or judgment/decision cue. Entries
    for multiple procedural documents in one application deliberately share
    one application identity.
    """
    from .authority import load_authority

    grouped: dict[str, list[tuple[str, CitationAuthorityEntry]]] = defaultdict(list)
    for entry in load_authority().entries:
        if not entry.title:
            continue
        identity = _authority_short_identity(entry)
        for alias in _distinctive_short_forms(entry.title):
            grouped[_key(alias)].append((identity, entry))
    prepared: list[tuple[str, tuple[CitationAuthorityEntry, ...], re.Pattern[str], str | None]] = []
    for key, values in grouped.items():
        aliases = [
            short
            for _, entry in values
            for short in _distinctive_short_forms(entry.title or "")
            if _key(short) == key
        ]
        alias = max(aliases, key=len)
        entries = tuple(value for _, value in values)
        prepared.append((alias, entries, _pattern(alias), _anchor_token(alias)))
    prepared.sort(key=lambda value: (-len(value[0]), _key(value[0])))
    return tuple(prepared)


@lru_cache(maxsize=1)
def _historical_short_aliases() -> tuple[
    tuple[str, tuple[CitationAuthorityEntry, ...], re.Pattern[str], str | None], ...
]:
    """Expose unique catalogued historical titles to the same cue gates."""
    from .catalog import load_historical_catalog

    converted: dict[str, CitationAuthorityEntry] = {}
    for historical_entry in load_historical_catalog().entries:
        if not historical_entry.title or not (
            historical_entry.target_ecli or historical_entry.target_itemid
        ):
            continue
        payload = (
            f"{historical_entry.reporter_key}|{historical_entry.normalized_title}|"
            f"{historical_entry.date}|{historical_entry.target_ecli}|"
            f"{historical_entry.target_itemid}"
        )
        entry_id = "historical:" + hashlib.sha256(payload.encode()).hexdigest()
        converted[entry_id] = CitationAuthorityEntry(
            entry_id=entry_id,
            entry_source="curated_supplement",
            language=(
                "fra" if (historical_entry.title or "").casefold().startswith("affaire") else "eng"
            ),
            citation=historical_entry.title,
            normalized_citation=historical_entry.normalized_title,
            title=historical_entry.title,
            normalized_title=historical_entry.normalized_title,
            appnos=list(historical_entry.appnos),
            date=historical_entry.date,
            document_kind=historical_entry.document_kind,
            target_ecli=historical_entry.target_ecli,
            target_itemid=historical_entry.target_itemid,
        )
    grouped: dict[str, list[CitationAuthorityEntry]] = defaultdict(list)
    aliases: dict[str, list[str]] = defaultdict(list)
    for entry in converted.values():
        for alias in _distinctive_short_forms(entry.title or ""):
            key = _key(alias)
            grouped[key].append(entry)
            aliases[key].append(alias)
    prepared = [
        (
            max(aliases[key], key=len),
            tuple(entries),
            _pattern(max(aliases[key], key=len)),
            _anchor_token(max(aliases[key], key=len)),
        )
        for key, entries in grouped.items()
        if len({_authority_short_identity(entry) for entry in entries}) == 1
    ]
    prepared.sort(key=lambda value: (-len(value[0]), _key(value[0])))
    return tuple(prepared)


@lru_cache(maxsize=1)
def _authority_series_groups() -> dict[str, tuple[tuple[CitationAuthorityEntry, ...], ...]]:
    """Index official Series A rows by equivalent bibliographic work.

    English and French rows remain distinct authority records, but their
    declared equivalence links allow one printed Series A locator to establish
    a single bibliographic locus.  A non-unique locator is never returned as a
    usable anchor.
    """
    from .authority import load_authority

    grouped: dict[str, dict[str, list[CitationAuthorityEntry]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for entry in load_authority().entries:
        if entry.reporter is None or entry.reporter.family != "series_a":
            continue
        equivalence_key = min((entry.entry_id, *entry.equivalent_entry_ids))
        grouped[entry.reporter.key][equivalence_key].append(entry)
    return {
        reporter_key: tuple(
            tuple(sorted(entries, key=lambda value: (value.language or "", value.entry_id)))
            for _, entries in sorted(groups.items())
        )
        for reporter_key, groups in grouped.items()
    }


def _authority_short_mention(
    case: Case,
    entries: tuple[CitationAuthorityEntry, ...],
    *,
    raw: str,
    block: DocumentBlock,
    start: int,
    end: int,
    ordinal: int,
    method: str,
    printed_name: str,
) -> CitationMention | None:
    """Build an application-scoped mention from compatible authority rows."""
    printed_phase = infer_procedural_phase(raw)
    if re.search(r"\(\s*(?:merits|fond)\s*\)", raw, re.I):
        printed_phase = "merits"
    if printed_phase != "unknown":
        phase_entries = tuple(
            entry for entry in entries if entry.procedural_phase in {"unknown", printed_phase}
        )
        if phase_entries:
            entries = phase_entries
    authority_identities = {_authority_short_identity(entry) for entry in entries}
    mention_id = hashlib.sha256(
        (
            f"{case.itemid}|{block.block_id}|{start}|{end}|authority-short|"
            + "||".join(sorted(authority_identities))
        ).encode()
    ).hexdigest()
    evidence = {
        "method": (
            method if len(authority_identities) == 1 else method.replace("unique", "ambiguous")
        ),
        "block_start": start,
        "block_end": end,
        "printed_cited_name": printed_name,
        "authority_candidate_identities": sorted(authority_identities),
        "authority_candidate_titles": sorted({entry.title for entry in entries if entry.title}),
        "authority_candidate_applications": sorted(
            {appno for entry in entries for appno in entry.appnos}
        ),
    }
    if len(authority_identities) != 1:
        return CitationMention(
            mention_id=mention_id,
            reference_hash=hashlib.sha256(normalize_reference(raw).casefold().encode()).hexdigest(),
            source_itemid=case.itemid,
            source_ecli=case.ecli,
            source_appnos=sorted(_case_appnos(case)),
            source_language=case.language,
            source_date=case.kp_date or case.judgement_date or case.decision_date,
            ordinal=ordinal,
            raw_ref=raw,
            normalized_ref=normalize_reference(raw).casefold(),
            cited_name=raw.split(",", 1)[0].strip(),
            origin="text_discovery",
            source_section=block.section,
            source_block_id=block.block_id,
            source_para_id=block.legal_para_id or block.para_id,
            source_opinion_id=block.opinion_id,
            source_footnote_id=block.footnote_id,
            source_invoking_block_ids=list(block.referenced_by_block_ids),
            source_invoking_para_ids=list(block.referenced_by_para_ids),
            discovery_evidence=evidence,
        )
    language = (case.language or "").casefold()
    selected = sorted(
        entries,
        key=lambda entry: (
            entry.language != language,
            len(entry.title or ""),
            entry.entry_id,
        ),
    )[0]
    title = _strip_phase(selected.title or "")
    appnos = sorted({appno for entry in entries for appno in entry.appnos})
    if not title:
        return None
    parsed = parse_scl_mentions(
        case.model_copy(
            update={
                "scl": f"{title}, no. {appnos[0]}" if appnos else title,
                "sclappnos": [],
            }
        )
    )
    if not parsed:
        return None
    dates = {entry.date for entry in entries} - {None}
    kinds = {entry.document_kind for entry in entries} - {"unknown"}
    phases = {entry.procedural_phase for entry in entries} - {"unknown"}
    return parsed[0].model_copy(
        update={
            "mention_id": mention_id,
            "reference_hash": hashlib.sha256(
                normalize_reference(raw).casefold().encode()
            ).hexdigest(),
            "ordinal": ordinal,
            "raw_ref": raw,
            "normalized_ref": normalize_reference(raw).casefold(),
            "origin": "text_discovery",
            "cited_name": title,
            "respondent": extract_respondent(title),
            "explicit_appnos": appnos,
            "target_date": next(iter(dates)) if len(dates) == 1 else None,
            "document_kind": next(iter(kinds)) if len(kinds) == 1 else "unknown",
            "procedural_phase": (
                printed_phase
                if printed_phase != "unknown"
                else next(iter(phases))
                if len(phases) == 1
                else "unknown"
            ),
            "source_section": block.section,
            "source_block_id": block.block_id,
            "source_para_id": block.legal_para_id or block.para_id,
            "source_opinion_id": block.opinion_id,
            "source_footnote_id": block.footnote_id,
            "source_invoking_block_ids": list(block.referenced_by_block_ids),
            "source_invoking_para_ids": list(block.referenced_by_para_ids),
            "discovery_evidence": evidence,
        }
    )


def _isolated_italic_span(block: DocumentBlock, start: int, end: int) -> bool:
    """Require typography to belong to the alias, not a larger italic phrase."""
    overlapping = [
        run for run in block.inline_runs if run.italic and run.start < end and run.end > start
    ]
    if not overlapping or min(run.start for run in overlapping) > start:
        return False
    if max(run.end for run in overlapping) < end:
        return False
    run_start = min(run.start for run in overlapping)
    run_end = max(run.end for run in overlapping)
    styled = block.text[run_start:run_end].strip(" \t\r\n,;:()[]{}\"“”'’")
    styled = re.sub(r"\s*\[(?:GC|Grand Chamber|Section)\]\s*$", "", styled, flags=re.I)
    printed = block.text[start:end].strip(" \t\r\n,;:()[]{}\"“”'’")
    return _key(styled) == _key(printed)


def _explicit_back_reference_name(text: str, cue_start: int) -> tuple[int, str] | None:
    """Return a conservative unresolved name immediately owning a back-reference."""
    window_start = max(0, cue_start - 140)
    prefix = text[window_start:cue_start]
    boundary = max(
        prefix.rfind(";"),
        prefix.rfind("("),
        prefix.rfind("\n"),
    )
    candidate_start = boundary + 1
    citation_intros = list(_CITATION_INTRO_RE.finditer(prefix))
    if citation_intros:
        candidate_start = max(candidate_start, citation_intros[-1].end())
    candidate = prefix[candidate_start:]
    intro = re.match(
        r"\s*(?:(?:see|voir|cf\.?|and|et|or|ou|compare|contrast)\s+)*",
        candidate,
        re.I,
    )
    if intro is not None:
        candidate_start += intro.end()
        candidate = candidate[intro.end() :]
    candidate = re.sub(r"\s*\[(?:GC|Grand Chamber|Section)\]\s*$", "", candidate, flags=re.I)
    candidate = re.sub(r"\s*\(\s*(?:n(?:o\.?|°|º))\s*\d+\s*\)\s*$", "", candidate, flags=re.I)
    candidate = candidate.strip(" ,[]()")
    absolute_start = window_start + candidate_start
    while absolute_start < cue_start and text[absolute_start].isspace():
        absolute_start += 1
    if not candidate or len(candidate) > 110 or not candidate[:1].isupper():
        return None
    key = _key(candidate)
    if (
        len(key) < 3
        or key in _GENERIC
        or key in _STATE_WORDS
        or _BAD_FALLBACK_NAME_START_RE.search(candidate)
        or re.match(
            r"^(?:articles?|protocol|rules?|convention|court|commission|government|applicant)\b",
            candidate,
            re.I,
        )
        or re.match(r"^[IVXLC]+\s*[,)]", candidate)
        or re.search(r"\b(?:both|all|tous|toutes)\s*$", candidate, re.I)
    ):
        return None
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ'’.-]+", candidate)
    if not words or not any(word[:1].isupper() for word in words):
        return None
    return absolute_start, candidate


def _unresolved_back_reference_mention(
    case: Case,
    block: DocumentBlock,
    *,
    start: int,
    end: int,
    cited_name: str,
    ordinal: int,
    method: str,
) -> CitationMention:
    raw = block.text[start:end]
    mention_id = hashlib.sha256(
        f"{case.itemid}|{block.block_id}|{start}|{end}|{method}".encode()
    ).hexdigest()
    return CitationMention(
        mention_id=mention_id,
        reference_hash=hashlib.sha256(normalize_reference(raw).casefold().encode()).hexdigest(),
        source_itemid=case.itemid,
        source_ecli=case.ecli,
        source_appnos=sorted(_case_appnos(case)),
        source_language=case.language,
        source_date=case.kp_date or case.judgement_date or case.decision_date,
        ordinal=ordinal,
        raw_ref=raw,
        normalized_ref=normalize_reference(raw).casefold(),
        cited_name=cited_name,
        origin="text_discovery",
        source_section=block.section,
        source_block_id=block.block_id,
        source_para_id=block.legal_para_id or block.para_id,
        source_opinion_id=block.opinion_id,
        source_footnote_id=block.footnote_id,
        source_invoking_block_ids=list(block.referenced_by_block_ids),
        source_invoking_para_ids=list(block.referenced_by_para_ids),
        discovery_evidence={
            "method": method,
            "block_start": start,
            "block_end": end,
            "printed_cited_name": cited_name,
        },
    )


def _same_source_party(case: Case, owner: _Owner) -> bool:
    """Identify aliases that could merely name the current litigant or case."""
    cited = owner.mention.cited_name or ""
    source = case.docname or ""
    cited_parts = _PARTY_RE.split(_strip_phase(cited), maxsplit=1)
    source_parts = _PARTY_RE.split(_strip_phase(source), maxsplit=1)
    if not cited_parts or not source_parts:
        return False
    cited_applicant = _key(cited_parts[0])
    source_applicant = _key(source_parts[0])
    return bool(
        cited_applicant
        and source_applicant
        and (cited_applicant == source_applicant or cited_applicant in source_applicant)
    )


def _case_appnos(case: Case) -> set[str]:
    """Return canonical source app numbers even from legacy array-like cells."""
    return {match.group(0) for value in case.appno for match in APPNO_REGEX.finditer(str(value))}


def _respondent_is_convention_state(value: str | None) -> bool:
    key = _key(value or "")
    key = re.sub(r"\s+[ivxlcdm]+$", "", key)
    if not key:
        return False
    return any(
        key == state
        or key.endswith(f" {state}")
        or key.startswith(f"{state} and ")
        or f" and {state}" in key
        for state in _STATE_WORDS
    )


def _party_name_variants(name: str) -> list[str]:
    """Generate close English/French connector variants without bare states."""
    match = _PARTY_RE.search(name)
    if match is None:
        return []
    applicant = name[: match.start()].strip()
    respondent = name[match.end() :].strip()
    if not applicant or not respondent:
        return []
    return [
        f"{applicant} v. {respondent}",
        f"{applicant} v {respondent}",
        f"{applicant} c. {respondent}",
        f"{applicant} c {respondent}",
        f"{applicant} contre {respondent}",
    ]


@dataclass(frozen=True)
class _Owner:
    mention: CitationMention
    resolution: CitationResolution | None

    @property
    def identity(self) -> str:
        mention = self.mention
        if mention.explicit_ecli:
            return f"ecli:{mention.explicit_ecli}"
        if mention.explicit_itemid:
            return f"itemid:{mention.explicit_itemid}"
        phase = f"{mention.document_kind}:{mention.procedural_phase}"
        if mention.explicit_appnos:
            return f"appno:{'|'.join(sorted(mention.explicit_appnos))}:{phase}"
        name = _key(mention.cited_name or "")
        date = mention.target_date.isoformat() if mention.target_date else ""
        if name:
            return f"name:{name}:{date}:{phase}"
        if mention.reporter:
            return f"reporter:{mention.reporter.key}:{date}:{phase}"
        return f"reference:{mention.reference_hash}"


@dataclass(frozen=True)
class _Alias:
    text: str
    key: str
    owner: _Owner
    finder: str
    strong: bool


def _owners(case: Case, resolutions: Iterable[CitationResolution] | None) -> list[_Owner]:
    """Return one owner for each authoritative SCL mention.

    Inclusive callers may supply resolutions for thousands of text-discovered
    envelopes as well as SCL mentions.  Seeding the gazetteer with every one
    of those envelopes makes repeated discoveries of the same authority look
    like competing owners, especially when one cached lookup is unresolved.
    SCL owns the baseline here; genuinely non-covered text discoveries are
    appended after identity coverage is evaluated in
    :func:`extract_citation_occurrences`.
    """
    supplied = {value.mention.mention_id: value for value in (resolutions or [])}
    return [
        _Owner(mention, supplied.get(mention.mention_id)) for mention in parse_scl_mentions(case)
    ]


def _owner_appnos(owner: _Owner) -> set[str]:
    """Return printed/catalogued app numbers without consulting resolution.

    Detection must expose the same physical loci whether target resolutions
    are supplied or not.  Text discovery enriches unique historical reporters
    from the packaged catalog before this point, so the gazetteer never needs
    a resolved candidate merely to recognise a same-application authority.
    """
    return set(owner.mention.explicit_appnos)


def _aliases(
    owner: _Owner,
    *,
    source_appnos: set[str] | None = None,
) -> list[tuple[str, str, bool]]:
    mention = owner.mention
    values: list[tuple[str, str, bool]] = []
    same_application = bool((source_appnos or set()).intersection(_owner_appnos(owner)))

    def add(
        value: str | None,
        finder: str,
        strong: bool = True,
        *,
        minimum_length: int = 5,
    ) -> None:
        value = (value or "").strip()
        if len(value) >= minimum_length:
            values.append((value, finder, strong))

    raw_finder = (
        str(mention.discovery_evidence.get("method", "text_discovery"))
        if mention.origin == "text_discovery"
        else "scl_reference"
    )
    raw_alias = re.split(
        r"(?<=[.)])\s+(?=(?:Law|Act|Code|Loi|D[ée]cret|Gesetz)\s+"
        r"n(?:o|os|°|º)\b)",
        mention.raw_ref,
        maxsplit=1,
        flags=re.I,
    )[0]
    add(raw_alias, raw_finder)
    if not same_application:
        add(mention.cited_name, "full_name")
        add(_printed_title_alias(mention.raw_ref), "printed_title")
    else:
        printed_title = _printed_title_alias(mention.raw_ref)
        add(printed_title, "same_application_full_name", False)
        if printed_title:
            add(
                re.sub(
                    r"\s*\((?:dec\.?|decision|admissibility|recevabilit[ée])\)\s*$",
                    "",
                    printed_title,
                    flags=re.I,
                ),
                "same_application_full_name",
                False,
            )
    if mention.cited_name:
        phase_stripped = _strip_phase(mention.cited_name)
        if not same_application:
            add(phase_stripped, "full_name")
            for variant in _party_name_variants(phase_stripped):
                add(variant, "party_variant")
        date_match = _DATE_TEXT_RE.search(mention.raw_ref)
        if date_match:
            add(f"{phase_stripped}, {date_match.group()}", "name_date")
        for short in _distinctive_short_forms(mention.cited_name):
            add(
                short,
                "same_application_short_form" if same_application else "short_form",
                False,
                minimum_length=3,
            )
            if mention.procedural_phase == "preliminary_objections":
                add(
                    f"{short} judgment (preliminary objections)",
                    "phase_short_form",
                )
                add(
                    f"arrêt {short} (exceptions préliminaires)",
                    "phase_short_form",
                )
            elif mention.procedural_phase == "merits":
                add(f"{short} judgment (merits)", "phase_short_form")
                add(f"arrêt {short} (fond)", "phase_short_form")
    if not same_application:
        for appno in mention.explicit_appnos:
            add(appno, "application_number")
    add(mention.explicit_ecli, "ecli")
    if mention.reporter and mention.reporter.family == "series_a":
        add(mention.reporter.raw, "reporter")
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str, bool]] = []
    for value, finder, strong in values:
        key = _key(value)
        if not key or (key, finder) in seen:
            continue
        seen.add((key, finder))
        out.append((value, finder, strong))
    return out


def _gazetteer(
    owners: list[_Owner],
    *,
    source_appnos: set[str] | None = None,
) -> tuple[list[_Alias], dict[str, list[_Alias]]]:
    grouped: dict[str, list[_Alias]] = defaultdict(list)
    for owner in owners:
        for text, finder, strong in _aliases(owner, source_appnos=source_appnos):
            grouped[_key(text)].append(
                _Alias(text=text, key=_key(text), owner=owner, finder=finder, strong=strong)
            )
    accepted: list[_Alias] = []
    ambiguous: dict[str, list[_Alias]] = {}
    for key, entries in grouped.items():
        identities = {entry.owner.identity for entry in entries}
        if len(identities) > 1:
            # Text discovery can print the same authority first as a formatted
            # title and later with its application number.  Treat the weaker
            # title-only owner as duplicate provenance only when all printed
            # identity fields are compatible.  This is deliberately
            # resolution-neutral so supplied targets cannot create loci.
            appno_sets = {
                tuple(sorted(entry.owner.mention.explicit_appnos))
                for entry in entries
                if entry.owner.mention.explicit_appnos
            }
            title_keys = {
                _key(_strip_phase(entry.owner.mention.cited_name or ""))
                for entry in entries
                if entry.owner.mention.cited_name
            }
            dates = {
                entry.owner.mention.target_date
                for entry in entries
                if entry.owner.mention.target_date
            }
            kinds = {
                entry.owner.mention.document_kind
                for entry in entries
                if entry.owner.mention.document_kind != "unknown"
            }
            phases = {
                entry.owner.mention.procedural_phase
                for entry in entries
                if entry.owner.mention.procedural_phase != "unknown"
            }
            exact_ids = {
                (entry.owner.mention.explicit_ecli, entry.owner.mention.explicit_itemid)
                for entry in entries
                if entry.owner.mention.explicit_ecli or entry.owner.mention.explicit_itemid
            }
            if (
                len(appno_sets) == 1
                and len(title_keys) == 1
                and len(dates) <= 1
                and len(kinds) <= 1
                and len(phases) <= 1
                and len(exact_ids) <= 1
            ):
                numbered = [entry for entry in entries if entry.owner.mention.explicit_appnos]
                numbered_identities = {entry.owner.identity for entry in numbered}
                if len(numbered_identities) == 1:
                    entries = numbered
                    identities = numbered_identities
        if len(identities) > 1:
            ambiguous[key] = entries
            continue
        # Prefer the first SCL entry for duplicate aliases resolving to one target.
        chosen = sorted(entries, key=lambda value: (not value.strong, value.owner.mention.ordinal))[
            0
        ]
        accepted.append(chosen)
    accepted.sort(
        key=lambda value: (-len(value.text), not value.strong, value.owner.mention.ordinal)
    )
    return accepted, ambiguous


def _one_alias_per_identity(entries: list[_Alias]) -> dict[str, _Alias]:
    selected: dict[str, _Alias] = {}
    for entry in entries:
        prior = selected.get(entry.owner.identity)
        if prior is None or (entry.strong and not prior.strong):
            selected[entry.owner.identity] = entry
    return selected


def _citation_evidence_window(text: str, start: int, end: int) -> str:
    """Return following bibliographic evidence without entering the next cite."""
    limit = min(len(text), end + 240)
    following = text[end:limit]
    boundaries = [
        match.start()
        for pattern in (r"[;\n]", r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Þ])")
        if (match := re.search(pattern, following))
    ]
    following_names = _case_name_matches(following)
    if following_names:
        boundaries.append(following_names[0].start())
    if boundaries:
        following = following[: min(boundaries)]
    return text[max(0, start - 24) : end] + following


def _printed_evidence_alias(
    entries: list[_Alias], text: str, start: int, end: int
) -> _Alias | None:
    """Disambiguate a shared title only with evidence printed at this locus."""
    aliases = _one_alias_per_identity(entries)
    remaining = dict(aliases)
    context = _citation_evidence_window(text, start, end)
    used_evidence = False

    appnos = set(APPNO_REGEX.findall(context))
    if appnos:
        compatible = {
            identity: alias
            for identity, alias in remaining.items()
            if appnos.intersection(alias.owner.mention.explicit_appnos)
        }
        if compatible:
            remaining = compatible
            used_evidence = True

    printed_date = parse_reference_date(context)
    if printed_date:
        compatible = {
            identity: alias
            for identity, alias in remaining.items()
            if alias.owner.mention.target_date == printed_date
        }
        if compatible:
            remaining = compatible
            used_evidence = True

    reporter = parse_reporter(context)
    if reporter:
        compatible = {
            identity: alias
            for identity, alias in remaining.items()
            if alias.owner.mention.reporter and alias.owner.mention.reporter.key == reporter.key
        }
        if compatible:
            remaining = compatible
            used_evidence = True

    printed_kind = infer_document_kind(context)
    printed_phase = infer_procedural_phase(context)
    if printed_kind != "unknown" or printed_phase != "unknown":
        compatible = {}
        for identity, alias in remaining.items():
            mention = alias.owner.mention
            kind_ok = (
                printed_kind == "unknown"
                or mention.document_kind == "unknown"
                or mention.document_kind == printed_kind
            )
            phase_ok = (
                printed_phase == "unknown"
                or mention.procedural_phase == "unknown"
                or mention.procedural_phase == printed_phase
            )
            if kind_ok and phase_ok:
                compatible[identity] = alias
        if compatible and len(compatible) < len(remaining):
            remaining = compatible
            used_evidence = True

    if used_evidence and len(remaining) == 1:
        selected = next(iter(remaining.values()))
        return _Alias(
            text=selected.text,
            key=selected.key,
            owner=selected.owner,
            finder=f"printed_evidence_{selected.finder}",
            strong=True,
        )
    return None


def _antecedent_alias(
    entries: list[_Alias],
    established_positions: dict[str, int],
    context: str,
    *,
    italic: bool,
) -> _Alias | None:
    """Resolve a shared short form from a prior, uniquely established owner."""
    aliases = _one_alias_per_identity(entries)
    prior = {
        identity: (established_positions[identity], alias)
        for identity, alias in aliases.items()
        if identity in established_positions
    }
    if not prior or not (italic or _CUE_RE.search(context)):
        return None

    printed_phase = infer_procedural_phase(context)
    if printed_phase != "unknown":
        phase_matches = {
            identity: value
            for identity, value in prior.items()
            if value[1].owner.mention.procedural_phase in {"unknown", printed_phase}
        }
        if phase_matches:
            prior = phase_matches
    if len(prior) == 1:
        return next(iter(prior.values()))[1]

    if not re.search(r"cited\s+above|pr[ée]cit[ée]e?", context, re.I):
        return None
    ordered = sorted(prior.values(), key=lambda value: value[0], reverse=True)
    if len(ordered) > 1 and ordered[0][0] == ordered[1][0]:
        return None
    return ordered[0][1]


def _style_at(block: DocumentBlock, start: int, end: int) -> tuple[bool, bool]:
    runs = [run for run in block.inline_runs if run.start < end and run.end > start]
    return any(run.italic for run in runs), any(run.bold for run in runs)


def _component(block: DocumentBlock) -> Literal["majority", "opinion", "appendix"]:
    if block.opinion_id or block.section == "separate_opinion":
        return "opinion"
    if block.section == "appendix":
        return "appendix"
    return "majority"


def _locus_id(case: Case, block: DocumentBlock, start: int, end: int) -> str:
    """Identify a printed source locus without using target-resolution state."""
    source = case.itemid or case.ecli or ";".join(case.appno) or "unknown"
    raw = normalize_reference(block.text[start:end])
    return hashlib.sha256(f"{source}|{block.block_id}|{start}|{end}|{raw}".encode()).hexdigest()


def _group_fields(mention: CitationMention, _locus_id: str) -> tuple[str | None, int, int]:
    evidence = mention.discovery_evidence
    group_id = evidence.get("citation_group_id")
    ordinal = evidence.get("group_ordinal", 1)
    size = evidence.get("group_size", 1)
    return (
        str(group_id) if group_id else None,
        ordinal if isinstance(ordinal, int) and ordinal > 0 else 1,
        size if isinstance(size, int) and size > 0 else 1,
    )


def _attach_source_invocations(
    occurrences: list[CitationOccurrence], blocks: dict[str, DocumentBlock]
) -> None:
    for occurrence in occurrences:
        source = blocks.get(occurrence.source_block_id)
        if source is None:
            continue
        invocation_ids = (
            source.referenced_by_block_ids
            if source.footnote_id and source.referenced_by_block_ids
            else [source.block_id]
        )
        invocations: list[CitationSourceInvocation] = []
        for block_id in dict.fromkeys(invocation_ids):
            block = blocks.get(block_id)
            if block is None:
                continue
            invocations.append(
                CitationSourceInvocation(
                    source_block_id=block.block_id,
                    source_para_id=block.legal_para_id or block.para_id,
                    source_para_num=(
                        block.legal_para_num if block.legal_para_id is not None else block.para_num
                    ),
                    source_section=block.section,
                    source_component=_component(block),
                    source_opinion_id=block.opinion_id,
                    source_opinion_ordinal=block.opinion_ordinal,
                    source_opinion_type=block.opinion_type,
                    source_opinion_authors=list(block.opinion_authors),
                    source_opinion_joined_by=list(block.opinion_joined_by),
                )
            )
        occurrence.source_invocations = invocations
        if not source.footnote_id or not invocations:
            continue
        contexts = {
            (
                value.source_component,
                value.source_opinion_id,
                value.source_opinion_ordinal,
                value.source_opinion_type,
                tuple(value.source_opinion_authors),
                tuple(value.source_opinion_joined_by),
            )
            for value in invocations
        }
        if len(contexts) != 1:
            occurrence.source_component = "appendix"
            continue
        selected = invocations[0]
        occurrence.source_component = selected.source_component
        occurrence.source_opinion_id = selected.source_opinion_id
        occurrence.source_opinion_ordinal = selected.source_opinion_ordinal
        occurrence.source_opinion_type = selected.source_opinion_type
        occurrence.source_opinion_authors = list(selected.source_opinion_authors)
        occurrence.source_opinion_joined_by = list(selected.source_opinion_joined_by)


def _carry_forward_occurrences(
    case: Case,
    spine: DocumentSpine,
    occurrences: list[CitationOccurrence],
    owners: list[_Owner],
    diagnostics: list[dict[str, object]],
) -> None:
    """Add tightly gated ``ibid.``/``loc. cit.`` loci after anchor detection."""
    owner_by_mention = {owner.mention.mention_id: owner for owner in owners}
    blocks = list(spine.blocks)
    block_index = {block.block_id: index for index, block in enumerate(blocks)}

    def owner_for(value: CitationOccurrence) -> _Owner | None:
        owner = owner_by_mention.get(value.mention_id)
        if owner is not None:
            return owner
        for mention_id in value.scl_mention_ids:
            if mention_id in owner_by_mention:
                return owner_by_mention[mention_id]
        return None

    for block in blocks:
        for match in _CARRY_FORWARD_RE.finditer(block.text):
            if any(
                value.source_block_id == block.block_id
                and match.start() < value.block_end
                and match.end() > value.block_start
                for value in occurrences
            ):
                continue
            prefix = block.text[: match.start()]
            quiet_prefix = not (
                APPNO_REGEX.search(prefix)
                or ECLI_REGEX.search(prefix)
                or parse_reporter(prefix) is not None
                or _case_name_matches(prefix)
                or _CARRY_FORWARD_RE.search(prefix)
                or any(
                    value.source_block_id == block.block_id and value.block_end <= match.start()
                    for value in occurrences
                )
            )
            prior_values = [
                value
                for value in occurrences
                if value.source_block_id == block.block_id
                and value.block_end <= match.start()
                and (value.finder == "carry_forward" or bool(value.evidence.get("strong")))
            ]
            if prior_values:
                nearest_end = max(value.block_end for value in prior_values)
                prior_values = [value for value in prior_values if value.block_end == nearest_end]
                gap = block.text[nearest_end : match.start()]
                next_occurrence_start = min(
                    (
                        value.block_start
                        for value in occurrences
                        if value.source_block_id == block.block_id
                        and value.block_start > nearest_end
                    ),
                    default=len(block.text),
                )
                owned_envelope = any(
                    _evidence_offset(value.evidence.get("pinpoint_boundary"), -1) >= match.end()
                    for value in prior_values
                ) or bool(re.match(r"\s*[,;(]", gap) and match.end() <= next_occurrence_start)
                if (
                    APPNO_REGEX.search(gap)
                    or ECLI_REGEX.search(gap)
                    or (parse_reporter(gap) is not None and not owned_envelope)
                    or _case_name_matches(gap)
                    or _CARRY_FORWARD_RE.search(gap)
                ):
                    prior_values = []
            if not prior_values and (
                re.fullmatch(r"\s*(?:\[\d+\]\s*)?", block.text[: match.start()]) or quiet_prefix
            ):
                index = block_index[block.block_id]
                previous = blocks[index - 1] if index else None
                if (
                    previous is not None
                    and previous.section == block.section
                    and previous.opinion_id == block.opinion_id
                    and (
                        previous.footnote_id == block.footnote_id
                        or (previous.footnote_id is not None and block.footnote_id is not None)
                    )
                ):
                    previous_values = [
                        value
                        for value in occurrences
                        if value.source_block_id == previous.block_id
                        and (
                            value.finder == "carry_forward"
                            or bool(value.evidence.get("strong"))
                            or bool(value.evidence.get("citation_cue_present"))
                        )
                    ]
                    previous_identities = {
                        owner.identity
                        for value in previous_values
                        if (owner := owner_for(value)) is not None
                    }
                    if len(previous_identities) == 1 and previous_values:
                        nearest_end = max(value.block_end for value in previous_values)
                        prior_values = [
                            value for value in previous_values if value.block_end == nearest_end
                        ]
            identities = {
                owner.identity for value in prior_values if (owner := owner_for(value)) is not None
            }
            if len(identities) != 1:
                diagnostics.append(
                    {
                        "code": "ambiguous_carry_forward",
                        "source_itemid": case.itemid,
                        "block_id": block.block_id,
                        "block_start": match.start(),
                        "block_end": match.end(),
                        "raw_text": match.group(),
                        "candidate_occurrence_ids": [value.occurrence_id for value in prior_values],
                    }
                )
                continue
            selected_value = min(prior_values, key=lambda value: value.group_ordinal)
            selected_owner = owner_for(selected_value)
            if selected_owner is None:
                continue
            alias = _Alias(
                text=match.group(),
                key=_key(match.group()),
                owner=selected_owner,
                finder="carry_forward",
                strong=False,
            )
            italic, bold = _style_at(block, match.start(), match.end())
            value = _occurrence(
                case,
                block,
                alias,
                match.start(),
                match.end(),
                italic=italic,
                bold=bold,
            )
            value.scl_coverage = selected_value.scl_coverage
            value.scl_mention_ids = list(selected_value.scl_mention_ids)
            value.discovery_methods = ["carry_forward"]
            value.evidence["antecedent_occurrence_id"] = selected_value.occurrence_id
            occurrences.append(value)


def _merge_classified_commission_report_overlaps(
    occurrences: list[CitationOccurrence],
) -> None:
    """Fold a classified report alias into a longer compatible SCL envelope."""
    removable: set[str] = set()
    for classified in occurrences:
        discovery_evidence = classified.evidence.get("discovery_evidence")
        if not isinstance(discovery_evidence, dict) or (
            discovery_evidence.get("namespace") != "echr_commission"
            or discovery_evidence.get("resolution_policy") != "classified_unresolved"
        ):
            continue
        if classified.target_node_id or classified.resolution_scope != "unresolved":
            continue
        if not re.search(
            r"(?:report\s+of\s+the\s+Commission|rapport\s+de\s+la\s+Commission)",
            classified.raw_text,
            re.I,
        ):
            continue
        classified_date = parse_reference_date(classified.raw_text)
        if classified_date is None:
            continue
        compatible = [
            value
            for value in occurrences
            if value.occurrence_id != classified.occurrence_id
            and value.source_block_id == classified.source_block_id
            and value.scl_coverage == "covered"
            and not value.target_node_id
            and value.resolution_scope == "unresolved"
            and value.block_start <= classified.block_start
            and value.block_end >= classified.block_end
            and parse_reference_date(value.raw_text) == classified_date
            and re.search(
                r"(?:report\s+of\s+the\s+Commission|rapport\s+de\s+la\s+Commission)",
                value.raw_text,
                re.I,
            )
        ]
        if len(compatible) != 1:
            continue
        keeper = compatible[0]
        keeper.discovery_methods = sorted(
            set([*keeper.discovery_methods, *classified.discovery_methods])
        )
        keeper.evidence["classified_commission_provenance"] = discovery_evidence
        prior_mention_ids = keeper.evidence.get("merged_mention_ids")
        if not isinstance(prior_mention_ids, list):
            prior_mention_ids = []
        keeper.evidence["merged_mention_ids"] = sorted(
            {
                keeper.mention_id,
                classified.mention_id,
                *(str(value) for value in prior_mention_ids),
            }
        )
        removable.add(classified.occurrence_id)
    if removable:
        occurrences[:] = [value for value in occurrences if value.occurrence_id not in removable]


def _merge_compatible_duplicate_loci(
    occurrences: list[CitationOccurrence], diagnostics: list[dict[str, object]]
) -> None:
    """Collapse compatible duplicate rows for one physical printed locus.

    Target-specific rows may share a locus only when an explicit compound
    citation group owns them. If at most one document identity is present,
    retaining duplicate rows would turn resolution metadata into a false
    occurrence count.
    """

    grouped: dict[str, list[CitationOccurrence]] = defaultdict(list)
    for value in occurrences:
        grouped[value.locus_id or value.occurrence_id].append(value)
    removable: set[str] = set()
    for locus_id, values in grouped.items():
        if len(values) < 2 or any(value.citation_group_id for value in values):
            continue
        target_ids = {
            value.target_node_id
            for value in values
            if value.resolution_scope == "document" and value.target_node_id
        }
        if len(target_ids) > 1:
            continue
        if len({_key(value.raw_text) for value in values}) != 1:
            continue
        explicit_appno_identities = {
            str(identity).split(":", 2)[1]
            for value in values
            if (identity := value.evidence.get("target_identity"))
            and str(identity).startswith("appno:")
        }
        if len(explicit_appno_identities) > 1:
            continue
        keeper = max(
            values,
            key=lambda value: (
                value.resolution_scope == "document",
                value.resolution_scope == "application",
                len(value.target_paragraphs),
                value.scl_coverage == "covered",
                len(value.discovery_methods),
                -value.group_ordinal,
                value.occurrence_id,
            ),
        )
        removed_here: list[str] = []
        for duplicate in values:
            if duplicate is keeper:
                continue
            keeper.target_paragraphs = list(
                dict.fromkeys([*keeper.target_paragraphs, *duplicate.target_paragraphs])
            )
            keeper.discovery_methods = sorted(
                {*keeper.discovery_methods, *duplicate.discovery_methods}
            )
            keeper.scl_mention_ids = sorted({*keeper.scl_mention_ids, *duplicate.scl_mention_ids})
            if duplicate.scl_coverage == "covered":
                keeper.scl_coverage = "covered"
            merged = keeper.evidence.get("merged_mention_ids")
            merged_ids = [str(value) for value in merged] if isinstance(merged, list) else []
            keeper.evidence["merged_mention_ids"] = sorted(
                {keeper.mention_id, duplicate.mention_id, *merged_ids}
            )
            removable.add(duplicate.occurrence_id)
            removed_here.append(duplicate.occurrence_id)
        diagnostics.append(
            {
                "code": "duplicate_unresolved_locus_merged",
                "source_itemid": keeper.source_itemid,
                "block_id": keeper.source_block_id,
                "locus_id": locus_id,
                "kept_occurrence_id": keeper.occurrence_id,
                "removed_occurrence_ids": sorted(removed_here),
            }
        )
    if removable:
        occurrences[:] = [value for value in occurrences if value.occurrence_id not in removable]


def _evidence_offset(value: object, default: int = 0) -> int:
    return value if isinstance(value, int) else default


def _balanced_identifier_boundary(
    text: str, citation_start: int, identifier_end: int, tail: str
) -> int | None:
    """Return a closing-delimiter boundary after an identifier when appropriate."""
    prefix = text[citation_start:identifier_end]
    depth = prefix.count("(") - prefix.count(")")
    if (
        citation_start > 0
        and text[citation_start - 1] == "("
        or re.search(
            r"\(\s*(?:see(?:\s+also)?|voir(?:\s+aussi)?|cf\.?)\s*$",
            text[max(0, citation_start - 30) : citation_start],
            re.I,
        )
    ):
        depth += 1
    if depth <= 0:
        return None
    for index, char in enumerate(tail):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                after = tail[index + 1 :]
                if re.match(
                    r"\s*,?\s*(?:§{1,2}|paras?\.?|paragraphs?)\b",
                    after,
                    flags=re.I,
                ):
                    return None
                return index + 1
    return None


def _coverage_matches(
    discovered: CitationMention,
    scl_mentions: list[CitationMention],
) -> list[CitationMention]:
    appnos = set(discovered.explicit_appnos)
    name_key = _key(discovered.cited_name or "")
    matches = []
    for mention in scl_mentions:
        discovered_kind: str = discovered.document_kind
        discovered_phase = discovered.procedural_phase
        discovered_date = discovered.target_date
        scl_kind: str = mention.document_kind
        scl_phase = mention.procedural_phase
        scl_date = mention.target_date
        if discovered_kind != "unknown" and scl_kind != "unknown" and discovered_kind != scl_kind:
            continue
        if (
            discovered_phase != "unknown"
            and scl_phase != "unknown"
            and discovered_phase != scl_phase
        ):
            continue
        if discovered_date and scl_date and discovered_date != scl_date:
            continue
        scl_appnos = set(mention.explicit_appnos)
        if appnos and scl_appnos and not appnos.intersection(scl_appnos):
            # A shared title cannot override conflicting printed application
            # numbers.  This distinction is essential for later applications
            # between the same parties and for numbered procedural cases.
            continue
        if appnos and appnos.intersection(mention.explicit_appnos):
            matches.append(mention)
            continue
        other = _key(mention.cited_name or "")
        if (
            name_key
            and other
            and _numbered_case_titles_compatible(
                discovered.cited_name or "", mention.cited_name or ""
            )
            and (name_key == other or name_key in other or other in name_key)
        ):
            matches.append(mention)
    return matches


def _numbered_case_titles_compatible(left: str, right: str) -> bool:
    """Keep numbered inter-State cases distinct during name-only matching."""

    def markers(value: str) -> set[str]:
        return {
            match.group(1).upper() for match in re.finditer(r"\(([IVXLCDM]+)\)", value, flags=re.I)
        }

    left_markers = markers(left)
    right_markers = markers(right)
    return not (left_markers or right_markers) or left_markers == right_markers


def _discovery_spine(case: Case, *, html: str | None, spine: DocumentSpine | None) -> DocumentSpine:
    if spine is not None:
        return spine
    if case.sections and case.sections.spine:
        return case.sections.spine
    source = html if html is not None else case.text or ""
    if html is not None or source.lstrip().startswith("<"):
        from ..text.segmentation import segment_html

        sections = segment_html(
            source,
            doctype=case.doctype,
            doctype_branch=case.doctype_branch,
            document_id=case.itemid,
        )
        return sections.spine or build_spine_from_html(source, document_id=case.itemid)
    from ..text.segmentation import segment_full

    sections = segment_full(
        source,
        doctype=case.doctype,
        doctype_branch=case.doctype_branch,
        document_id=case.itemid,
    )
    return sections.spine or build_spine_from_text(source, document_id=case.itemid)


def _classified_commission_mention(
    case: Case,
    block: DocumentBlock,
    *,
    start: int,
    end: int,
    ordinal: int,
    method: str,
    procedural_phase: Literal["commission_report", "commission_decision"],
    appno: str | None = None,
    cited_name: str | None = None,
) -> CitationMention:
    raw = block.text[start:end]
    target_date = parse_reference_date(raw)
    reporter = parse_reporter(raw)
    digest = hashlib.sha256(
        f"{case.itemid}|{block.block_id}|{start}|{end}|{normalize_reference(raw).casefold()}".encode()
    ).hexdigest()
    return CitationMention(
        mention_id=digest,
        reference_hash=hashlib.sha256(normalize_reference(raw).casefold().encode()).hexdigest(),
        source_itemid=case.itemid,
        source_ecli=case.ecli,
        source_appnos=sorted(_case_appnos(case)),
        source_language=case.language,
        source_date=case.kp_date or case.judgement_date or case.decision_date,
        ordinal=ordinal,
        raw_ref=raw,
        normalized_ref=normalize_reference(raw).casefold(),
        cited_name=cited_name,
        respondent=extract_respondent(cited_name) if cited_name else None,
        explicit_appnos=[appno] if appno else [],
        target_date=target_date,
        target_year=target_date.year if target_date else None,
        target_month=target_date.month if target_date else None,
        target_day=target_date.day if target_date else None,
        document_kind="commission",
        procedural_phase=procedural_phase,
        reporter=reporter,
        origin="text_discovery",
        source_section=block.section,
        source_block_id=block.block_id,
        source_para_id=block.legal_para_id or block.para_id,
        source_opinion_id=block.opinion_id,
        source_footnote_id=block.footnote_id,
        source_invoking_block_ids=list(block.referenced_by_block_ids),
        source_invoking_para_ids=list(block.referenced_by_para_ids),
        discovery_evidence={
            "method": method,
            "namespace": "echr_commission",
            "resolution_policy": "classified_unresolved",
            "block_start": start,
            "block_end": end,
        },
    )


def discover_citation_mentions(
    case: Case,
    *,
    html: str | None = None,
    spine: DocumentSpine | None = None,
) -> CitationDiscoveryResult:
    """Discover strong, name-bearing ECtHR citation envelopes without SCL."""
    spine = _discovery_spine(case, html=html, spine=spine)
    found: list[tuple[int, CitationMention]] = []
    diagnostics: list[dict[str, object]] = []
    source_appnos = _case_appnos(case)
    ordinal = 0
    series_catalog: dict[str, list[HistoricalCatalogEntry]] | None = None
    for block in spine.blocks:
        if (
            block.type == "heading"
            and "\n" not in block.text
            and not APPNO_REGEX.search(block.text)
        ) or block.heading_role == "frontmatter":
            continue
        for match in _GROUPED_PHASE_RE.finditer(block.text):
            raw = match.group("raw")
            name = match.group("name") or match.group("name_rev")
            phases = (
                ("preliminary_objections", "merits")
                if match.group("name")
                else ("merits", "preliminary_objections")
            )
            paragraphs = (
                (match.group("first_para"), match.group("second_para"))
                if match.group("name")
                else (match.group("first_para_rev"), match.group("second_para_rev"))
            )
            group_id = hashlib.sha256(
                f"{case.itemid}|{block.block_id}|{match.start()}|{match.end()}|group".encode()
            ).hexdigest()
            for group_ordinal, (phase, paragraph) in enumerate(
                zip(phases, paragraphs, strict=True), 1
            ):
                grouped_mention = parse_scl_mentions(
                    case.model_copy(
                        update={
                            "scl": f"{name} judgment ({phase.replace('_', ' ')}), § {paragraph}",
                            "sclappnos": [],
                        }
                    )
                )[0]
                mention_id = hashlib.sha256(
                    f"{group_id}|{group_ordinal}|{phase}".encode()
                ).hexdigest()
                mention = grouped_mention.model_copy(
                    update={
                        "mention_id": mention_id,
                        "reference_hash": hashlib.sha256(
                            f"{normalize_reference(raw).casefold()}|{phase}".encode()
                        ).hexdigest(),
                        "ordinal": ordinal,
                        "raw_ref": raw,
                        "normalized_ref": normalize_reference(raw).casefold(),
                        "origin": "text_discovery",
                        "cited_name": name,
                        "respondent": None,
                        "document_kind": "judgment",
                        "procedural_phase": phase,
                        "target_paragraphs": [paragraph],
                        "source_section": block.section,
                        "source_block_id": block.block_id,
                        "source_para_id": block.legal_para_id or block.para_id,
                        "source_opinion_id": block.opinion_id,
                        "source_footnote_id": block.footnote_id,
                        "source_invoking_block_ids": list(block.referenced_by_block_ids),
                        "source_invoking_para_ids": list(block.referenced_by_para_ids),
                        "discovery_evidence": {
                            "method": "grouped_procedural_documents",
                            "block_start": match.start(),
                            "block_end": match.end(),
                            "citation_group_id": group_id,
                            "group_ordinal": group_ordinal,
                            "group_size": 2,
                            "owned_pinpoint": paragraph,
                        },
                    }
                )
                found.append((block.char_start + match.start(), mention))
                ordinal += 1
        claimed_app_spans: list[tuple[int, int]] = []
        commission_spans: list[tuple[int, int]] = []
        commission_patterns: tuple[
            tuple[
                str,
                re.Pattern[str],
                Literal["commission_report", "commission_decision"],
            ],
            ...,
        ] = (
            (
                "commission_report_reference",
                _COMMISSION_REPORT_REFERENCE_RE,
                "commission_report",
            ),
            (
                "commission_decision_reference",
                _COMMISSION_DECISION_REFERENCE_RE,
                "commission_decision",
            ),
            (
                "commission_dr_reference",
                _COMMISSION_DR_REFERENCE_RE,
                "commission_decision",
            ),
        )
        for method, pattern, phase in commission_patterns:
            for commission_match in pattern.finditer(block.text):
                start, end = commission_match.span("raw")
                if any(
                    start < prior_end and end > prior_start
                    for prior_start, prior_end in commission_spans
                ):
                    continue
                appno = commission_match.groupdict().get("appno")
                if appno and appno in source_appnos:
                    diagnostics.append(
                        {
                            "code": "self_reference",
                            "source_itemid": case.itemid,
                            "block_id": block.block_id,
                            "raw_text": commission_match.group("raw"),
                        }
                    )
                    continue
                if method == "commission_decision_reference":
                    namespace_context = block.text[max(0, start - 90) : end]
                    if (
                        not re.search(r"\bCommission\b", namespace_context, re.I)
                        and parse_reporter(commission_match.group("raw")) is None
                    ):
                        continue
                cited_name = None
                if method == "commission_report_reference":
                    prior_names = [
                        value
                        for value in _case_name_matches(block.text[:start])
                        if _respondent_is_convention_state(extract_respondent(value.group("name")))
                    ]
                    if prior_names:
                        cited_name = prior_names[-1].group("name")
                        cited_name = cited_name[cited_name.rfind("\n") + 1 :]
                        cited_name = re.sub(
                            r"^(?:(?:See|Voir|Cf\.?)\s+)+",
                            "",
                            cited_name,
                            flags=re.I,
                        ).strip()
                mention = _classified_commission_mention(
                    case,
                    block,
                    start=start,
                    end=end,
                    ordinal=ordinal,
                    method=method,
                    procedural_phase=phase,
                    appno=appno,
                    cited_name=cited_name,
                )
                found.append((block.char_start + start, mention))
                commission_spans.append((start, end))
                if appno:
                    claimed_app_spans.append((start, end))
                ordinal += 1
        for app_match in APPNO_REGEX.finditer(block.text):
            if any(start <= app_match.start() < end for start, end in claimed_app_spans):
                continue
            appno = app_match.group(1)
            same_source_appno = appno in source_appnos
            prefix_start = max(0, app_match.start() - 260)
            prefix = block.text[prefix_start : app_match.start()]
            namespace_prefix = block.text[max(0, app_match.start() - 360) : app_match.start()]
            if _EXTERNAL_HUMAN_RIGHTS_COMMITTEE_RE.search(namespace_prefix):
                diagnostics.append(
                    {
                        "code": "external_identifier",
                        "namespace": "un_human_rights_committee",
                        "source_itemid": case.itemid,
                        "block_id": block.block_id,
                        "raw_text": appno,
                    }
                )
                continue
            if _EXTERNAL_NUMBER_RE.search(prefix):
                diagnostics.append(
                    {
                        "code": "external_identifier",
                        "source_itemid": case.itemid,
                        "block_id": block.block_id,
                        "raw_text": appno,
                    }
                )
                continue
            names = _case_name_matches(prefix)
            authority_name = _authority_name_before_appno(prefix, appno)
            authority_applicant = _authority_applicant_before_appno(
                prefix, appno, language=case.language
            )
            fallback_name = _fallback_name_before_appno(prefix)
            name_match = (
                max(names, key=lambda value: (value.end(), -value.start())) if names else None
            )
            selected_name: tuple[int, int, str] | None = authority_name
            canonical_name: str | None = None
            selected_method = "authority_printed_title" if authority_name else "strict_name_grammar"
            if selected_name is None and authority_applicant is not None:
                selected_name = authority_applicant[:3]
                canonical_name = authority_applicant[3]
                selected_method = "authority_printed_applicant"
            if (
                selected_name is None
                and fallback_name is not None
                and (
                    name_match is None
                    or (
                        fallback_name[0] <= name_match.start()
                        and fallback_name[1] >= name_match.end()
                        and not _fallback_crosses_prior_citation(prefix, fallback_name, name_match)
                    )
                )
            ):
                selected_name = fallback_name
                selected_method = "application_number_connector_fallback"
            if selected_name is None and name_match is not None:
                selected_name = (
                    name_match.start(),
                    name_match.end(),
                    name_match.group("name"),
                )
            if selected_name is None:
                diagnostics.append(
                    {
                        "code": (
                            "self_reference"
                            if same_source_appno
                            else "unanchored_application_number"
                        ),
                        "source_itemid": case.itemid,
                        "block_id": block.block_id,
                        "raw_text": appno,
                    }
                )
                continue
            selected_start, selected_end, selected_text = selected_name
            if app_match.start() - (prefix_start + selected_end) > 100:
                diagnostics.append(
                    {
                        "code": "unanchored_application_number",
                        "source_itemid": case.itemid,
                        "block_id": block.block_id,
                        "raw_text": appno,
                    }
                )
                continue
            printed_name = selected_text
            # Do not let a preceding structural heading become part of the
            # authority merely because the HTML spine joined it with a
            # newline (for example ``THE LAW\nA v. France``).
            line_start = printed_name.rfind("\n") + 1
            paragraph_prefix = re.match(r"\s*\d+[A-Za-z]?\.\s+", printed_name[line_start:])
            if paragraph_prefix is not None:
                line_start += paragraph_prefix.end()
            leading = re.match(
                r"(?:(?:See|Compare|Contrast|And)\s+)+",
                printed_name[line_start:],
            )
            leading_chars = line_start + (leading.end() if leading else 0)
            noise = _LEADING_CASE_NOISE_RE.match(printed_name[leading_chars:])
            if noise:
                leading_chars += noise.end()
            reverse_match = (
                next(
                    (
                        value
                        for value in reversed(list(_REVERSE_DATE_NAME_RE.finditer(prefix)))
                        if name_match is not None and value.end("name") == name_match.end()
                    ),
                    None,
                )
                if selected_method == "strict_name_grammar"
                else None
            )
            if reverse_match is not None:
                start = prefix_start + reverse_match.start()
                cited_name = reverse_match.group("name").strip(" ,.")
            else:
                start = prefix_start + selected_start + leading_chars
                cited_name = (
                    canonical_name
                    or re.sub(
                        r"\s*[\[(]+\s*(?=(?:v\.?|c\.?|contre|against)\b)",
                        " ",
                        printed_name[leading_chars:],
                        flags=re.I,
                    ).strip()
                )
            tail_limit = min(len(block.text), app_match.end() + 180)
            tail = block.text[app_match.end() : tail_limit]
            boundary = re.search(
                r"[;\n]|(?<=[.!?)])\s+(?=[A-Z])",
                tail,
            )
            boundary_offset = boundary.start() if boundary else len(tail)
            balanced = _balanced_identifier_boundary(block.text, start, app_match.end(), tail)
            if balanced is not None:
                boundary_offset = min(boundary_offset, balanced)
            # A following authority owns its own envelope and any pinpoint
            # after it.  This is essential for ``A ... and B ..., § 42``.
            following_names = _case_name_matches(tail)
            following_boundary_used = False
            if following_names:
                following_start = following_names[0].start()
                if following_start <= boundary_offset:
                    boundary_offset = following_start
                    following_boundary_used = True
            end = app_match.end() + boundary_offset
            if following_boundary_used:
                separator = re.search(
                    r"(?:,\s*)?(?:and|et)\s*$",
                    block.text[start:end],
                    re.I,
                )
                if separator is not None:
                    end = start + separator.start()
            while end > start and block.text[end - 1].isspace():
                end -= 1
            raw = block.text[start:end].strip(" ,")
            parsed_case = case.model_copy(update={"scl": raw, "sclappnos": []})
            parsed = parse_scl_mentions(parsed_case)
            if not parsed:
                continue
            mention = parsed[0]
            if same_source_appno:
                source_date = case.kp_date or case.judgement_date or case.decision_date
                cue_context = block.text[max(0, start - 100) : end]
                if (
                    source_date is None
                    or mention.target_date is None
                    or mention.target_date == source_date
                    or _PRIOR_DOCUMENT_CUE_RE.search(cue_context) is None
                ):
                    diagnostics.append(
                        {
                            "code": "self_reference",
                            "source_itemid": case.itemid,
                            "block_id": block.block_id,
                            "raw_text": raw,
                        }
                    )
                    continue
            digest = hashlib.sha256(
                f"{case.itemid}|{block.block_id}|{start}|{end}|{normalize_reference(raw).casefold()}".encode()
            ).hexdigest()
            mention = mention.model_copy(
                update={
                    "mention_id": digest,
                    "reference_hash": hashlib.sha256(
                        normalize_reference(raw).casefold().encode()
                    ).hexdigest(),
                    "ordinal": ordinal,
                    "origin": "text_discovery",
                    "cited_name": cited_name,
                    "respondent": extract_respondent(cited_name),
                    "source_section": block.section,
                    "source_block_id": block.block_id,
                    "source_para_id": block.legal_para_id or block.para_id,
                    "source_opinion_id": block.opinion_id,
                    "source_footnote_id": block.footnote_id,
                    "source_invoking_block_ids": list(block.referenced_by_block_ids),
                    "source_invoking_para_ids": list(block.referenced_by_para_ids),
                    "discovery_evidence": {
                        "method": (
                            "same_application_prior_document"
                            if same_source_appno
                            else "name_application_number"
                        ),
                        "name_selection": selected_method,
                        "block_start": start,
                        "block_end": end,
                        "application_start": app_match.start(),
                        "application_end": app_match.end(),
                    },
                }
            )
            found.append((block.char_start + start, mention))
            claimed_app_spans.append((start, end))
            ordinal += 1
        occupied_name_spans = [
            (
                _evidence_offset(value.discovery_evidence.get("block_start")),
                _evidence_offset(value.discovery_evidence.get("block_end")),
            )
            for _, value in found
            if value.source_block_id == block.block_id
        ]
        for name_match in _case_name_matches(block.text):
            start, end = name_match.span("name")
            roman_suffix = re.match(r"\s*\([IVXLC]+\)", block.text[end:])
            if roman_suffix is not None:
                end += roman_suffix.end()
            phase_suffix = re.match(
                rf"\s*,?\s*\(\s*{_PRINTED_PHASE}\s*\)",
                block.text[end:],
                re.I,
            )
            if phase_suffix is not None:
                after_phase = block.text[
                    end + phase_suffix.end() : min(len(block.text), end + phase_suffix.end() + 80)
                ]
                if re.match(
                    r"\s*,?\s*(?:(?:cited\s+above|pr[ée]cit[ée]e?)\b|"
                    r"(?:§§?|paras?\.)\s*\d)",
                    after_phase,
                    re.I,
                ):
                    end += phase_suffix.end()
            if any(
                start < old_end and end > old_start for old_start, old_end in occupied_name_spans
            ):
                continue
            raw_with_suffix = block.text[name_match.start("name") : end]
            connector_in_raw = _PARTY_RE.search(raw_with_suffix)
            if connector_in_raw is not None:
                prefix = raw_with_suffix[: connector_in_raw.start()]
                trim_start = prefix.rfind("\n") + 1
                intros = list(_CITATION_INTRO_RE.finditer(prefix))
                if intros:
                    trim_start = max(trim_start, intros[-1].end())
                if trim_start:
                    raw_with_suffix = raw_with_suffix[trim_start:]
                    start += trim_start
            raw = raw_with_suffix.strip(" ,")
            start += len(raw_with_suffix) - len(raw_with_suffix.lstrip(" ,"))
            end = start + len(raw)
            respondent = extract_respondent(_strip_phase(raw))
            if not _respondent_is_convention_state(respondent):
                continue
            italic, _ = _style_at(block, start, end)
            nearby = _context(block.text, start, end, radius=70)
            owned_tail = block.text[end : min(len(block.text), end + 140)]
            tail_boundary = re.search(r"[;\n]|(?<=[.!?)])\s+(?=[A-Z])", owned_tail)
            if tail_boundary is not None:
                owned_tail = owned_tail[: tail_boundary.start()]
            following_names = _case_name_matches(owned_tail)
            if following_names:
                owned_tail = owned_tail[: following_names[0].start()]
            if (
                _DATE_TEXT_RE.search(owned_tail)
                or parse_reporter(owned_tail) is not None
                or any(
                    reverse.start("name") <= start and reverse.end("name") >= end
                    for reverse in _REVERSE_DATE_NAME_RE.finditer(block.text)
                )
            ):
                # Rich historical grammars below own dated/reporter forms and
                # their larger citation envelopes.
                continue
            if not italic and _CUE_RE.search(nearby) is None:
                continue
            raw_key = _key(_strip_phase(raw))
            if any(
                _key(_strip_phase(prior.cited_name or "")) == raw_key
                for absolute, prior in found
                if absolute < block.char_start + start
            ):
                # A prior strong envelope already seeds this exact title; the
                # occurrence layer will locate the later form through its
                # document-local gazetteer without inventing a second owner.
                continue
            parsed = parse_scl_mentions(case.model_copy(update={"scl": raw, "sclappnos": []}))
            if not parsed or not parsed[0].cited_name:
                continue
            mention = parsed[0]
            if _same_source_party(case, _Owner(mention, None)) and not _prior_document_cue_at(
                block.text, start, end
            ):
                diagnostics.append(
                    {
                        "code": "self_reference",
                        "source_itemid": case.itemid,
                        "block_id": block.block_id,
                        "raw_text": raw,
                    }
                )
                continue
            mention_id = hashlib.sha256(
                f"{case.itemid}|{block.block_id}|{start}|{end}|{normalize_reference(raw).casefold()}".encode()
            ).hexdigest()
            mention = mention.model_copy(
                update={
                    "mention_id": mention_id,
                    "reference_hash": hashlib.sha256(
                        normalize_reference(raw).casefold().encode()
                    ).hexdigest(),
                    "ordinal": ordinal,
                    "origin": "text_discovery",
                    "cited_name": raw,
                    "respondent": respondent,
                    "source_section": block.section,
                    "source_block_id": block.block_id,
                    "source_para_id": block.legal_para_id or block.para_id,
                    "source_opinion_id": block.opinion_id,
                    "source_footnote_id": block.footnote_id,
                    "source_invoking_block_ids": list(block.referenced_by_block_ids),
                    "source_invoking_para_ids": list(block.referenced_by_para_ids),
                    "discovery_evidence": {
                        "method": "formatted_name_state_parties",
                        "block_start": start,
                        "block_end": end,
                        "italic": italic,
                    },
                }
            )
            found.append((block.char_start + start, mention))
            occupied_name_spans.append((start, end))
            ordinal += 1
        for historical_pattern in (
            _HISTORICAL_ENVELOPE_RE,
            _HISTORICAL_FALLBACK_ENVELOPE_RE,
        ):
            occupied = [
                (
                    _evidence_offset(value.discovery_evidence.get("block_start")),
                    _evidence_offset(value.discovery_evidence.get("block_end")),
                )
                for _, value in found
                if value.source_block_id == block.block_id
            ]
            for match in historical_pattern.finditer(block.text):
                raw = match.group("raw").strip(" ,")
                printed_names = _case_name_matches(raw)
                if not printed_names:
                    continue
                printed = max(
                    printed_names,
                    key=lambda value: len(value.group("name")),
                )
                printed_name = printed.group("name")
                actual_start = match.start() + printed.start()
                en_name = match.group("en_name")
                if en_name and "," in en_name:
                    expanded_names = _case_name_matches(en_name)
                    if expanded_names:
                        expanded = max(
                            expanded_names,
                            key=lambda value: len(value.group("name")),
                        )
                        printed_name = expanded.group("name")
                        actual_start = match.start("en_name") + expanded.start()
                leading = re.match(
                    r"(?:(?:See|Compare|Contrast|And|Voir)\s+)+"
                    r"(?:l['’])?(?:the\s+)?",
                    printed_name,
                    flags=re.I,
                )
                leading_chars = leading.end() if leading else 0
                noise = _LEADING_CASE_NOISE_RE.match(printed_name[leading_chars:])
                if noise is not None:
                    leading_chars += noise.end()
                actual_start += leading_chars
                if any(actual_start < end and match.end() > start for start, end in occupied):
                    continue
                raw = block.text[actual_start : match.end()].strip(" ,")
                parsed_case = case.model_copy(update={"scl": raw, "sclappnos": []})
                parsed = parse_scl_mentions(parsed_case)
                if not parsed or not parsed[0].cited_name:
                    continue
                mention = parsed[0]
                cited_name = printed_name[leading_chars:].strip()
                if match.group("fr_name"):
                    cited_name = re.sub(r"\s+(?:du|de)$", "", cited_name, flags=re.I)
                digest = hashlib.sha256(
                    f"{case.itemid}|{block.block_id}|{actual_start}|{match.end()}|"
                    f"{normalize_reference(raw).casefold()}".encode()
                ).hexdigest()
                mention = mention.model_copy(
                    update={
                        "mention_id": digest,
                        "reference_hash": hashlib.sha256(
                            normalize_reference(raw).casefold().encode()
                        ).hexdigest(),
                        "ordinal": ordinal,
                        "origin": "text_discovery",
                        "cited_name": cited_name,
                        "respondent": extract_respondent(cited_name),
                        "source_section": block.section,
                        "source_block_id": block.block_id,
                        "source_para_id": block.legal_para_id or block.para_id,
                        "source_opinion_id": block.opinion_id,
                        "source_footnote_id": block.footnote_id,
                        "source_invoking_block_ids": list(block.referenced_by_block_ids),
                        "source_invoking_para_ids": list(block.referenced_by_para_ids),
                        "discovery_evidence": {
                            "method": "historical_name_date",
                            "block_start": actual_start,
                            "block_end": match.end(),
                        },
                    }
                )
                found.append((block.char_start + actual_start, mention))
                ordinal += 1
        occupied = [
            (
                _evidence_offset(value.discovery_evidence.get("block_start")),
                _evidence_offset(value.discovery_evidence.get("block_end")),
            )
            for _, value in found
            if value.source_block_id == block.block_id
        ]
        for envelope_method, envelope_pattern, reverse_order in (
            ("historical_name_date_reporter", _NAME_DATE_REPORTER_RE, False),
            ("historical_name_reporter", _NAME_REPORTER_RE, False),
            ("historical_name_date_reporter", _NAME_DATE_SERIES_RE, False),
            ("historical_reverse_date_name", _REVERSE_DATE_NAME_RE, True),
        ):
            for match in envelope_pattern.finditer(block.text):
                matched_name = match.group("name")
                cited_name = matched_name.strip(" ,.")
                leading = re.match(
                    r"(?:(?:See|Compare|Contrast|And|Voir)\s+)+(?:l['’])?(?:the\s+)?",
                    cited_name,
                    flags=re.I,
                )
                leading_chars = leading.end() if leading else 0
                noise = _LEADING_CASE_NOISE_RE.match(cited_name[leading_chars:])
                if noise is not None:
                    leading_chars += noise.end()
                cited_name = cited_name[leading_chars:].strip()
                cited_name = re.sub(
                    r"\s*\[\s*(?=(?:v\.?|c\.?|contre|against)\b)",
                    " ",
                    cited_name,
                    flags=re.I,
                ).strip(" ,.")
                actual_start = (
                    match.start() if reverse_order else match.start("name") + leading_chars
                )
                if any(actual_start < end and match.end() > start for start, end in occupied):
                    continue
                raw = block.text[actual_start : match.end()].strip(" ,.")
                if not _unicode_case_name_valid(cited_name):
                    continue
                parsed = parse_scl_mentions(case.model_copy(update={"scl": raw, "sclappnos": []}))
                if not parsed:
                    continue
                digest = hashlib.sha256(
                    f"{case.itemid}|{block.block_id}|{actual_start}|{match.end()}|"
                    f"{normalize_reference(raw).casefold()}".encode()
                ).hexdigest()
                mention = parsed[0].model_copy(
                    update={
                        "mention_id": digest,
                        "reference_hash": hashlib.sha256(
                            normalize_reference(raw).casefold().encode()
                        ).hexdigest(),
                        "ordinal": ordinal,
                        "origin": "text_discovery",
                        "cited_name": cited_name,
                        "respondent": extract_respondent(cited_name),
                        "source_section": block.section,
                        "source_block_id": block.block_id,
                        "source_para_id": block.legal_para_id or block.para_id,
                        "source_opinion_id": block.opinion_id,
                        "source_footnote_id": block.footnote_id,
                        "source_invoking_block_ids": list(block.referenced_by_block_ids),
                        "source_invoking_para_ids": list(block.referenced_by_para_ids),
                        "discovery_evidence": {
                            "method": envelope_method,
                            "block_start": actual_start,
                            "block_end": match.end(),
                        },
                    }
                )
                found.append((block.char_start + actual_start, mention))
                occupied.append((actual_start, match.end()))
                ordinal += 1
        for method, pattern in (("exact_ecli", ECLI_REGEX), ("exact_itemid", ITEMID_REGEX)):
            for match in pattern.finditer(block.text):
                if any(match.start() < end and match.end() > start for start, end in occupied):
                    continue
                raw = match.group(1)
                if raw == case.itemid or raw == case.ecli:
                    diagnostics.append(
                        {
                            "code": "self_reference",
                            "source_itemid": case.itemid,
                            "block_id": block.block_id,
                            "raw_text": raw,
                        }
                    )
                    continue
                parsed = parse_scl_mentions(case.model_copy(update={"scl": raw, "sclappnos": []}))
                if not parsed:
                    continue
                digest = hashlib.sha256(
                    f"{case.itemid}|{block.block_id}|{match.start()}|{match.end()}|"
                    f"{raw.casefold()}".encode()
                ).hexdigest()
                mention = parsed[0].model_copy(
                    update={
                        "mention_id": digest,
                        "reference_hash": hashlib.sha256(raw.casefold().encode()).hexdigest(),
                        "ordinal": ordinal,
                        "origin": "text_discovery",
                        "source_section": block.section,
                        "source_block_id": block.block_id,
                        "source_para_id": block.legal_para_id or block.para_id,
                        "source_opinion_id": block.opinion_id,
                        "source_footnote_id": block.footnote_id,
                        "source_invoking_block_ids": list(block.referenced_by_block_ids),
                        "source_invoking_para_ids": list(block.referenced_by_para_ids),
                        "discovery_evidence": {
                            "method": method,
                            "block_start": match.start(),
                            "block_end": match.end(),
                        },
                    }
                )
                found.append((block.char_start + match.start(), mention))
                occupied.append((match.start(), match.end()))
                ordinal += 1
        for match in _SERIES_ONLY_RE.finditer(block.text):
            if any(match.start() < end and match.end() > start for start, end in occupied):
                continue
            reporter = parse_reporter(match.group())
            if reporter is None:
                continue
            citation_end = match.end()
            closing = re.match(r"\s*\){1,2}", block.text[citation_end:])
            if closing is not None:
                citation_end += closing.end()
            if series_catalog is None:
                from .catalog import load_historical_catalog

                series_catalog = defaultdict(list)
                for entry in load_historical_catalog().entries:
                    series_catalog[entry.reporter_key].append(entry)
            catalog_entries = series_catalog.get(reporter.key, [])
            identities = {
                getattr(entry, "target_ecli", None) or getattr(entry, "target_itemid", None)
                for entry in catalog_entries
            } - {None}
            authority_entries: tuple[CitationAuthorityEntry, ...] = ()
            discovery_method = "unique_series_a"
            if len(identities) == 1:
                compatible_catalog_entries = [
                    value
                    for value in catalog_entries
                    if (value.target_ecli or value.target_itemid) in identities
                ]
                language = (case.language or "").casefold()
                selected_entry: HistoricalCatalogEntry | CitationAuthorityEntry = sorted(
                    compatible_catalog_entries,
                    key=lambda value: (
                        not (
                            (
                                language == "fre"
                                and (value.title or "").casefold().startswith("affaire")
                            )
                            or (
                                language != "fre"
                                and not (value.title or "").casefold().startswith("affaire")
                            )
                        ),
                        value.title,
                    ),
                )[0]
                candidate_titles = sorted(
                    {value.title for value in compatible_catalog_entries if value.title}
                )
            else:
                authority_groups = _authority_series_groups().get(reporter.key, ())
                if len(authority_groups) == 1:
                    authority_entries = authority_groups[0]
                    language = (case.language or "").casefold()
                    selected_entry = next(
                        (
                            value
                            for value in authority_entries
                            if (value.language or "").casefold() == language
                        ),
                        authority_entries[0],
                    )
                    candidate_titles = sorted(
                        {value.title for value in authority_entries if value.title}
                    )
                    discovery_method = "unique_official_series_a"
                else:
                    diagnostics.append(
                        {
                            "code": "ambiguous_reporter",
                            "source_itemid": case.itemid,
                            "block_id": block.block_id,
                            "raw_text": match.group(),
                            "candidate_count": max(len(identities), len(authority_groups)),
                        }
                    )
                    continue
            if not candidate_titles:
                diagnostics.append(
                    {
                        "code": "ambiguous_reporter",
                        "source_itemid": case.itemid,
                        "block_id": block.block_id,
                        "raw_text": match.group(),
                        "candidate_count": 0,
                    }
                )
                continue
            citation_start = match.start()
            printed_cited_name: str | None = None
            search_start = max(0, match.start() - 240)
            prefix = block.text[search_start : match.start()]
            printed_title_matches: list[tuple[int, int, str]] = []
            for candidate_title in candidate_titles:
                connector = _PARTY_RE.search(candidate_title)
                if connector is not None:
                    applicant = candidate_title[: connector.start()].strip()
                    applicant = re.sub(r"^(?:CASE\s+OF|AFFAIRE)\s+", "", applicant, flags=re.I)
                else:
                    quoted = re.match(
                        r"^(?:CASE|AFFAIRE)\s+[\"'‘“«]?(.+?)[\"'’”»]?"
                        r"(?:\s*\((?:MERITS|FOND)\))?$",
                        candidate_title,
                        re.I,
                    )
                    applicant = quoted.group(1).strip() if quoted else ""
                if not applicant:
                    continue
                for title_match in _pattern(applicant).finditer(prefix):
                    date_context = prefix[max(0, title_match.start() - 100) :]
                    if _DATE_TEXT_RE.search(date_context) is None:
                        continue
                    printed_date = parse_reference_date(date_context)
                    target_date = getattr(selected_entry, "date", None)
                    if printed_date and target_date and printed_date != target_date:
                        continue
                    printed_title_matches.append(
                        (
                            search_start + title_match.start(),
                            search_start + title_match.end(),
                            block.text[
                                search_start + title_match.start() : search_start
                                + title_match.end()
                            ],
                        )
                    )
            if printed_title_matches:
                citation_start, _, printed_cited_name = max(
                    printed_title_matches, key=lambda value: (value[1], value[1] - value[0])
                )
                leading_start = max(0, citation_start - 120)
                leading_text = block.text[leading_start:citation_start]
                cue = re.search(
                    rf"(?:(?:the\s+)?(?:judgment|decision|arr[êe]t|d[ée]cision)\s+"
                    rf"(?:of|du)\s+{_DATE_TEXT_RE.pattern}\s+(?:in|dans)\s+)?"
                    r"(?:l['’]\s*)?(?:the\s+)?(?:arr[êe]t|d[ée]cision|affaire|"
                    r"case(?:\s+of)?)\s*[\"'‘“«]?\s*$",
                    leading_text,
                    re.I,
                )
                if cue is not None:
                    citation_start = leading_start + cue.start()
            raw = block.text[citation_start:citation_end]
            parsed = parse_scl_mentions(case.model_copy(update={"scl": raw, "sclappnos": []}))
            if not parsed:
                continue
            digest = hashlib.sha256(
                f"{case.itemid}|{block.block_id}|{citation_start}|{citation_end}|"
                f"{raw.casefold()}".encode()
            ).hexdigest()
            mention = parsed[0].model_copy(
                update={
                    "mention_id": digest,
                    "reference_hash": hashlib.sha256(raw.casefold().encode()).hexdigest(),
                    "ordinal": ordinal,
                    "origin": "text_discovery",
                    "cited_name": printed_cited_name,
                    "source_section": block.section,
                    "source_block_id": block.block_id,
                    "source_para_id": block.legal_para_id or block.para_id,
                    "source_opinion_id": block.opinion_id,
                    "source_footnote_id": block.footnote_id,
                    "source_invoking_block_ids": list(block.referenced_by_block_ids),
                    "source_invoking_para_ids": list(block.referenced_by_para_ids),
                    "discovery_evidence": {
                        "method": discovery_method,
                        "block_start": citation_start,
                        "block_end": citation_end,
                        "reporter_key": reporter.key,
                        "authority_candidate_titles": candidate_titles,
                        "catalog_target_ecli": getattr(selected_entry, "target_ecli", None),
                        "catalog_target_itemid": getattr(selected_entry, "target_itemid", None),
                        "catalog_appnos": list(getattr(selected_entry, "appnos", [])),
                    },
                }
            )
            found.append((block.char_start + citation_start, mention))
            occupied.append((citation_start, citation_end))
            ordinal += 1
    # The Court also uses explicit back-references whose full authority exists
    # only in its official citation list, not in the document's selective SCL
    # field. A globally unique official applicant alias plus an immediately
    # owned citation cue is strong enough to recover the printed locus, while
    # target identity remains application-scoped when procedural rows differ.
    local_short_keys = {
        _match_key(alias)
        for mention in [
            *parse_scl_mentions(case),
            *(mention for _, mention in found if mention.cited_name),
        ]
        for alias in _distinctive_short_forms(mention.cited_name or "")
    }
    # Global authority aliases number in the thousands.  Build the exact set
    # of contiguous local phrases once instead of re-normalising every alias
    # for every local authority (which made discovery effectively quadratic
    # on citation-dense judgments).
    local_shadow_keys: set[str] = set(local_short_keys)
    for local_key in local_short_keys:
        tokens = local_key.split()
        for start in range(len(tokens)):
            for end in range(start + 1, len(tokens) + 1):
                phrase = " ".join(tokens[start:end])
                if len(phrase) >= 5:
                    local_shadow_keys.add(phrase)
    source_applicant_key = _key(_PARTY_RE.split(_strip_phase(case.docname or ""), maxsplit=1)[0])
    authority_aliases_list = []
    for value in (*_authority_short_aliases(), *_historical_short_aliases()):
        alias_key = _match_key(value[0])
        if alias_key in local_shadow_keys:
            continue
        if any(set(entry.appnos).intersection(source_appnos) for entry in value[1]):
            continue
        source_alias_key = _key(value[0])
        if source_applicant_key and (
            source_alias_key == source_applicant_key
            or (len(source_alias_key) >= 6 and source_alias_key in source_applicant_key)
        ):
            continue
        authority_aliases_list.append(value)
    authority_aliases = tuple(authority_aliases_list)
    authority_aliases_by_anchor: dict[
        str,
        list[
            tuple[
                str,
                tuple[CitationAuthorityEntry, ...],
                re.Pattern[str],
                str | None,
            ]
        ],
    ] = defaultdict(list)
    for value in authority_aliases:
        if value[3] is not None:
            authority_aliases_by_anchor[value[3]].append(value)
    for block in spine.blocks:
        if (
            block.heading_role == "frontmatter"
            or str(block.para_id or "").startswith("pre-")
            or _all_caps(block.text)
        ):
            continue
        occupied = [
            (
                _evidence_offset(mention.discovery_evidence.get("block_start")),
                _evidence_offset(mention.discovery_evidence.get("block_end")),
            )
            for _, mention in found
            if mention.source_block_id == block.block_id
        ]
        block_tokens = set(_match_key(block.text).split())
        possible_aliases = sorted(
            (
                value
                for token in block_tokens
                for value in authority_aliases_by_anchor.get(token, [])
            ),
            key=lambda value: (-len(value[0]), _key(value[0])),
        )
        for authority_alias, authority_entries, pattern, _ in possible_aliases:
            for match in pattern.finditer(block.text):
                printed_alias = match.group().strip()
                first_letter = next(
                    (character for character in printed_alias if character.isalpha()), ""
                )
                if not first_letter or not first_letter.isupper():
                    continue
                if any(
                    match.start() < known_end and match.end() > known_start
                    for known_start, known_end in occupied
                ):
                    continue
                following = block.text[match.end() : min(len(block.text), match.end() + 100)]
                back_reference = re.match(
                    r"\s*(?:\(\s*(?:n(?:o\.?|°|º))\s*\d+\s*\)\s*)?"
                    r"(?:[\[(]\s*)?(?:\[(?:GC|Grand Chamber|Section)\])?\s*,?\s*"
                    rf"(?:\(\s*{_PRINTED_PHASE}\s*\)\s*,?\s*)?"
                    r"(?:(?:judgment|decision|arr[êe]t|d[ée]cision)\s+)?"
                    r"(?:cited\s+above|pr[ée]cit[ée]e?)\b",
                    following,
                    re.I,
                )
                document_cue = re.match(
                    r"\s+(?:judgment|decision)\b",
                    following,
                    re.I,
                )
                case_cue = (
                    re.match(r"\s+(?:case|affaire)\b", following, re.I)
                    if len(authority_alias.split()) >= 2
                    else None
                )
                grouped_pin = _PIN_RE.search(following)
                grouped_back_reference = (
                    grouped_pin
                    if grouped_pin is not None
                    and grouped_pin.start() <= 5
                    and re.search(
                        r"\b(?:(?:all|both)\s+cited\s+above|"
                        r"(?:tous|toutes|les\s+deux)\s+pr[ée]cit[ée]s?)\b",
                        block.text[
                            match.end() + grouped_pin.end() : min(
                                len(block.text), match.end() + 220
                            )
                        ],
                        re.I,
                    )
                    else None
                )
                authority_identities = {
                    _authority_short_identity(entry) for entry in authority_entries
                }
                italic = _isolated_italic_span(block, match.start(), match.end())
                continues_as_full_name = _PARTY_RE.match(following) is not None
                formatted_short = (
                    italic
                    and len(authority_identities) == 1
                    and not continues_as_full_name
                    and len(match.group().split()) >= 2
                )
                cue = back_reference or document_cue or case_cue or grouped_back_reference
                if cue is None and not formatted_short:
                    continue
                end = match.end() + (cue.end() if cue is not None else 0)
                if grouped_back_reference is None:
                    pin = _PIN_RE.search(block.text[end : min(len(block.text), end + 70)])
                    if pin is not None and pin.start() <= 5:
                        end += pin.end()
                    closing = re.match(r"\s*[])]", block.text[end:])
                    if closing is not None:
                        end += closing.end()
                raw = block.text[match.start() : end]
                method = (
                    "authority_unique_short_back_reference"
                    if back_reference is not None
                    else "authority_unique_grouped_back_reference"
                    if grouped_back_reference is not None
                    else "authority_unique_document_short"
                    if document_cue is not None or case_cue is not None
                    else "authority_unique_formatted_short"
                )
                authority_mention = _authority_short_mention(
                    case,
                    authority_entries,
                    raw=raw,
                    block=block,
                    start=match.start(),
                    end=end,
                    ordinal=ordinal,
                    method=method,
                    printed_name=match.group(),
                )
                if authority_mention is None:
                    continue
                found.append((block.char_start + match.start(), authority_mention))
                occupied.append((match.start(), end))
                ordinal += 1
        for group_cue in re.finditer(
            r"\b(?:(?:all|both)\s+cited\s+above|"
            r"(?:tous|toutes|les\s+deux)\s+pr[ée]cit[ée]s?)\b",
            block.text,
            re.I,
        ):
            window_start = max(0, group_cue.start() - 240)
            prefix = block.text[window_start : group_cue.start()]
            for pin in _PIN_RE.finditer(prefix):
                left = prefix[: pin.start()]
                boundaries = [left.rfind(";"), left.rfind("\n")]
                sentence_boundaries = list(re.finditer(r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Þ])", left))
                if sentence_boundaries:
                    boundaries.append(sentence_boundaries[-1].end() - 1)
                intros = list(_CITATION_INTRO_RE.finditer(left))
                if intros:
                    boundaries.append(intros[-1].end() - 1)
                conjunctions = list(
                    re.finditer(
                        r"\b(?:and|et)\s+(?!(?:Others|autres)\b)"
                        r"(?=[A-ZÀ-ÖØ-Þ])",
                        left,
                        re.I,
                    )
                )
                if conjunctions:
                    boundaries.append(conjunctions[-1].end() - 1)
                relative_start = max(boundaries) + 1
                candidate = left[relative_start:].strip(" ,;()")
                start = window_start + relative_start
                while start < group_cue.start() and block.text[start] in " ,;()":
                    start += 1
                if (
                    not candidate
                    or not candidate[:1].isupper()
                    or len(candidate) > 110
                    or _key(candidate) in _GENERIC
                    or _BAD_FALLBACK_NAME_START_RE.search(candidate)
                ):
                    continue
                end = window_start + pin.end()
                candidate_key = _match_key(candidate)
                if any(
                    candidate_key == local_key
                    or (
                        len(candidate_key) >= 6
                        and (candidate_key in local_key or local_key in candidate_key)
                    )
                    for local_key in local_short_keys
                ) or any(
                    start < known_end and end > known_start for known_start, known_end in occupied
                ):
                    continue
                mention = _unresolved_back_reference_mention(
                    case,
                    block,
                    start=start,
                    end=end,
                    cited_name=candidate,
                    ordinal=ordinal,
                    method="explicit_unresolved_grouped_back_reference",
                )
                found.append((block.char_start + start, mention))
                occupied.append((start, end))
                ordinal += 1
        # Explicit ``cited above``/``précité`` forms are citation loci even
        # when neither SCL nor the official authority can identify the case.
        # They remain unresolved and therefore cannot create graph edges.
        for cue_match in re.finditer(
            r"\b(?:cited\s+above|pr[ée]cit[ée]e?)\b",
            block.text,
            re.I,
        ):
            selected = _explicit_back_reference_name(block.text, cue_match.start())
            if selected is None:
                continue
            start, cited_name = selected
            name_end = start + len(cited_name)
            cited_key = _key(cited_name)
            if any(
                cited_key == local_key
                or (len(cited_key) >= 6 and (cited_key in local_key or local_key in cited_key))
                for local_key in local_short_keys
            ) or any(
                start < known_end and name_end > known_start for known_start, known_end in occupied
            ):
                continue
            end = cue_match.end()
            pin = _PIN_RE.search(block.text[end : min(len(block.text), end + 70)])
            if pin is not None and pin.start() <= 5:
                end += pin.end()
            closing = re.match(r"\s*[])]", block.text[end:])
            if closing is not None:
                end += closing.end()
            mention = _unresolved_back_reference_mention(
                case,
                block,
                start=start,
                end=end,
                cited_name=cited_name,
                ordinal=ordinal,
                method="explicit_unresolved_back_reference",
            )
            found.append((block.char_start + start, mention))
            occupied.append((start, end))
            ordinal += 1
    found.sort(
        key=lambda value: (
            value[0],
            _evidence_offset(value[1].discovery_evidence.get("group_ordinal"), 1),
            value[1].mention_id,
        )
    )
    blocks = {block.block_id: block for block in spine.blocks}
    preliminary: list[CitationOccurrence] = []
    for _, mention in found:
        source_block = blocks.get(mention.source_block_id or "")
        if source_block is None:
            continue
        evidence = mention.discovery_evidence
        start = _evidence_offset(evidence.get("block_start"))
        end = _evidence_offset(evidence.get("block_end"), start)
        italic, bold = _style_at(source_block, start, end)
        method = str(evidence.get("method", "text_discovery"))
        locus_id = _locus_id(case, source_block, start, end)
        occurrence_group_id, group_ordinal, group_size = _group_fields(mention, locus_id)
        occurrence_id = hashlib.sha256(f"{locus_id}|{mention.mention_id}".encode()).hexdigest()
        preliminary.append(
            CitationOccurrence(
                occurrence_id=occurrence_id,
                locus_id=locus_id,
                citation_group_id=occurrence_group_id,
                group_ordinal=group_ordinal,
                group_size=group_size,
                mention_id=mention.mention_id,
                source_itemid=case.itemid,
                source_language=case.language,
                source_section=source_block.section,
                source_block_id=source_block.block_id,
                source_para_id=source_block.legal_para_id or source_block.para_id,
                source_para_num=(
                    source_block.legal_para_num
                    if source_block.legal_para_id is not None
                    else source_block.para_num
                ),
                block_start=start,
                block_end=end,
                document_start=source_block.char_start + start,
                document_end=source_block.char_start + end,
                raw_text=source_block.text[start:end],
                source_context=source_block.text,
                italic=italic,
                bold=bold,
                finder=method,
                evidence={"discovery_evidence": evidence},
                target_paragraphs=(
                    list(mention.target_paragraphs)
                    if method == "grouped_procedural_documents"
                    else []
                ),
                source_component=_component(source_block),
                source_opinion_id=source_block.opinion_id,
                source_opinion_ordinal=source_block.opinion_ordinal,
                source_opinion_type=source_block.opinion_type,
                source_opinion_authors=list(source_block.opinion_authors),
                source_opinion_joined_by=list(source_block.opinion_joined_by),
                source_footnote_id=source_block.footnote_id,
                source_invoking_block_ids=list(source_block.referenced_by_block_ids),
                source_invoking_para_ids=list(source_block.referenced_by_para_ids),
                scl_coverage="indeterminate",
                discovery_methods=[method],
                resolution_scope="unresolved",
            )
        )
    _attach_source_invocations(preliminary, blocks)
    _assign_owned_pinpoints(preliminary, blocks, diagnostics)
    return CitationDiscoveryResult(
        mentions=[value[1] for value in found],
        preliminary_occurrences=preliminary,
        rejected_candidates=diagnostics,
        diagnostics=diagnostics,
    )


def _is_article_pinpoint(text: str, start: int) -> bool:
    before = text[max(0, start - 40) : start]
    return bool(
        re.search(r"(?:Article|Protocol|Rule|Articles)\s+\d+(?:\s*§\s*\d+)?\s*$", before, re.I)
    )


def _first_owned_pinpoint(text: str) -> re.Match[str] | None:
    return next(
        (
            match
            for match in _PIN_RE.finditer(text)
            if not _is_article_pinpoint(text, match.start())
        ),
        None,
    )


def _pinpoint_labels(match: re.Match[str]) -> list[str]:
    """Parse labels without swallowing a following citation date as a label."""
    labels: list[str] = []
    group = match.group("labels")
    for label_match in _LABEL_RE.finditer(group):
        absolute_end = match.start("labels") + label_match.end()
        following = match.string[absolute_end : absolute_end + 24]
        if labels and re.match(rf"\s+{_MONTH_WORD}\b", following, re.I):
            break
        labels.append(label_match.group())
    return labels


def _target_paragraphs(
    text: str,
    start: int,
    end: int,
    *,
    next_citation_start: int | None = None,
) -> tuple[list[str], str | None]:
    """Return only pinpoints owned by this citation envelope.

    A pinpoint inside the matched citation wins. Otherwise scanning stops at
    the next accepted citation, semicolon, newline, or sentence boundary. It
    never looks backwards, because that lets the preceding authority steal a
    following authority's pinpoint.
    """
    if match := _first_owned_pinpoint(text[start:end]):
        return _pinpoint_labels(match), "inside_envelope"
    limit = min(len(text), end + 240)
    if next_citation_start is not None:
        limit = min(limit, next_citation_start)
    after = text[end:limit]
    boundaries = [
        match.start()
        for pattern in (r"[;\n]", r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Þ])")
        if (match := re.search(pattern, after))
    ]
    if boundaries:
        after = after[: min(boundaries)]
    if match := _first_owned_pinpoint(after):
        return _pinpoint_labels(match), "following_envelope"
    return [], None


def _assign_owned_pinpoints(
    occurrences: list[CitationOccurrence],
    blocks: dict[str, DocumentBlock],
    diagnostics: list[dict[str, object]],
) -> None:
    grouped: dict[str, list[CitationOccurrence]] = defaultdict(list)
    for occurrence in occurrences:
        grouped[occurrence.source_block_id].append(occurrence)
    for block_id, values in grouped.items():
        block = blocks.get(block_id)
        if block is None:
            continue
        values.sort(key=lambda value: (value.block_start, value.block_end))
        for index, occurrence in enumerate(values):
            if occurrence.group_size > 1 and occurrence.target_paragraphs:
                occurrence.evidence["pinpoint_source"] = "grouped_procedural_documents"
                occurrence.evidence["pinpoint_boundary"] = occurrence.block_end
                continue
            next_start = values[index + 1].block_start if index + 1 < len(values) else None
            labels, source = _target_paragraphs(
                block.text,
                occurrence.block_start,
                occurrence.block_end,
                next_citation_start=next_start,
            )
            occurrence.target_paragraphs = labels
            occurrence.evidence["pinpoint_source"] = source
            occurrence.evidence["pinpoint_boundary"] = next_start
            if not labels and next_start is not None:
                intervening = block.text[occurrence.block_end : next_start]
                if _PIN_RE.search(intervening):
                    diagnostics.append(
                        {
                            "code": "unowned_pinpoint",
                            "source_itemid": occurrence.source_itemid,
                            "block_id": block_id,
                            "occurrence_id": occurrence.occurrence_id,
                            "raw_text": intervening,
                        }
                    )


def _duplicates_named_anchor(
    candidate: tuple[int, int, _Alias, bool, bool],
    candidates: list[tuple[int, int, _Alias, bool, bool]],
) -> bool:
    """Return whether a machine identifier is part of an already named cite."""
    start, end, alias, _, _ = candidate
    machine_finder = next(
        (
            finder
            for finder in ("application_number", "ecli", "reporter")
            if alias.finder == finder or alias.finder.endswith(f"_{finder}")
        ),
        None,
    )
    if machine_finder is None:
        return False
    return any(
        (
            other.owner.identity == alias.owner.identity
            or (
                machine_finder == "application_number"
                and bool(
                    set(APPNO_REGEX.findall(alias.text)).intersection(
                        other.owner.mention.explicit_appnos
                    )
                )
            )
        )
        and (
            other.finder in {"full_name", "target_name", "printed_title"}
            or other.finder.endswith("_full_name")
            or other.finder.endswith("_printed_title")
        )
        and other_start <= start
        and start - other_end <= 200
        for other_start, other_end, other, _, _ in candidates
    )


def _context(text: str, start: int, end: int, radius: int = 180) -> str:
    return text[max(0, start - radius) : min(len(text), end + radius)]


def _all_caps(value: str) -> bool:
    letters = [char for char in value if char.isalpha()]
    return len(letters) >= 8 and all(char.isupper() for char in letters)


def _unmatched_candidates(
    case: Case,
    block: DocumentBlock,
    known_spans: list[tuple[int, int]],
) -> list[dict[str, object]]:
    diagnostics: list[dict[str, object]] = []
    seen: set[tuple[int, int, str]] = set()
    for finder, pattern in _UNMATCHED_ANCHORS:
        for match in pattern.finditer(block.text):
            start, end = match.span()
            if any(
                start < known_end and end > known_start for known_start, known_end in known_spans
            ):
                continue
            identity = (start, end, finder)
            if identity in seen:
                continue
            seen.add(identity)
            italic, bold = _style_at(block, start, end)
            diagnostics.append(
                {
                    "code": "unmatched_candidate",
                    "source_itemid": case.itemid,
                    "block_id": block.block_id,
                    "para_id": block.para_id,
                    "block_start": start,
                    "block_end": end,
                    "document_start": block.char_start + start,
                    "document_end": block.char_start + end,
                    "raw_text": block.text[start:end],
                    "finder": finder,
                    "italic": italic,
                    "bold": bold,
                }
            )
    return diagnostics


def _occurrence(
    case: Case,
    block: DocumentBlock,
    alias: _Alias,
    start: int,
    end: int,
    *,
    italic: bool,
    bold: bool,
) -> CitationOccurrence:
    target = alias.owner.resolution.target if alias.owner.resolution else None
    raw = block.text[start:end]
    locus_id = _locus_id(case, block, start, end)
    occurrence_group_id, group_ordinal, group_size = _group_fields(alias.owner.mention, locus_id)
    digest = hashlib.sha256(f"{locus_id}|{alias.owner.mention.mention_id}".encode()).hexdigest()
    return CitationOccurrence(
        occurrence_id=digest,
        locus_id=locus_id,
        citation_group_id=occurrence_group_id,
        group_ordinal=group_ordinal,
        group_size=group_size,
        mention_id=alias.owner.mention.mention_id,
        source_itemid=case.itemid,
        source_language=case.language,
        source_section=block.section,
        source_block_id=block.block_id,
        source_para_id=block.legal_para_id or block.para_id,
        source_para_num=(
            block.legal_para_num if block.legal_para_id is not None else block.para_num
        ),
        block_start=start,
        block_end=end,
        document_start=block.char_start + start,
        document_end=block.char_start + end,
        raw_text=raw,
        source_context=block.text,
        italic=italic,
        bold=bold,
        finder=alias.finder,
        evidence={
            "alias": alias.text,
            "strong": alias.strong,
            "typography": "italic" if italic else "bold" if bold else "plain",
            "scl_reference": alias.owner.mention.raw_ref,
            "mention_reference_hash": alias.owner.mention.reference_hash,
            "mention_ordinal": alias.owner.mention.ordinal,
            "target_identity": alias.owner.identity,
            "discovery_evidence": alias.owner.mention.discovery_evidence,
            "citation_cue_present": _citation_cue_at(block.text, start, end),
            "resolution_status": (
                alias.owner.resolution.status if alias.owner.resolution else None
            ),
            "resolution_method": (
                alias.owner.resolution.method if alias.owner.resolution else None
            ),
            "short_form_gate": (
                "italic"
                if not alias.strong and italic
                else "cue_or_prior_strong"
                if not alias.strong
                else None
            ),
        },
        target_node_id=target.node_id if target else None,
        target_ecli=target.ecli if target else None,
        target_itemid=target.itemid if target else None,
        target_appnos=list(target.appnos) if target else [],
        target_paragraphs=[],
        source_component=_component(block),
        source_opinion_id=block.opinion_id,
        source_opinion_ordinal=block.opinion_ordinal,
        source_opinion_type=block.opinion_type,
        source_opinion_authors=list(block.opinion_authors),
        source_opinion_joined_by=list(block.opinion_joined_by),
        source_footnote_id=block.footnote_id,
        source_invoking_block_ids=list(block.referenced_by_block_ids),
        source_invoking_para_ids=list(block.referenced_by_para_ids),
        scl_coverage=(
            "not_covered" if alias.owner.mention.origin == "text_discovery" else "covered"
        ),
        scl_mention_ids=(
            []
            if alias.owner.mention.origin == "text_discovery"
            else [alias.owner.mention.mention_id]
        ),
        discovery_methods=[alias.finder],
        resolution_scope=(
            "document"
            if target and target.node_id
            else "application"
            if (
                alias.owner.resolution
                and alias.owner.resolution.candidates
                and any(
                    set(candidate.appnos).intersection(alias.owner.mention.explicit_appnos)
                    for candidate in alias.owner.resolution.candidates
                )
            )
            else "unresolved"
        ),
        resolution_candidates=(
            [
                {
                    "node_id": candidate.node_id,
                    "itemid": candidate.itemid,
                    "ecli": candidate.ecli,
                    "docname": candidate.docname,
                    "appnos": list(candidate.appnos),
                    "date": candidate.date.isoformat() if candidate.date else None,
                    "document_kind": candidate.document_kind,
                }
                for candidate in alias.owner.resolution.candidates
            ]
            if alias.owner.resolution
            else []
        ),
    )


def extract_citation_occurrences(
    case: Case,
    resolutions: Iterable[CitationResolution] | None = None,
    *,
    html: str | None = None,
    spine: DocumentSpine | None = None,
    scope: str = "scl",
) -> CitationOccurrenceResult:
    """Locate SCL authorities in source paragraphs without an LLM.

    ``resolutions`` supplies authoritative target identities.  When omitted,
    occurrences still link to parsed SCL mention IDs but remain unresolved.
    """
    if scope not in {"scl", "inclusive"}:
        raise ValueError("scope must be 'scl' or 'inclusive'")
    spine = _discovery_spine(case, html=html, spine=spine)
    resolution_values = list(resolutions or [])
    owners = _owners(case, resolution_values)
    scl_mentions = parse_scl_mentions(case)
    discovery = (
        discover_citation_mentions(case, html=html, spine=spine)
        if scope == "inclusive"
        else CitationDiscoveryResult()
    )
    supplied_by_mention = {value.mention.mention_id: value for value in resolution_values}
    coverage = {
        mention.mention_id: _coverage_matches(mention, scl_mentions)
        for mention in discovery.mentions
    }
    # SCL sometimes supplies only an applicant plus application number.  A
    # compatible full title discovered from the exact numbered envelope can
    # enrich the document-local gazetteer while SCL retains mention identity
    # and target authority.
    discovered_names_by_scl: dict[str, CitationMention] = {}
    for mention in discovery.mentions:
        if not mention.cited_name:
            continue
        for covered in coverage[mention.mention_id]:
            if covered.cited_name:
                continue
            discovered_names_by_scl.setdefault(covered.mention_id, mention)
    if discovered_names_by_scl:
        owners = [
            _Owner(
                owner.mention.model_copy(
                    update={
                        "cited_name": discovered_names_by_scl[owner.mention.mention_id].cited_name,
                        "respondent": discovered_names_by_scl[owner.mention.mention_id].respondent,
                    }
                ),
                owner.resolution,
            )
            if owner.mention.mention_id in discovered_names_by_scl
            else owner
            for owner in owners
        ]
    for mention in discovery.mentions:
        if coverage[mention.mention_id]:
            continue
        owners.append(_Owner(mention, supplied_by_mention.get(mention.mention_id)))
    aliases, ambiguous = _gazetteer(owners, source_appnos=_case_appnos(case))
    prepared_aliases = [
        (alias, _pattern(alias.text), _anchor_token(alias.text)) for alias in aliases
    ]
    prepared_ambiguous = [
        (key, entries, _pattern(entries[0].text), _anchor_token(entries[0].text))
        for key, entries in ambiguous.items()
    ]
    source_blocks = {block.block_id: block for block in spine.blocks}
    discovery_anchor_positions: dict[str, list[int]] = defaultdict(list)
    for mention in discovery.mentions:
        block = source_blocks.get(mention.source_block_id or "")
        if block is None:
            continue
        selected = coverage[mention.mention_id][0] if coverage[mention.mention_id] else mention
        end = _evidence_offset(mention.discovery_evidence.get("block_end"))
        discovery_anchor_positions[_Owner(selected, None).identity].append(block.char_start + end)
    occurrences: list[CitationOccurrence] = []
    diagnostics: list[dict[str, object]] = list(discovery.diagnostics)
    established: set[str] = set()
    established_positions: dict[str, int] = {}
    ambiguous_hits = 0
    unmatched_hits = 0

    for block in spine.blocks:
        if (
            block.type == "heading"
            and "\n" not in block.text
            and not APPNO_REGEX.search(block.text)
        ) or block.heading_role == "frontmatter":
            continue
        if str(block.para_id or "").startswith("pre-") or _all_caps(block.text):
            continue

        block_tokens = set(_match_key(block.text).split())
        candidates: list[tuple[int, int, _Alias, bool, bool]] = []
        raw_candidates: list[tuple[int, int, _Alias, bool, bool]] = []
        for alias, pattern, anchor in prepared_aliases:
            if anchor is not None and anchor not in block_tokens:
                continue
            for match in pattern.finditer(block.text):
                italic, bold = _style_at(block, match.start(), match.end())
                raw_candidates.append((match.start(), match.end(), alias, italic, bold))

        for start, end, alias, italic, bold in raw_candidates:
            nearby = _context(block.text, start, end, radius=80)
            if (
                "same_application_" in alias.finder or _same_source_party(case, alias.owner)
            ) and not _prior_document_cue_at(block.text, start, end):
                continue
            earlier_strong = any(
                other.strong and other.owner.identity == alias.owner.identity and other_end <= start
                for _, other_end, other, _, _ in raw_candidates
            )
            earlier_discovery = any(
                position <= block.char_start + start
                for position in discovery_anchor_positions.get(alias.owner.identity, [])
            )
            if not alias.strong and not (
                italic
                or _citation_cue_at(block.text, start, end)
                or alias.owner.identity in established
                or earlier_strong
                or earlier_discovery
            ):
                continue
            raw = block.text[start:end]
            if not alias.strong and " " not in raw.strip() and raw[:1].islower():
                continue
            if not alias.strong:
                document_cue = _ATTACHED_DOCUMENT_CUE_RE.match(block.text[end:])
                if document_cue is not None:
                    end += document_cue.end()
            candidates.append((start, end, alias, italic, bold))

        for key, entries, pattern, anchor in prepared_ambiguous:
            if anchor is not None and anchor not in block_tokens:
                continue
            for match in pattern.finditer(block.text):
                italic, bold = _style_at(block, match.start(), match.end())
                nearby = _context(block.text, match.start(), match.end(), radius=100)
                available_positions = dict(established_positions)
                absolute_start = block.char_start + match.start()
                for identity, positions in discovery_anchor_positions.items():
                    prior_positions = [
                        position for position in positions if position <= absolute_start
                    ]
                    if prior_positions:
                        available_positions[identity] = max(
                            available_positions.get(identity, -1), max(prior_positions)
                        )
                selected_alias = _printed_evidence_alias(
                    entries, block.text, match.start(), match.end()
                ) or _antecedent_alias(
                    entries,
                    available_positions,
                    nearby,
                    italic=italic,
                )
                if (
                    selected_alias is not None
                    and (
                        "same_application_" in selected_alias.finder
                        or _same_source_party(case, selected_alias.owner)
                    )
                    and not _prior_document_cue_at(block.text, match.start(), match.end())
                ):
                    selected_alias = None
                if selected_alias is not None:
                    candidates.append((match.start(), match.end(), selected_alias, italic, bold))
                    raw_candidates.append(
                        (match.start(), match.end(), selected_alias, italic, bold)
                    )
                    diagnostics.append(
                        {
                            "code": "disambiguated_alias",
                            "source_itemid": case.itemid,
                            "block_id": block.block_id,
                            "para_id": block.para_id,
                            "block_start": match.start(),
                            "block_end": match.end(),
                            "raw_text": block.text[match.start() : match.end()],
                            "alias_key": key,
                            "mention_id": selected_alias.owner.mention.mention_id,
                            "method": selected_alias.finder,
                        }
                    )
                    continue
                ambiguous_hits += 1
                diagnostics.append(
                    {
                        "code": "ambiguous_alias",
                        "source_itemid": case.itemid,
                        "block_id": block.block_id,
                        "para_id": block.para_id,
                        "block_start": match.start(),
                        "block_end": match.end(),
                        "raw_text": block.text[match.start() : match.end()],
                        "alias_key": key,
                        "mention_ids": sorted(
                            {entry.owner.mention.mention_id for entry in entries}
                        ),
                    }
                )
                raw_candidates.append((match.start(), match.end(), entries[0], italic, bold))

        unmatched = _unmatched_candidates(
            case,
            block,
            [(start, end) for start, end, _, _, _ in raw_candidates],
        )
        unmatched_hits += len(unmatched)
        diagnostics.extend(unmatched)

        # At one locus, retain the longest/highest-confidence alias only.
        candidates.sort(key=lambda item: (item[0], not item[2].strong, -(item[1] - item[0])))
        accepted_spans: list[tuple[int, int]] = []
        for start, end, alias, italic, bold in candidates:
            if any(start < old_end and end > old_start for old_start, old_end in accepted_spans):
                continue
            if _duplicates_named_anchor((start, end, alias, italic, bold), candidates):
                continue
            occurrence = _occurrence(case, block, alias, start, end, italic=italic, bold=bold)
            occurrences.append(occurrence)
            accepted_spans.append((start, end))
            if alias.strong:
                established.add(alias.owner.identity)
                established_positions[alias.owner.identity] = block.char_start + end

    # A discovered strong envelope is authoritative for its printed locus,
    # regardless of whether SCL covers the cited authority.  The ordinary
    # gazetteer can miss the exact form when two procedural documents share a
    # title; source offsets must not disappear merely because target identity
    # remains ambiguous.
    blocks = {block.block_id: block for block in spine.blocks}
    resolution_by_mention = {value.mention.mention_id: value for value in resolution_values}
    owner_identity_by_mention = {owner.mention.mention_id: owner.identity for owner in owners}
    for mention in discovery.mentions:
        matches = coverage[mention.mention_id]
        identities = matches or [mention]
        selected = identities[0]
        selected_identity = _Owner(selected, None).identity
        evidence = mention.discovery_evidence
        discovery_block = blocks.get(mention.source_block_id or "")
        if discovery_block is None:
            continue
        start_value = evidence.get("block_start", 0)
        end_value = evidence.get("block_end", start_value)
        start = start_value if isinstance(start_value, int) else 0
        end = end_value if isinstance(end_value, int) else start
        overlapping_values = [
            value
            for value in occurrences
            if value.source_block_id == discovery_block.block_id
            and start < value.block_end
            and end > value.block_start
        ]
        # ``cited above`` and its French equivalents refer back to the
        # document-local authority established by an earlier strong anchor.
        # Prefer that uniquely resolved local owner over a same-title SCL row
        # for another procedural phase. The SCL row remains present in the
        # decision-level SCL artifact; it must not create a second occurrence
        # at this printed locus.
        exact_local_prior = [
            value
            for value in overlapping_values
            if value.block_start == start
            and value.block_end == end
            and value.resolution_scope == "document"
            and _prior_document_cue_at(discovery_block.text, start, end)
        ]
        if len(exact_local_prior) == 1:
            keeper = exact_local_prior[0]
            keeper.discovery_methods = sorted(
                {
                    *keeper.discovery_methods,
                    str(mention.discovery_evidence.get("method", "text_discovery")),
                }
            )
            diagnostics.append(
                {
                    "code": "local_prior_authority_preferred",
                    "source_itemid": case.itemid,
                    "block_id": discovery_block.block_id,
                    "block_start": start,
                    "block_end": end,
                    "kept_occurrence_id": keeper.occurrence_id,
                    "rejected_mention_id": mention.mention_id,
                }
            )
            continue
        ids = sorted(value.mention_id for value in identities)

        def same_authority(
            value: CitationOccurrence,
            candidate_ids: frozenset[str] = frozenset(ids),
            identity: str = selected_identity,
        ) -> bool:
            return bool(
                value.mention_id in candidate_ids
                or set(value.scl_mention_ids).intersection(candidate_ids)
                or owner_identity_by_mention.get(value.mention_id) == identity
            )

        grouped_discovery = _evidence_offset(evidence.get("group_size"), 1) > 1
        # A machine identifier printed inside a name-bearing envelope is
        # evidence for that same physical citation, not a substitute locus.
        # Remove the contained identifier so the exact discovered envelope
        # below owns the source span and its SCL provenance.
        contained_identifiers = {
            value.occurrence_id
            for value in overlapping_values
            if value.finder in {"application_number", "ecli", "reporter"}
            and start <= value.block_start
            and value.block_end <= end
            and same_authority(value)
        }
        if contained_identifiers:
            occurrences[:] = [
                value for value in occurrences if value.occurrence_id not in contained_identifiers
            ]
            overlapping_values = [
                value
                for value in overlapping_values
                if value.occurrence_id not in contained_identifiers
            ]
        if grouped_discovery:
            # A compound printed envelope owns the full shared locus.  Remove
            # a shorter gazetteer alias for the same SCL authority before
            # emitting the target-specific grouped rows.
            removable = {
                value.occurrence_id for value in overlapping_values if same_authority(value)
            }
            if removable:
                occurrences[:] = [
                    value for value in occurrences if value.occurrence_id not in removable
                ]
                overlapping_values = [
                    value for value in overlapping_values if value.occurrence_id not in removable
                ]
        overlapping = next(
            (value for value in overlapping_values if same_authority(value)),
            None,
        )
        if overlapping is not None and not grouped_discovery:
            exact_envelope_is_longer = (
                start <= overlapping.block_start
                and end >= overlapping.block_end
                and (start, end) != (overlapping.block_start, overlapping.block_end)
            )
            if exact_envelope_is_longer:
                occurrences[:] = [
                    value
                    for value in occurrences
                    if value.occurrence_id != overlapping.occurrence_id
                ]
                overlapping_values = [
                    value
                    for value in overlapping_values
                    if value.occurrence_id != overlapping.occurrence_id
                ]
            else:
                overlapping.scl_coverage = "covered" if matches else "not_covered"
                if matches:
                    overlapping.scl_mention_ids = sorted(set([*overlapping.scl_mention_ids, *ids]))
                overlapping.discovery_methods = sorted(
                    set(
                        [
                            *overlapping.discovery_methods,
                            str(mention.discovery_evidence.get("method", "text_discovery")),
                        ]
                    )
                )
                if mention.discovery_evidence.get("namespace") == "echr_commission":
                    overlapping.evidence["classified_commission_provenance"] = dict(
                        mention.discovery_evidence
                    )
                continue
        if overlapping_values:
            diagnostics.append(
                {
                    "code": "overlapping_distinct_authorities",
                    "source_itemid": case.itemid,
                    "block_id": discovery_block.block_id,
                    "block_start": start,
                    "block_end": end,
                    "discovery_mention_id": mention.mention_id,
                    "scl_mention_ids": ids,
                    "overlapping_occurrence_ids": [
                        value.occurrence_id for value in overlapping_values
                    ],
                }
            )
        alias = _Alias(
            text=mention.raw_ref,
            key=_key(mention.raw_ref),
            owner=_Owner(selected, resolution_by_mention.get(selected.mention_id)),
            finder=str(mention.discovery_evidence.get("method", "text_discovery")),
            strong=True,
        )
        italic, bold = _style_at(discovery_block, start, end)
        value = _occurrence(case, discovery_block, alias, start, end, italic=italic, bold=bold)
        value.scl_coverage = "covered" if matches else "not_covered"
        value.scl_mention_ids = ids if matches else []
        if grouped_discovery:
            occurrence_group_id, group_ordinal, group_size = _group_fields(
                mention, value.locus_id or value.occurrence_id
            )
            value.citation_group_id = occurrence_group_id
            value.group_ordinal = group_ordinal
            value.group_size = group_size
            value.target_paragraphs = list(mention.target_paragraphs)
        value.discovery_methods = [
            str(mention.discovery_evidence.get("method", "text_discovery")),
            *(["scl_identity"] if matches else []),
        ]
        if mention.discovery_evidence.get("namespace") == "echr_commission":
            value.evidence["classified_commission_provenance"] = dict(mention.discovery_evidence)
        occurrences.append(value)

    _carry_forward_occurrences(case, spine, occurrences, owners, diagnostics)
    _merge_classified_commission_report_overlaps(occurrences)
    _merge_compatible_duplicate_loci(occurrences, diagnostics)
    _attach_source_invocations(occurrences, blocks)
    _assign_owned_pinpoints(occurrences, blocks, diagnostics)
    occurrences.sort(
        key=lambda value: (
            value.document_start,
            value.document_end,
            value.group_ordinal,
            value.mention_id,
        )
    )
    located = {value.mention_id for value in occurrences}
    for owner in owners:
        if owner.mention.mention_id not in located:
            diagnostics.append(
                {
                    "code": "unlocated_mention",
                    "source_itemid": case.itemid,
                    "mention_id": owner.mention.mention_id,
                    "raw_reference": owner.mention.raw_ref,
                }
            )
    methods = Counter(value.finder for value in occurrences)
    components = Counter(value.source_component for value in occurrences)
    sections = Counter(value.source_section or "unknown" for value in occurrences)
    inclusive_edges: list[dict[str, object]] = []
    edge_rows: dict[tuple[str, str], list[CitationOccurrence]] = defaultdict(list)
    for value in occurrences:
        if value.target_node_id and value.resolution_scope == "document":
            edge_rows[(case.itemid or "", value.target_node_id)].append(value)
    for (source, target), values in sorted(edge_rows.items()):
        inclusive_edges.append(
            {
                "source": source,
                "target": target,
                "occurrence_count": len(values),
                "mention_count": len({value.mention_id for value in values}),
                "scl_covered_occurrence_count": sum(
                    value.scl_coverage == "covered" for value in values
                ),
                "text_only_occurrence_count": sum(
                    value.scl_coverage == "not_covered" for value in values
                ),
                "occurrence_ids": [value.occurrence_id for value in values],
            }
        )
    scl_ids = {value.mention_id for value in scl_mentions}
    located_scl = located.intersection(scl_ids)
    report = CitationOccurrenceReport(
        documents=1,
        scl_mentions=len(scl_mentions),
        occurrences=len(occurrences),
        located_mentions=len(located_scl),
        unlocated_mentions=max(0, len(scl_mentions) - len(located_scl)),
        ambiguous_hits=ambiguous_hits,
        unmatched_candidates=unmatched_hits,
        methods=dict(methods),
        text_discovered_mentions=len(discovery.mentions),
        text_only_occurrences=sum(value.scl_coverage == "not_covered" for value in occurrences),
        scl_covered_occurrences=sum(value.scl_coverage == "covered" for value in occurrences),
        components={str(key): value for key, value in components.items()},
        sections={str(key): value for key, value in sections.items()},
    )
    return CitationOccurrenceResult(
        occurrences=occurrences,
        report=report,
        diagnostics=diagnostics,
        mentions=[*scl_mentions, *discovery.mentions],
        inclusive_edges=inclusive_edges,
    )
