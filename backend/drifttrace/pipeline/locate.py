"""Step 3: find the claim sentence in a parsed paper and read off its citations."""

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from drifttrace.schemas import CitationMarker, ParsedDoc

SENTENCE_MATCH_THRESHOLD = 88


@dataclass
class ClaimContext:
    """A claim as it appears in a citing text."""

    sentence: str
    before: str = ""
    after: str = ""
    section: str | None = None
    page: int | None = None
    citations: list[CitationMarker] = field(default_factory=list)
    match_score: float = 100.0

    @property
    def verification_text(self) -> str:
        return " ".join(part for part in (self.before, self.sentence, self.after) if part)


def _plain(sentence: str, markers: list[CitationMarker]) -> str:
    """Remove citation markers, which users often leave out when pasting a sentence."""
    for marker in sorted(markers, key=lambda m: m.start, reverse=True):
        sentence = sentence[: marker.start] + sentence[marker.end :]
    return re.sub(r"\s+", " ", sentence).strip().lower()


def find_sentence(doc: ParsedDoc, claim: str) -> ClaimContext | None:
    target = re.sub(r"\s+", " ", re.sub(r"\[[\d\s,;–—-]+\]", "", claim)).strip().lower()
    best: ClaimContext | None = None
    best_score = 0.0
    for section in doc.sections:
        flat = [(p, s) for p in section.paragraphs for s in p.sentences]
        for index, (paragraph, sentence) in enumerate(flat):
            plain = _plain(sentence.text, sentence.citations)
            score = max(
                fuzz.ratio(target, plain),
                fuzz.partial_ratio(target, plain) * 0.98 if len(target) >= 30 else 0.0,
            )
            # On equal text similarity, the sentence that carries a citation is the claim.
            if score > best_score or (score == best_score and best and sentence.citations and not best.citations):
                best_score = score
                best = ClaimContext(
                    sentence=sentence.text,
                    before=flat[index - 1][1].text if index else "",
                    after=flat[index + 1][1].text if index + 1 < len(flat) else "",
                    section=section.title or None,
                    page=paragraph.page,
                    citations=sentence.citations,
                    match_score=score,
                )
    return best if best_score >= SENTENCE_MATCH_THRESHOLD else None


def unique_ref_keys(citations: list[CitationMarker]) -> list[tuple[str, str]]:
    """One (reference key, marker) per cited paper. A bundle becomes several hops."""
    seen: dict[str, str] = {}
    for citation in citations:
        for key in citation.ref_keys:
            seen.setdefault(key, citation.marker)
    return list(seen.items())
