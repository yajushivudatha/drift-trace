"""Data shapes shared by the parsers, the LLM steps, and the HTTP API."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from drifttrace.taxonomy import Label

# --- Parsed documents ---------------------------------------------------------------------------


class CitationMarker(BaseModel):
    marker: str
    ref_keys: list[str]
    start: int = 0
    end: int = 0


class Sentence(BaseModel):
    text: str
    start: int = 0
    end: int = 0
    citations: list[CitationMarker] = []


class Paragraph(BaseModel):
    text: str
    page: int | None = None
    sentences: list[Sentence] = []
    citations: list[CitationMarker] = []


class Section(BaseModel):
    title: str = ""
    kind: str = "other"
    paragraphs: list[Paragraph] = []


class RefEntry(BaseModel):
    key: str
    index: int
    raw_text: str | None = None
    doi: str | None = None
    title: str | None = None
    authors: list[str] = []
    year: int | None = None


class ParsedDoc(BaseModel):
    title: str | None = None
    doi: str | None = None
    abstract: str | None = None
    authors: list[str] = []
    year: int | None = None
    sections: list[Section] = []
    references: list[RefEntry] = []
    source: Literal["tei", "jats", "plain"] = "tei"
    quality: Literal["structured", "degraded"] = "structured"

    def full_text(self) -> str:
        parts: list[str] = []
        if self.abstract:
            parts.append(self.abstract)
        for section in self.sections:
            if section.title:
                parts.append(section.title)
            parts.extend(p.text for p in section.paragraphs)
        return "\n\n".join(parts)


# --- LLM outputs --------------------------------------------------------------------------------


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClaimFrame(Strict):
    subject: str
    intervention_or_exposure: str
    outcome: str
    relation: str
    causal_strength: Literal["causal", "associative", "descriptive", "speculative"]
    population: str
    conditions: list[str]
    hedges: list[str]
    magnitude: str
    study_type_implied: str


class RerankResult(Strict):
    passage_ids: list[int]


Match = Literal["yes", "partial", "no"]


class FieldRow(Strict):
    claimed: str
    source: str
    match: Match


class FieldComparison(Strict):
    subject: FieldRow
    intervention_or_exposure: FieldRow
    outcome: FieldRow
    population: FieldRow
    conditions: FieldRow
    causal_strength: FieldRow
    hedging: FieldRow
    magnitude: FieldRow
    study_type: FieldRow


class Highlights(Strict):
    """Exact substrings of the two quotes, used to mark up the side-by-side diff."""

    source_hedges_removed: list[str]
    source_conditions_dropped: list[str]
    claimed_causal_terms: list[str]
    source_causal_terms: list[str]


class Judgment(Strict):
    primary_label: Label
    secondary_labels: list[Label]
    confidence: Literal["high", "medium", "low"]
    confidence_reason: str
    explanation: str
    claimed_quote: str
    source_quotes: list[str]
    field_comparison: FieldComparison
    highlights: Highlights


# --- API ----------------------------------------------------------------------------------------


class ResolveIn(BaseModel):
    doi: str | None = None
    url: str | None = None


class TraceSettings(BaseModel):
    max_depth: int = Field(default=4, ge=1, le=8)
    max_branches: int = Field(default=3, ge=1, le=10)
    mode: Literal["fast", "thorough"] = "fast"


class TraceIn(BaseModel):
    input_type: Literal["doi", "upload", "free_text"]
    claim: str = Field(min_length=8, max_length=4000)
    doi: str | None = None
    paper_id: str | None = None
    settings: TraceSettings = TraceSettings()


class FeedbackIn(BaseModel):
    agrees: bool
    user_label: Label | None = None
    note: str | None = Field(default=None, max_length=4000)


class GoldIn(BaseModel):
    gold_label: Label
    note: str | None = Field(default=None, max_length=4000)
