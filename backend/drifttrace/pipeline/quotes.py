"""Quote verification (principle 2).

A quote passes only if it occurs in the stored text once PDF artifacts are removed: whitespace,
line-break hyphens, ligatures and typographic quote marks. The tolerance is deliberately narrow.
Any character that could change the meaning, digits above all, has to match.
"""

import re
import unicodedata

from rapidfuzz import fuzz

MIN_QUOTE_CHARS = 8
NEAR_MATCH_SCORE = 98.5
MIN_NEAR_MATCH_CHARS = 40

_DASHES = "-‐‑‒–—―−"
_QUOTES = {"‘": "'", "’": "'", "“": '"', "”": '"', "′": "'"}
_INNER_DASH = re.compile(rf"(?<=[^\W\d_])[{_DASHES}](?=\w)|(?<=\d)[{_DASHES}](?=[^\W\d_])")
_ANY_DASH = re.compile(rf"[{_DASHES}]")


def squash(text: str) -> str:
    """Reduce text to a form in which PDF artifacts are gone and meaningful characters remain."""
    text = unicodedata.normalize("NFKC", text).replace("­", "")
    for typographic, plain in _QUOTES.items():
        text = text.replace(typographic, plain)
    text = re.sub(r"\s+", "", text)
    text = _INNER_DASH.sub("", text)
    text = _ANY_DASH.sub("-", text)
    return text.casefold()


def _digits(text: str) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?", text)


def quote_in_text(quote: str, text: str) -> bool:
    quote_form = squash(quote)
    if len(quote_form) < MIN_QUOTE_CHARS:
        return False
    text_form = squash(text)
    if quote_form in text_form:
        return True
    if len(quote_form) < MIN_NEAR_MATCH_CHARS:
        return False
    alignment = fuzz.partial_ratio_alignment(quote_form, text_form)
    if alignment is None or alignment.score < NEAR_MATCH_SCORE:
        return False
    return _digits(quote_form) == _digits(text_form[alignment.dest_start : alignment.dest_end])


def failed_quotes(quotes: list[str], text: str) -> list[str]:
    return [quote for quote in quotes if not quote_in_text(quote, text)]
