"""Canonical ECHR judge roster: name normalization, country, region, tenure.

The data was verified against the official ECHR "Judges of the Court since
1959" list (currently 188 curated names: 168 elected + 20 ad hoc). The roster
is deliberately auditable but not yet an exhaustive transcription of every
historical judge in the official list.

"Country" is the state *in respect of which* the judge was elected -- not
necessarily personal nationality (Liechtenstein judges are often Swiss).
Used by :mod:`hudoc_py.text.opinions` to canonicalize authoring-judge names.
"""

from __future__ import annotations

import re
import unicodedata

_STROKE_TABLE = str.maketrans(
    {"Ø": "O", "Đ": "D", "Ł": "L", "Æ": "AE", "Ð": "D", "Þ": "TH", "ß": "SS", "Œ": "OE"}
)


def _fold(name: str) -> str:
    """Accent/stroke-insensitive key used only for verified roster lookup."""
    translated = name.upper().translate(_STROKE_TABLE)
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", translated)
        if not unicodedata.combining(char)
    )

# Explicit variant -> canonical mappings (applied after upper-casing).
_ALIAS = {
    # Diacritic variants
    "DAVID THÒR BJÖRGVINSSON":   "DAVÍD THÓR BJÖRGVINSSON",
    "DAVID THÓR BJÖRGVINSSON":   "DAVÍD THÓR BJÖRGVINSSON",
    "BJÖRGVINSSON":              "DAVÍD THÓR BJÖRGVINSSON",
    "BERRO-LEFEVRE":             "BERRO-LEFÈVRE",
    "ZUPANČIĆ":                  "ZUPANČIČ",
    "ZUPANCIC":                  "ZUPANČIČ",
    "JOČIENÉ":                   "JOČIENĖ",
    "KARAKAS":                   "KARAKAŞ",
    "KURIS":                     "KŪRIS",
    "LOHMUS":                    "LÕHMUS",
    "SAJÒ":                      "SAJÓ",
    "POLACKOVA":                 "POLÁČKOVÁ",
    "PANŢÎRU":                   "PANTÎRU",
    # Space / hyphen variant
    "LAZAROVA TRAJKOVSKA":       "LAZAROVA-TRAJKOVSKA",
    "LÓPEZ-GUERRA":              "LÓPEZ GUERRA",
    # Partial names → canonical full form
    "BRATZA":                    "SIR NICOLAS BRATZA",
    "NICOLAS BRATZA":            "SIR NICOLAS BRATZA",
    "GAETANO":                   "DE GAETANO",
    "MEYER":                     "DE MEYER",
    "ALBUQUERQUE":               "PINTO DE ALBUQUERQUE",
    "PINTO DE":                  "PINTO DE ALBUQUERQUE",
    "PINTO":                     "PINTO DE ALBUQUERQUE",
    "FURA":                      "FURA-SANDSTRÖM",
    "BERRO":                     "BERRO-LEFÈVRE",
    "POWER":                     "POWER-FORDE",
}


def normalise_judge_name(name: str) -> str:
    """Normalise a judge name as parsed from an opinion heading or roster.

    Cleans parsing artifacts (page numbers, "JOINED ...", "JUDGE " prefixes),
    folds Unicode hyphen/apostrophe variants, and maps known spelling /
    partial-name variants to the canonical form.
    """
    n = name.strip().upper()

    # Garbage cleanup: trailing dot-leader page numbers ("KELLER.......82"),
    # "JOINED ..." / "CONCERNING ..." / "IN RESPECT OF ..." / "ON THE ..."
    # suffixes, and "JUDGE " / "AND " prefixes.
    n = re.sub(r"\.{2,}\s*\d+$", "", n).strip()
    n = re.sub(r"\s+JOINED.*$", "", n).strip()
    if n.startswith("JOINED "):
        m = re.search(r"JUDGE\s+(.+)", n)
        n = m.group(1).strip() if m else ""
    n = re.sub(r"\s+CONCERNING.*$", "", n).strip()
    n = re.sub(r"\s+IN RESPECT OF.*$", "", n).strip()
    n = re.sub(r"\s+ON THE\s+.*$", "", n).strip()
    if n.startswith("JUDGE "):
        n = n[6:].strip()
    if n.startswith("AND "):
        n = n[4:].strip()

    # Unicode normalisation: hyphen and apostrophe variants.
    n = re.sub(r"[\u2010\u2011\u2012\u2013\u2014\u2015\u00AD\u2212]", "-", n)
    n = n.replace("\u2018", "'").replace("\u2019", "'")

    if n in _ALIAS:
        return _ALIAS[n]
    if n in JUDGE_COUNTRY or n in AD_HOC_JUDGES:
        return n
    folded = _fold(n)
    if folded in _FOLD_INDEX:
        return _FOLD_INDEX[folded]
    loose = re.sub(r"[-\s]+", " ", folded).strip()
    return _FOLD_LOOSE.get(loose, n)


