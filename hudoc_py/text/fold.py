"""One letter-folding rule for every identity comparison in the package.

Unicode decomposition (NFKD) strips accents that are combining marks
(``é`` -> ``e``) but leaves letters whose stroke or form is part of the
character: ``ł``, ``ø``, ``đ``, ``ð``, ``ı``, ``æ``, ``œ``, ``þ``, ``ß``.
A key built only from NFKD therefore turns "Kudła" into "kud a" while HUDOC's
own metadata spells the case "KUDLA". These letters are common in the Court's
citation lists (``ı`` alone appears some 1,700 times in HUDOC SCL fields), so
they are transliterated first, in one place, and every identity normaliser
uses the result.
"""

from __future__ import annotations

import unicodedata

#: Letters that NFKD does not decompose, mapped to the spelling HUDOC metadata
#: uses for them. Capital and small forms are both listed so the rule does not
#: depend on the order of case folding.
TRANSLITERATION = str.maketrans(
    {
        "Ł": "L", "ł": "l",
        "Ø": "O", "ø": "o",
        "Đ": "D", "đ": "d",
        "Ð": "D", "ð": "d",
        "ı": "i",
        "Æ": "AE", "æ": "ae",
        "Œ": "OE", "œ": "oe",
        "Þ": "TH", "þ": "th",
        "ß": "ss", "ẞ": "SS",
        "Ħ": "H", "ħ": "h",
        "Ŧ": "T", "ŧ": "t",
    }
)  # fmt: skip


def transliterate(value: str) -> str:
    """Replace letters that Unicode decomposition leaves intact (``ł`` -> ``l``)."""
    return value.translate(TRANSLITERATION)


def fold_letters(value: str) -> str:
    """Case-fold, transliterate and strip combining marks: "Kudła" -> "kudla"."""
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", transliterate(value).casefold())
        if not unicodedata.combining(char)
    )


__all__ = ["TRANSLITERATION", "fold_letters", "transliterate"]
