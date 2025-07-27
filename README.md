# Tex Tech

Genuinely useful, zero dependency scripts for doing things with LaTeX documents.

## Contents

- `bibtexchex.py`: checks and strips citations and bibliographies to only
  include those citations you actually cited in your document. Can optionally
  query NASA/ADS to fetch the most up-to-date and standard formatting of the
  bibtex entries where they can be unambiguously resolved.

- `adsq.py`: a NASA/ADS Query program, basically giving you the NASA/ADS search
  window but on the command line, and without the heavy JavaScript of the
  website. It can also be used to (mass) fetch BibTeX entries from BibCodes.