# -- Country mapping (canonical name -> state in respect of which elected) --

JUDGE_COUNTRY = {
    # Albania
    "PAVLI":                     "Albania",
    "BIANKU":                    "Albania",
    "TRAJA":                     "Albania",
    # Andorra
    "PASTOR VILANOVA":           "Andorra",
    "CASADEVALL":                "Andorra",
    # Armenia
    "HARUTYUNYAN":               "Armenia",
    "GYULUMYAN":                 "Armenia",
    # Austria
    "KUCSKO-STADLMAYER":         "Austria",
    "STEINER":                   "Austria",
    "FUHRMANN":                  "Austria",
    "MATSCHER":                  "Austria",
    # Azerbaijan
    "HÜSEYNOV":                  "Azerbaijan",
    "HAJIYEV":                   "Azerbaijan",
    # Belgium
    "KRENC":                     "Belgium",
    "LEMMENS":                   "Belgium",
    "TULKENS":                   "Belgium",
    "DE MEYER":                  "Belgium",
    # Bosnia and Herzegovina
    "VEHABOVIĆ":                 "Bosnia and Herzegovina",
    "MIJOVIĆ":                   "Bosnia and Herzegovina",
    # Bulgaria
    "GROZEV":                    "Bulgaria",
    "KALAYDJIEVA":               "Bulgaria",
    "BOTOUCHAROVA":              "Bulgaria",
    "GOTCHEV":                   "Bulgaria",
    # Croatia
    "DERENČINOVIĆ":              "Croatia",
    "TURKOVIĆ":                  "Croatia",
    "VAJIĆ":                     "Croatia",
    # Cyprus
    "SERGHIDES":                 "Cyprus",
    "NICOLAOU":                  "Cyprus",
    "LOUCAIDES":                 "Cyprus",
    "LOIZOU":                    "Cyprus",
    # Czech Republic
    "ŠIMÁČKOVÁ":                 "Czech Republic",
    "PEJCHAL":                   "Czech Republic",
    "JUNGWIERT":                 "Czech Republic",
    # Denmark
    "BORMANN":                   "Denmark",
    "KJØLBRO":                   "Denmark",
    "LORENZEN":                  "Denmark",
    "FOIGHEL":                   "Denmark",
    # Estonia
    "ROOSMA":                    "Estonia",
    "LAFFRANQUE":                "Estonia",
    "MARUSTE":                   "Estonia",
    "LÕHMUS":                    "Estonia",
    # Finland
    "KOSKELO":                   "Finland",
    "HIRVELÄ":                   "Finland",
    "PELLONPÄÄ":                 "Finland",
    "PEKKANEN":                  "Finland",
    # France
    "GUYOMAR":                   "France",
    "COSTA":                     "France",
    "PETTITI":                   "France",
    # Georgia
    "CHANTURIA":                 "Georgia",
    "TSOTSORIA":                 "Georgia",
    "UGREKHELIDZE":              "Georgia",
    # Germany
    "SEIBERT-FOHR":              "Germany",
    "NUSSBERGER":                "Germany",
    "JAEGER":                    "Germany",
    "RESS":                      "Germany",
    "BERNHARDT":                 "Germany",
    # Greece
    "KTISTAKIS":                 "Greece",
    "SICILIANOS":                "Greece",
    "ROZAKIS":                   "Greece",
    "VALTICOS":                  "Greece",
    # Hungary
    "PACZOLAY":                  "Hungary",
    "SAJÓ":                      "Hungary",
    "BAKA":                      "Hungary",
    # Iceland
    "ARNARDÓTTIR":               "Iceland",
    "SPANO":                     "Iceland",
    "DAVÍD THÓR BJÖRGVINSSON":   "Iceland",
    "GAUKUR JÖRUNDSSON":         "Iceland",
    "THÓR VILHJÁLMSSON":         "Iceland",
    # Ireland
    "NÍ RAIFEARTAIGH":           "Ireland",
    "O'LEARY":                   "Ireland",
    "POWER-FORDE":               "Ireland",
    "HEDIGAN":                   "Ireland",
    "WALSH":                     "Ireland",
    # Italy
    "SABATO":                    "Italy",
    "RAIMONDI":                  "Italy",
    "ZAGREBELSKY":               "Italy",
    "CONFORTI":                  "Italy",
    "RUSSO":                     "Italy",
    # Latvia
    "MITS":                      "Latvia",
    "ZIEMELE":                   "Latvia",
    "LEVITS":                    "Latvia",
    # Liechtenstein (judges often Swiss/Canadian by personal nationality)
    "RANZONI":                   "Liechtenstein",
    "VILLIGER":                  "Liechtenstein",
    "CAFLISCH":                  "Liechtenstein",
    "MACDONALD":                 "Liechtenstein",
    # Lithuania
    "KŪRIS":                     "Lithuania",
    "JOČIENĖ":                   "Lithuania",
    # Luxembourg
    "RAVARANI":                  "Luxembourg",
    "SPIELMANN":                 "Luxembourg",
    "FISCHBACH":                 "Luxembourg",
    # Malta
    "SCHEMBRI ORLAND":           "Malta",
    "DE GAETANO":                "Malta",
    "BONELLO":                   "Malta",
    "MIFSUD BONNICI":            "Malta",
    # Republic of Moldova
    "GRIŢCO":                    "Moldova",
    "POALELUNGI":                "Moldova",
    "PAVLOVSCHI":                "Moldova",
    "PANTÎRU":                   "Moldova",
    # Monaco
    "MOUROU-VIKSTRÖM":           "Monaco",
    "BERRO-LEFÈVRE":             "Monaco",
    # Montenegro
    "JELIĆ":                     "Montenegro",
    "VUČINIĆ":                   "Montenegro",
    # Netherlands
    "SILVIS":                    "Netherlands",
    "MYJER":                     "Netherlands",
    "THOMASSEN":                 "Netherlands",
    "VAN DIJK":                  "Netherlands",
    "MARTENS":                   "Netherlands",
    # North Macedonia
    "ILIEVSKI":                  "North Macedonia",
    "LAZAROVA-TRAJKOVSKA":       "North Macedonia",
    "TSATSA-NIKOLOVSKA":         "North Macedonia",
    # Norway
    "BÅRDSEN":                   "Norway",
    "MØSE":                      "Norway",
    "JEBENS":                    "Norway",
    "GREVE":                     "Norway",
    "RYSSDAL":                   "Norway",
    # Poland
    "WOJTYCZEK":                 "Poland",
    "GARLICKI":                  "Poland",
    "MAKARCZYK":                 "Poland",
    # Portugal
    "GUERRA MARTINS":            "Portugal",
    "PINTO DE ALBUQUERQUE":      "Portugal",
    "CABRAL BARRETO":            "Portugal",
    "LOPES ROCHA":               "Portugal",
    # Romania
    "RĂDULEŢU":                  "Romania",
    "MOTOC":                     "Romania",
    "BÎRSAN":                    "Romania",
    "VOICU":                     "Romania",
    # Russian Federation
    "LOBOV":                     "Russia",
    "DEDOV":                     "Russia",
    "KOVLER":                    "Russia",
    # San Marino
    "FELICI":                    "San Marino",
    "PARDALOS":                  "San Marino",
    "MULARONI":                  "San Marino",
    "FERRARI BRAVO":             "San Marino",
    "BIGI":                      "San Marino",
    # Serbia
    "LUBARDA":                   "Serbia",
    "POPOVIĆ":                   "Serbia",
    # Slovak Republic
    "POLÁČKOVÁ":                 "Slovak Republic",
    "ŠIKUTA":                    "Slovak Republic",
    "STRÁŽNICKÁ":                "Slovak Republic",
    "REPIK":                     "Slovak Republic",
    # Slovenia
    "BOŠNJAK":                   "Slovenia",
    "ZUPANČIČ":                  "Slovenia",
    "JAMBREK":                   "Slovenia",
    # Spain
    "ELÓSEGUI":                  "Spain",
    "LÓPEZ GUERRA":              "Spain",
    "BORREGO BORREGO":           "Spain",
    "PASTOR RIDRUEJO":           "Spain",
    "MORENILLA":                 "Spain",
    # Sweden
    "WENNERSTRÖM":               "Sweden",
    "JÄDERBLOM":                 "Sweden",
    "FURA-SANDSTRÖM":            "Sweden",
    "PALM":                      "Sweden",
    # Switzerland
    "ZÜND":                      "Switzerland",
    "KELLER":                    "Switzerland",
    "MALINVERNI":                "Switzerland",
    "WILDHABER":                 "Switzerland",
    "BINDSCHEDLER-ROBERT":        "Switzerland",
    # Turkey
    "YÜKSEL":                    "Turkey",
    "KARAKAŞ":                   "Turkey",
    "TÜRMEN":                    "Turkey",
    "GÖLCÜKLÜ":                  "Turkey",
    # Ukraine
    "GNATOVSKYY":                "Ukraine",
    "YUDKIVSKA":                 "Ukraine",
    "BUTKEVYCH":                 "Ukraine",
    # United Kingdom
    "EICKE":                     "United Kingdom",
    "MAHONEY":                   "United Kingdom",
    "SIR NICOLAS BRATZA":        "United Kingdom",
    "SIR JOHN FREELAND":         "United Kingdom",
}

