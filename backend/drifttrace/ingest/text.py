"""Sentence splitting, citation-marker bookkeeping and section classification, for every parser."""

import re

from drifttrace.schemas import CitationMarker, Paragraph, Sentence

ABBREVIATIONS = {
    "al", "e.g", "i.e", "fig", "figs", "eq", "eqs", "vs", "cf", "approx", "ca", "dr", "prof", "mr",
    "ms", "no", "nos", "ref", "refs", "tab", "sect", "sec", "suppl", "resp", "inc", "ltd", "st",
    "vol", "pp", "p", "ed", "eds", "spp", "sp", "var", "subsp", "dept", "univ", "etc",
}
_BOUNDARY = re.compile(r"[.!?][\"'”’)\]]*\s+(?=[\"'“‘(\[]?[A-Z0-9])")


def split_sentences(text: str, protected: list[tuple[int, int]] | None = None) -> list[tuple[int, int]]:
    """Return (start, end) spans. A boundary inside a protected span, such as a citation, is ignored."""
    protected = protected or []
    spans: list[tuple[int, int]] = []
    start = 0
    for match in _BOUNDARY.finditer(text):
        dot = match.start()
        if any(a <= dot < b for a, b in protected):
            continue
        word = text[max(start, dot - 12) : dot].split()[-1:] or [""]
        token = word[0].lstrip("([\"'").lower()
        if token in ABBREVIATIONS or re.fullmatch(r"[a-z]", token):
            continue
        end = match.end()
        spans.append((start, len(text[:end].rstrip())))
        start = end
    if start < len(text) and text[start:].strip():
        spans.append((start, len(text.rstrip())))
    return spans


def build_paragraph(text: str, markers: list[CitationMarker], page: int | None = None) -> Paragraph:
    markers = sorted(markers, key=lambda m: m.start)
    sentences: list[Sentence] = []
    for start, end in split_sentences(text, [(m.start, m.end) for m in markers]):
        inside = [
            m.model_copy(update={"start": m.start - start, "end": m.end - start})
            for m in markers
            if start <= m.start < end
        ]
        sentences.append(Sentence(text=text[start:end], start=start, end=end, citations=inside))
    return Paragraph(text=text, page=page, sentences=sentences, citations=markers)


def numbers_in_marker(text: str) -> list[int] | None:
    """Expand a numeric marker such as "[3, 5-7]". Returns None for author-year style markers."""
    inner = text.strip().strip("[]() ")
    if not inner or not re.fullmatch(r"[\d\s,;–—-]+", inner):
        return None
    numbers: list[int] = []
    for part in re.split(r"[,;]", inner):
        part = part.strip()
        bounds = re.split(r"\s*[–—-]\s*", part)
        if len(bounds) == 2 and bounds[0].isdigit() and bounds[1].isdigit():
            low, high = int(bounds[0]), int(bounds[1])
            if 0 < high - low <= 60:
                numbers.extend(range(low, high + 1))
            else:
                numbers.extend([low, high])
        elif part.isdigit():
            numbers.append(int(part))
    return numbers or None


def expand_bundles(text: str, markers: list[CitationMarker], ordered_keys: list[str]) -> list[CitationMarker]:
    """Expand citation ranges so that "[3–7]" yields one reference key per cited paper.

    Handles a single marker whose text spans a range and two markers joined by a dash, the two
    forms parsers emit for a bundle. Keys come from the document's reference order.
    """
    position = {key: i for i, key in enumerate(ordered_keys)}
    markers = sorted(markers, key=lambda m: m.start)

    for marker in markers:
        numbers = numbers_in_marker(marker.marker)
        if numbers and len(numbers) > 1 and len(marker.ref_keys) == 1 and marker.ref_keys[0] in position:
            first = position[marker.ref_keys[0]]
            offsets = [n - numbers[0] for n in numbers]
            keys = [ordered_keys[first + o] for o in offsets if 0 <= first + o < len(ordered_keys)]
            marker.ref_keys = list(dict.fromkeys(keys))

    merged: list[CitationMarker] = []
    for marker in markers:
        if merged:
            previous = merged[-1]
            between = text[previous.end : marker.start]
            if (
                re.fullmatch(r"\s*[–—-]\s*", between)
                and previous.ref_keys
                and marker.ref_keys
                and previous.ref_keys[-1] in position
                and marker.ref_keys[0] in position
            ):
                low, high = position[previous.ref_keys[-1]], position[marker.ref_keys[0]]
                if 0 < high - low <= 60:
                    previous.ref_keys = list(
                        dict.fromkeys([*previous.ref_keys, *ordered_keys[low : high + 1], *marker.ref_keys])
                    )
                    previous.marker = text[previous.start : marker.end]
                    previous.end = marker.end
                    continue
        merged.append(marker)
    return merged


class TextBuffer:
    """Accumulates text with collapsed whitespace, so recorded offsets match the final string."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.length = 0
        self._ends_space = True

    def add(self, value: str | None) -> None:
        if not value:
            return
        value = re.sub(r"\s+", " ", value)
        if self._ends_space:
            value = value.lstrip(" ")
        if not value:
            return
        self.parts.append(value)
        self.length += len(value)
        self._ends_space = value.endswith(" ")

    def text(self) -> str:
        return "".join(self.parts)


SECTION_KINDS = [
    ("abstract", r"abstract|summary"),
    ("introduction", r"introduction|background"),
    ("methods", r"method|materials?|experimental|procedure|study design"),
    ("results", r"results?|findings"),
    ("discussion", r"discussion"),
    ("conclusion", r"conclusion|concluding"),
    ("references", r"references|bibliography|literature cited"),
    ("acknowledgements", r"acknowledg|funding|conflict|competing|author contribution|data availab"),
]


def section_kind(title: str | None) -> str:
    lowered = (title or "").lower()
    for kind, pattern in SECTION_KINDS:
        if re.search(pattern, lowered):
            return kind
    return "other"
