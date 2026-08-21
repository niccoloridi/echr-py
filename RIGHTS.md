# Rights and third-party material

The [MIT licence](LICENSE) applies to the original software in this repository.
It does not grant rights in source material obtained from HUDOC, HUDOC-EXEC, the
European Court of Human Rights, the Council of Europe, or another rights holder.

## Material shipped with the software

The package includes compact data derived from public Court research resources:

- the official English and French case-law citation authorities and transparent resolver
  supplements;
- a checksummed historical citation catalogue;
- the English HUDOC keyword thesaurus; and
- versioned schemas and the vendored D3 viewer assets.

Court-derived data retains the source's terms. D3 is redistributed under the
licence reproduced in `hudoc_py/graphs/assets/LICENSE-D3.txt`. The JSON Schema
files and original package metadata are part of the MIT-licensed software.

## Repository-only fixtures and demonstrations

Short synthetic texts under `tests/data/opinions/` test multilingual document
structure and separate-opinion boundaries without reproducing substantive
Court documents. They are not included in the wheel or source distribution.

Demonstration artifacts under `docs/` and `examples/` contain source identifiers,
short citation contexts, derived citation edges, counts, graphs, and provenance.
They do not constitute a general corpus of Court texts.

The project logo was created with OpenAI image generation. It is project
artwork, not a Court or Council of Europe mark.

For Court website text, consult the current
[ECHR copyright and disclaimer](https://www.echr.coe.int/en/copyright-and-disclaimer)
and retain the required `© ECHR-CEDH` attribution. Public-source availability
does not by itself make the source text MIT, CC0, or unrestricted open data.

## User-acquired material

Files downloaded with `echr-py` are not relicensed by this project. Users are
responsible for checking the source terms, attribution requirements, privacy
considerations, and any restrictions applicable to their intended use.
