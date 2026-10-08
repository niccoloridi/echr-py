# DOCX structure survey (8 October 2026)

`survey.json` records, for 33 HUDOC documents from Lawless v. Ireland (1961) to
2026, whether the DOCX rendition carries the Registry template's named styles,
how long each rendition took to fetch, and where the advisory DOCX structure
layer agrees with the canonical HTML segmentation. Every DOCX that HUDOC served
carried the styles; one 2025 Committee judgment had no DOCX (HTTP 500) and no
text and is omitted. Each document is identified by item ID and DOCX SHA-256,
and the file names the package commit and the method. The agreement figures
compare the main sections and the separate opinions; every opinion
disagreement was inspected and is a case where the DOCX reading is right.