# ── Service years (start, end) ──────────────────────────────────────────────
# End year None = still serving as of 2025


# -- Service years (canonical name -> (start, end); end None = serving) --

JUDGE_YEARS = {
    # Albania
    "PAVLI":                     (2019, None),
    "BIANKU":                    (2008, 2019),
    "TRAJA":                     (1998, 2008),
    # Andorra
    "PASTOR VILANOVA":           (2015, 2024),
    "CASADEVALL":                (1996, 2015),
    # Armenia
    "HARUTYUNYAN":               (2015, 2025),
    "GYULUMYAN":                 (2003, 2014),
    # Austria
    "KUCSKO-STADLMAYER":         (2015, 2024),
    "STEINER":                   (2001, 2015),
    "FUHRMANN":                  (1998, 2001),
    "MATSCHER":                  (1977, 1998),
    # Azerbaijan
    "HÜSEYNOV":                  (2017, None),
    "HAJIYEV":                   (2003, 2016),
    # Belgium
    "KRENC":                     (2021, None),
    "LEMMENS":                   (2012, 2021),
    "TULKENS":                   (1998, 2012),
    "DE MEYER":                  (1986, 1998),
    # Bosnia and Herzegovina
    "VEHABOVIĆ":                 (2012, None),
    "MIJOVIĆ":                   (2004, 2011),
    # Bulgaria
    "GROZEV":                    (2015, 2024),
    "KALAYDJIEVA":               (2008, 2015),
    "BOTOUCHAROVA":              (1998, 2008),
    "GOTCHEV":                   (1992, 1998),
    # Croatia
    "DERENČINOVIĆ":              (2022, None),
    "TURKOVIĆ":                  (2013, 2022),
    "VAJIĆ":                     (1998, 2012),
    # Cyprus
    "SERGHIDES":                 (2016, None),
    "NICOLAOU":                  (2008, 2016),
    "LOUCAIDES":                 (1998, 2008),
    "LOIZOU":                    (1990, 1998),
    # Czech Republic
    "ŠIMÁČKOVÁ":                 (2021, None),
    "PEJCHAL":                   (2012, 2021),
    "JUNGWIERT":                 (1993, 2012),
    # Denmark
    "BORMANN":                   (2023, None),
    "KJØLBRO":                   (2014, 2022),
    "LORENZEN":                  (1998, 2014),
    "FOIGHEL":                   (1989, 1998),
    # Estonia
    "ROOSMA":                    (2020, None),
    "LAFFRANQUE":                (2011, 2020),
    "MARUSTE":                   (1998, 2010),
    "LÕHMUS":                    (1994, 1998),
    # Finland
    "KOSKELO":                   (2016, 2024),
    "HIRVELÄ":                   (2007, 2015),
    "PELLONPÄÄ":                 (1998, 2006),
    "PEKKANEN":                  (1989, 1998),
    # France
    "GUYOMAR":                   (2020, None),
    "COSTA":                     (1998, 2011),
    "PETTITI":                   (1980, 1998),
    # Georgia
    "CHANTURIA":                 (2018, None),
    "TSOTSORIA":                 (2008, 2018),
    "UGREKHELIDZE":              (1999, 2008),
    # Germany
    "SEIBERT-FOHR":              (2020, None),
    "NUSSBERGER":                (2011, 2019),
    "JAEGER":                    (2004, 2010),
    "RESS":                      (1998, 2004),
    "BERNHARDT":                 (1981, 1998),
    # Greece
    "KTISTAKIS":                 (2021, None),
    "SICILIANOS":                (2011, 2021),
    "ROZAKIS":                   (1998, 2011),
    "VALTICOS":                  (1986, 1998),
    # Hungary
    "PACZOLAY":                  (2017, None),
    "SAJÓ":                      (2008, 2017),
    "BAKA":                      (1991, 2008),
    # Iceland
    "ARNARDÓTTIR":               (2023, None),
    "SPANO":                     (2013, 2022),
    "DAVÍD THÓR BJÖRGVINSSON":   (2004, 2013),
    "GAUKUR JÖRUNDSSON":         (1998, 2004),
    "THÓR VILHJÁLMSSON":         (1971, 1998),
    # Ireland
    "NÍ RAIFEARTAIGH":           (2024, None),
    "O'LEARY":                   (2015, 2024),
    "POWER-FORDE":               (2008, 2014),
    "HEDIGAN":                   (1998, 2007),
    "WALSH":                     (1980, 1998),
    # Italy
    "SABATO":                    (2019, None),
    "RAIMONDI":                  (2010, 2019),
    "ZAGREBELSKY":               (2001, 2010),
    "CONFORTI":                  (1998, 2001),
    "RUSSO":                     (1981, 1998),
    # Latvia
    "MITS":                      (2015, 2024),
    "ZIEMELE":                   (2005, 2014),
    "LEVITS":                    (1995, 2004),
    # Liechtenstein
    "RANZONI":                   (2015, 2024),
    "VILLIGER":                  (2006, 2015),
    "CAFLISCH":                  (1998, 2006),
    "MACDONALD":                 (1980, 1998),
    # Lithuania
    "KŪRIS":                     (2013, 2024),  # Egidijus; Pranas (1994-2004) also existed
    "JOČIENĖ":                   (2004, 2013),
    # Luxembourg
    "RAVARANI":                  (2015, 2024),
    "SPIELMANN":                 (2004, 2015),
    "FISCHBACH":                 (1998, 2004),
    # Malta
    "SCHEMBRI ORLAND":           (2019, None),
    "DE GAETANO":                (2010, 2019),
    "BONELLO":                   (1998, 2010),
    "MIFSUD BONNICI":            (1992, 1998),
    # Republic of Moldova
    "GRIŢCO":                    (2012, 2021),
    "POALELUNGI":                (2008, 2012),
    "PAVLOVSCHI":                (2001, 2008),
    "PANTÎRU":                   (1996, 2001),
    # Monaco
    "MOUROU-VIKSTRÖM":           (2015, 2025),
    "BERRO-LEFÈVRE":             (2006, 2015),
    # Montenegro
    "JELIĆ":                     (2018, None),
    "VUČINIĆ":                   (2008, 2018),
    # Netherlands
    "SILVIS":                    (2012, 2016),
    "MYJER":                     (2004, 2012),
    "THOMASSEN":                 (1998, 2004),
    "VAN DIJK":                  (1996, 1998),
    "MARTENS":                   (1988, 1996),
    # North Macedonia
    "ILIEVSKI":                  (2017, None),
    "LAZAROVA-TRAJKOVSKA":       (2008, 2017),
    "TSATSA-NIKOLOVSKA":         (1998, 2008),
    # Norway
    "BÅRDSEN":                   (2019, None),
    "MØSE":                      (2011, 2018),
    "JEBENS":                    (2004, 2011),
    "GREVE":                     (1998, 2004),
    "RYSSDAL":                   (1973, 1998),
    # Poland
    "WOJTYCZEK":                 (2012, 2024),
    "GARLICKI":                  (2002, 2012),
    "MAKARCZYK":                 (1992, 2002),
    # Portugal
    "GUERRA MARTINS":            (2020, None),
    "PINTO DE ALBUQUERQUE":      (2011, 2020),
    "CABRAL BARRETO":            (1998, 2011),
    "LOPES ROCHA":               (1991, 1998),
    # Romania
    "RĂDULEŢU":                  (2023, None),
    "MOTOC":                     (2014, 2023),
    "BÎRSAN":                    (1998, 2013),
    "VOICU":                     (1996, 1998),
    # Russian Federation
    "LOBOV":                     (2022, 2022),
    "DEDOV":                     (2013, 2022),
    "KOVLER":                    (1999, 2012),
    # San Marino
    "FELICI":                    (2018, None),
    "PARDALOS":                  (2009, 2018),
    "MULARONI":                  (2001, 2008),
    "FERRARI BRAVO":             (1998, 2001),
    "BIGI":                      (1991, 1996),
    # Serbia
    "LUBARDA":                   (2015, 2024),
    "POPOVIĆ":                   (2005, 2015),
    # Slovak Republic
    "POLÁČKOVÁ":                 (2016, 2025),
    "ŠIKUTA":                    (2004, 2015),
    "STRÁŽNICKÁ":                (1998, 2004),
    "REPIK":                     (1992, 1998),
    # Slovenia
    "BOŠNJAK":                   (2016, 2025),
    "ZUPANČIČ":                  (1998, 2016),
    "JAMBREK":                   (1993, 1998),
    # Spain
    "ELÓSEGUI":                  (2018, None),
    "LÓPEZ GUERRA":              (2008, 2018),
    "BORREGO BORREGO":           (2003, 2008),
    "PASTOR RIDRUEJO":           (1998, 2003),
    "MORENILLA":                 (1990, 1998),
    # Sweden
    "WENNERSTRÖM":               (2019, None),
    "JÄDERBLOM":                 (2012, 2018),
    "FURA-SANDSTRÖM":            (2003, 2012),
    "PALM":                      (1988, 2003),
    # Switzerland
    "ZÜND":                      (2021, None),
    "KELLER":                    (2011, 2020),
    "MALINVERNI":                (2007, 2011),
    "WILDHABER":                 (1991, 2006),
    "BINDSCHEDLER-ROBERT":        (1975, 1991),
    # Turkey
    "YÜKSEL":                    (2019, None),
    "KARAKAŞ":                   (2008, 2019),
    "TÜRMEN":                    (1998, 2008),
    "GÖLCÜKLÜ":                  (1977, 1998),
    # Ukraine
    "GNATOVSKYY":                (2022, None),
    "YUDKIVSKA":                 (2010, 2022),
    "BUTKEVYCH":                 (1996, 2008),
    # United Kingdom
    "EICKE":                     (2016, 2025),
    "MAHONEY":                   (2012, 2016),
    "SIR NICOLAS BRATZA":        (1998, 2012),
    "SIR JOHN FREELAND":         (1991, 1998),
}


