"""Bench composition is independent of separate-opinion authors."""

from hudoc_py.text import extract_bench_composition, segment_full

ENGLISH = """In the case of Example v. State,
The European Court of Human Rights, sitting as a Chamber composed of:
Guido Raimondi, President,
Angelika Nußberger,
Ganna Yudkivska,
Paulo Pinto de Albuquerque,
Ksenija Turković,
Egidijus Kūris,
Marko Bošnjak, judges,
and also of Abel Campos, Section Registrar,
Having deliberated in private,
PROCEDURE
1. The case originated in an application.
THE FACTS
2. Facts.
THE LAW
3. Law.
FOR THESE REASONS
1. Holds unanimously.
DISSENTING OPINION OF JUDGE KŪRIS
1. I disagree.
"""


def test_extracts_deciding_bench_not_only_opinion_authors():
    sections = segment_full(ENGLISH, document_id="bench-en")

    assert sections.bench is not None
    assert sections.bench.judges == [
        "RAIMONDI",
        "NUSSBERGER",
        "YUDKIVSKA",
        "PINTO DE ALBUQUERQUE",
        "TURKOVIĆ",
        "KŪRIS",
        "BOŠNJAK",
    ]
    assert sections.bench.members[0].role == "president"
    assert sections.opinions[0].judges == ["KŪRIS"]
    assert set(sections.opinions[0].judges) < set(sections.bench.judges)


def test_french_composition_and_ad_hoc_role():
    text = """La Cour européenne des droits de l'homme, siégeant en une chambre composée de :
M. Jean-Paul Costa, président,
Mme Françoise Tulkens,
M. Stanislav Pavlovschi, juge ad hoc,
et de Mme Sally Dollé, greffière de section,
Après en avoir délibéré en chambre du conseil,
EN FAIT
1. Les faits.
EN DROIT
2. Le droit.
PAR CES MOTIFS
1. Dit.
"""
    composition = extract_bench_composition(text)

    assert composition is not None
    assert composition.judges == ["COSTA", "TULKENS", "PAVLOVSCHI"]
    assert composition.members[0].role == "president"
    assert composition.members[-1].role == "ad_hoc_judge"
    assert composition.members[-1].is_ad_hoc is True


def test_no_composition_is_explicitly_absent():
    assert extract_bench_composition("THE LAW\n1. No front matter here.") is None


def test_real_ocalan_grand_chamber_composition_keeps_every_initialled_name():
    # Public HUDOC item 001-69022. This exact front-matter shape is one source
    # paragraph containing comma-separated names, titles and single initials.
    text = """The European Court of Human Rights, sitting as a Grand Chamber composed of:
Mr L. Wildhaber, President, Mr C.L. Rozakis, Mr J.-P. Costa, Mr G. Ress,
Sir Nicolas Bratza, Mrs E. Palm, Mr L. Caflisch, Mr L. Loucaides,
Mr R. Türmen, Mrs V. Strážnická, Mr P. Lorenzen, Mr V. Butkevych,
Mr J. Hedigan, Mr M. Ugrekhelidze, Mr L. Garlicki,
Mr J. Borrego Borrego, Mrs A. Gyulumyan, judges,
and Mr P.J. Mahoney, Registrar,
Having deliberated in private,
PROCEDURE
"""

    composition = extract_bench_composition(text)

    assert composition is not None
    assert composition.language == "EN"
    assert len(composition.members) == 17
    assert composition.members[0].name == "WILDHABER"
    assert composition.members[0].role == "president"
    assert composition.members[-1].name == "GYULUMYAN"
