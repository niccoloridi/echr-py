"""Tests for the canonical judge roster (names, country, region, tenure)."""

from __future__ import annotations

from hudoc_py.text.judges import (
    _FOLD_INDEX,
    _FOLD_LOOSE,
    AD_HOC_JUDGES,
    COUNTRY_REGION,
    JUDGE_COUNTRY,
    JUDGE_YEARS,
    is_ad_hoc_judge,
    judge_country,
    judge_region,
    judge_years,
    normalise_judge_name,
)


def test_roster_sizes():
    # 168 curated elected + 20 ad hoc entries.
    assert len(JUDGE_COUNTRY) == 168
    assert len(JUDGE_YEARS) == 168
    assert len(AD_HOC_JUDGES) == 20


def test_roster_internally_consistent():
    # Every judge with a country has tenure years and a mapped region.
    assert set(JUDGE_COUNTRY) == set(JUDGE_YEARS)
    assert set(JUDGE_COUNTRY.values()) <= set(COUNTRY_REGION)
    # Ad hoc judges are not in the elected roster.
    assert not AD_HOC_JUDGES & set(JUDGE_COUNTRY)
    assert len(_FOLD_INDEX) == 188
    assert len(_FOLD_LOOSE) == 188


def test_normalise_diacritic_and_partial_variants():
    assert normalise_judge_name("ZUPANCIC") == "ZUPANČIČ"
    assert normalise_judge_name("Zupančić") == "ZUPANČIČ"
    assert normalise_judge_name("BRATZA") == "SIR NICOLAS BRATZA"
    assert normalise_judge_name("ALBUQUERQUE") == "PINTO DE ALBUQUERQUE"
    assert normalise_judge_name("POWER") == "POWER-FORDE"
    assert normalise_judge_name("LÓPEZ-GUERRA") == "LÓPEZ GUERRA"


def test_systematic_fold_recovers_verified_roster_diacritics():
    expected = {
        "VAJIC": "VAJIĆ",
        "SAJO": "SAJÓ",
        "TURMEN": "TÜRMEN",
        "KJOLBRO": "KJØLBRO",
        "VUCINIC": "VUČINIĆ",
        "BOSNJAK": "BOŠNJAK",
        "MOSE": "MØSE",
        "SIMACKOVA": "ŠIMÁČKOVÁ",
        "GRITCO": "GRIŢCO",
        "RADULETU": "RĂDULEŢU",
        "BIRSAN": "BÎRSAN",
        "JOCIENE": "JOČIENĖ",
        "LOHMUS": "LÕHMUS",
        "POLACKOVA": "POLÁČKOVÁ",
        "KARAKAS": "KARAKAŞ",
        "HUSEYNOV": "HÜSEYNOV",
        "BARDSEN": "BÅRDSEN",
        "ZUND": "ZÜND",
        "ELOSEGUI": "ELÓSEGUI",
        "NI RAIFEARTAIGH": "NÍ RAIFEARTAIGH",
        "ARNARDOTTIR": "ARNARDÓTTIR",
        "JADERBLOM": "JÄDERBLOM",
    }
    assert {name: normalise_judge_name(name) for name in expected} == expected


def test_normalise_strips_artifacts():
    assert normalise_judge_name("KELLER.......82") == "KELLER"
    assert normalise_judge_name("JUDGE COSTA") == "COSTA"
    assert normalise_judge_name("AND DE GAETANO") == "DE GAETANO"
    assert normalise_judge_name("SAJÓ JOINED BY JUDGE KŪRIS") == "SAJÓ"
    assert normalise_judge_name("NORDÉN ON THE MERITS OF THE CASE") == "NORDÉN"


def test_normalise_unicode_hyphens():
    assert normalise_judge_name("POWER‑FORDE") == "POWER-FORDE"
    assert normalise_judge_name("O’LEARY") == "O'LEARY"


def test_country_region_years_lookups():
    assert judge_country("TULKENS") == "Belgium"
    assert judge_region("TULKENS") == "Western Europe"
    assert judge_years("COSTA") == (1998, 2011)
    assert judge_country("bratza") == "United Kingdom"  # via alias, any case
    assert judge_years("SPANO") == (2013, 2022)
    assert judge_country("BINDSCHEDLER-ROBERT") == "Switzerland"
    assert judge_years("BIGI") == (1991, 1996)


def test_ad_hoc_and_unknown():
    assert is_ad_hoc_judge("REED") is True
    assert judge_country("REED") is None
    assert judge_region("REED") is None
    assert judge_country("NOT A JUDGE") is None
    assert judge_years("NOT A JUDGE") == (None, None)


def test_opinion_judges_are_canonical():
    from hudoc_py.text import split_opinions

    block = "JOINT DISSENTING OPINION OF JUDGES ZUPANCIC, BRATZA AND POWER\n\nText.\n"
    ops = split_opinions(block)
    assert ops[0].judges == ["ZUPANČIČ", "SIR NICOLAS BRATZA", "POWER-FORDE"]
    assert all(judge_country(j) for j in ops[0].judges)