# -- Ad hoc judges (sat ad hoc, not elected in respect of a state) --

AD_HOC_JUDGES = {
    "ARDEN",
    "BACQUET",
    "BRIEDE",
    "CHARLETON",
    "DEL TUFO",
    "ERÖNEN",
    "FEDOROVA",
    "FINLAY GEOGHEGAN",
    "FUAD",
    "HADJIHAMBIS",
    "KOUMANTOS",
    "MARCUS-HELMONS",
    "NORDÉN",
    "REED",
    "SAIZ ARNAIZ",
    "SIR BRIAN KERR",
    "STRETEANU",
    "VAN COMPERNOLLE",
    "YERARIS",
    "ZALAR",
}


# These indices deliberately map only onto the verified roster. Unknown names
# still pass through unchanged; folding is not a general fuzzy-name matcher.
_ALL_CANONICAL_JUDGES = (*JUDGE_COUNTRY, *AD_HOC_JUDGES)
_FOLD_INDEX = {_fold(canonical): canonical for canonical in _ALL_CANONICAL_JUDGES}
_FOLD_LOOSE = {
    re.sub(r"[-\s]+", " ", _fold(canonical)).strip(): canonical
    for canonical in _ALL_CANONICAL_JUDGES
}


# -- Region groupings --

COUNTRY_REGION = {
    # Western Europe
    "France":                    "Western Europe",
    "Belgium":                   "Western Europe",
    "Netherlands":               "Western Europe",
    "Luxembourg":                "Western Europe",
    "Monaco":                    "Western Europe",
    "Liechtenstein":             "Western Europe",
    "Switzerland":               "Western Europe",
    "Germany":                   "Western Europe",
    "Austria":                   "Western Europe",
    "Andorra":                   "Western Europe",
    # Southern Europe
    "Italy":                     "Southern Europe",
    "Spain":                     "Southern Europe",
    "Portugal":                  "Southern Europe",
    "Malta":                     "Southern Europe",
    "San Marino":                "Southern Europe",
    "Greece":                    "Southern Europe",
    "Cyprus":                    "Southern Europe",
    "Turkey":                    "Southern Europe",
    # Nordic
    "Sweden":                    "Nordic",
    "Norway":                    "Nordic",
    "Denmark":                   "Nordic",
    "Finland":                   "Nordic",
    "Iceland":                   "Nordic",
    # British Isles
    "Ireland":                   "British Isles",
    "United Kingdom":            "British Isles",
    # Central Europe
    "Poland":                    "Central Europe",
    "Czech Republic":            "Central Europe",
    "Slovak Republic":           "Central Europe",
    "Hungary":                   "Central Europe",
    "Slovenia":                  "Central Europe",
    "Croatia":                   "Central Europe",
    # Southeast Europe
    "Serbia":                    "Southeast Europe",
    "North Macedonia":           "Southeast Europe",
    "Montenegro":                "Southeast Europe",
    "Bosnia and Herzegovina":    "Southeast Europe",
    "Albania":                   "Southeast Europe",
    "Romania":                   "Southeast Europe",
    "Bulgaria":                  "Southeast Europe",
    # Eastern Europe / Caucasus
    "Russia":                    "Eastern Europe",
    "Ukraine":                   "Eastern Europe",
    "Moldova":                   "Eastern Europe",
    "Georgia":                   "Eastern Europe",
    "Armenia":                   "Eastern Europe",
    "Azerbaijan":                "Eastern Europe",
    # Baltic
    "Estonia":                   "Baltic",
    "Latvia":                    "Baltic",
    "Lithuania":                 "Baltic",
}


def is_ad_hoc_judge(name: str) -> bool:
    """Whether this judge sat as an ad hoc judge."""
    return normalise_judge_name(name) in AD_HOC_JUDGES


def judge_country(name: str) -> str | None:
    """State in respect of which the judge was elected; None if unknown/ad hoc."""
    return JUDGE_COUNTRY.get(normalise_judge_name(name))


def judge_region(name: str) -> str | None:
    """Region grouping of the judge's country; None if unknown/ad hoc."""
    country = judge_country(name)
    return COUNTRY_REGION.get(country) if country else None


def judge_years(name: str) -> tuple[int | None, int | None]:
    """Service years ``(start, end)``; ``(None, None)`` if unknown, end None = serving."""
    return JUDGE_YEARS.get(normalise_judge_name(name), (None, None))
