"""Steps 4 and 6: claim extraction, the LLM judge, and the rules that keep it honest."""

import json
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from drifttrace.config import get_settings
from drifttrace.db.models import Paper
from drifttrace.llm import LLMRefusal, complete_json
from drifttrace.llm.prompts import load_prompt
from drifttrace.pipeline.locate import ClaimContext
from drifttrace.pipeline.quotes import failed_quotes, squash
from drifttrace.pipeline.retrieve import Passage, format_passages, rerank, retrieve
from drifttrace.schemas import ClaimFrame, Judgment
from drifttrace.taxonomy import Label

NON_TEXT_WORK_TYPES = {"book", "book-chapter", "monograph", "dissertation", "dataset", "website", "reference-book"}
_LOWER_CONFIDENCE = {"high": "medium", "medium": "low", "low": "low"}


@dataclass
class Verdict:
    primary_label: str
    secondary_labels: list[str] = field(default_factory=list)
    confidence: str = "low"
    confidence_reason: str = ""
    explanation: str = ""
    claimed_quote: str = ""
    source_quotes: list[dict] = field(default_factory=list)
    field_comparison: dict = field(default_factory=dict)
    highlights: dict = field(default_factory=dict)
    quotes_verified: bool = False
    source_status: str = "NONE"
    passages: list[Passage] = field(default_factory=list)
    source_frame: dict | None = None
    retrieval_score: float | None = None
    prompt_version: str | None = None
    first_pass_quote_failure: bool = False
    quote_failure: bool = False


def unverifiable(reason: str, sentence: str, status: str, explanation: str | None = None) -> Verdict:
    return Verdict(
        primary_label=Label.UNVERIFIABLE,
        confidence="high",
        confidence_reason=reason,
        explanation=explanation or f"Could not verify: {reason[0].lower()}{reason[1:]}",
        claimed_quote=sentence,
        source_status=status,
    )


async def extract_claim(context: ClaimContext) -> ClaimFrame:
    return await complete_json(
        load_prompt("claim_extract"), ClaimFrame, "extract",
        before=context.before or "(none)", sentence=context.sentence, after=context.after or "(none)",
    )


async def extract_source_frame(claim: str, passages: list[Passage]) -> ClaimFrame:
    return await complete_json(
        load_prompt("source_frame"), ClaimFrame, "extract", claim=claim, passages=format_passages(passages)
    )


STATUS_DESCRIPTION = {
    "FULL": "Full text. These are the passages most relevant to the claim.",
    "ABSTRACT_ONLY": "Abstract only. The body of the paper could not be read.",
}


def _keep_in_quote(values: list[str], quotes: list[str]) -> list[str]:
    forms = [squash(q) for q in quotes]
    return [v for v in values if v.strip() and any(squash(v) in form for form in forms)]


def apply_principles(judgment: Judgment, status: str, degraded: bool) -> Judgment:
    """Enforce in code what must not depend on a model following instructions."""
    primary = judgment.primary_label
    secondary = [s for s in judgment.secondary_labels if s not in (Label.FAITHFUL, Label.UNVERIFIABLE, primary)]
    confidence = judgment.confidence
    reason = judgment.confidence_reason
    explanation = judgment.explanation

    if status == "ABSTRACT_ONLY":
        # Principle 3: absence from an abstract says nothing about the paper.
        if primary == Label.UNSUPPORTED_ATTRIBUTION:
            primary = Label.UNVERIFIABLE
            secondary = []
            explanation = (
                "Could not verify: full text unavailable. The abstract does not state this claim, "
                "but the body of the paper may."
            )
            reason = "Only the abstract was available."
        if confidence == "high" and primary != Label.UNVERIFIABLE:
            confidence = "medium"
            reason = f"{reason} Only the abstract was available."
    if degraded and primary != Label.UNVERIFIABLE:
        confidence = _LOWER_CONFIDENCE[confidence]
        reason = f"{reason} The PDF could only be read as plain text."

    return judgment.model_copy(
        update={
            "primary_label": primary, "secondary_labels": secondary, "confidence": confidence,
            "confidence_reason": reason, "explanation": explanation,
        }
    )


def _quote_location(quote: str, passages: list[Passage]) -> dict:
    form = squash(quote)
    for passage in passages:
        if form in squash(passage.text):
            return {"section": passage.section, "page": passage.page}
    return {"section": None, "page": None}


async def _judge_once(
    context: ClaimContext, claim_frame: ClaimFrame, source_frame: ClaimFrame, title: str, status: str,
    passages: list[Passage], extra: str = "",
) -> Judgment:
    return await complete_json(
        load_prompt("judge"), Judgment, "judge", extra_user=extra,
        before=context.before or "(none)", sentence=context.sentence, after=context.after or "(none)",
        claim_frame=claim_frame.model_dump_json(indent=1), source_title=title or "Untitled",
        source_status=STATUS_DESCRIPTION.get(status, status), passages=format_passages(passages),
        source_frame=source_frame.model_dump_json(indent=1),
    )


def _quote_problems(judgment: Judgment, context: ClaimContext, source_text: str) -> list[str]:
    problems: list[str] = []
    if not judgment.source_quotes and judgment.primary_label != Label.UNVERIFIABLE:
        problems.append("source_quotes is empty, but every label needs at least one source quote.")
    for quote in failed_quotes([judgment.claimed_quote], context.verification_text):
        problems.append(f"claimed_quote is not in the target sentence: {json.dumps(quote)}")
    for quote in failed_quotes(judgment.source_quotes, source_text):
        problems.append(f"This source quote is not in the passages: {json.dumps(quote)}")
    return problems


async def judge_passages(
    context: ClaimContext, claim_frame: ClaimFrame, title: str, status: str, passages: list[Passage],
    source_text: str, degraded: bool = False,
) -> Verdict:
    """Run the judge, verify every quote, retry once, and give up rather than show unverified text."""
    prompt_version = load_prompt("judge").tag
    try:
        source_frame = await extract_source_frame(context.sentence, passages)
        judgment = await _judge_once(context, claim_frame, source_frame, title, status, passages)
        problems = _quote_problems(judgment, context, source_text)
        first_pass_failed = bool(problems)
        if problems:
            correction = (
                "A previous attempt failed the exact-quote check:\n- " + "\n- ".join(problems)
                + "\nCopy each quote character for character from the text above."
            )
            judgment = await _judge_once(context, claim_frame, source_frame, title, status, passages, correction)
            problems = _quote_problems(judgment, context, source_text)
    except LLMRefusal as exc:
        return unverifiable(f"{exc} This hop was not assessed.", context.sentence, status)

    if problems:
        verdict = unverifiable(
            "The judge's quotes could not be matched to the stored paper text, so its verdict was discarded.",
            context.sentence, status,
        )
        verdict.passages = passages
        verdict.prompt_version = prompt_version
        verdict.first_pass_quote_failure = True
        verdict.quote_failure = True
        return verdict

    final = apply_principles(judgment, status, degraded)
    confidence, reason = final.confidence, final.confidence_reason

    if get_settings().judge_double_check:
        second = await _judge_once(context, claim_frame, source_frame, title, status, list(reversed(passages)))
        second = apply_principles(second, status, degraded)
        if second.primary_label != final.primary_label:
            confidence = "low"
            reason = (
                f"{reason} A second reading with the passages in a different order chose "
                f"{second.primary_label.value}."
            )

    is_unverifiable = final.primary_label == Label.UNVERIFIABLE
    quotes = [] if is_unverifiable else final.source_quotes
    highlights = final.highlights
    return Verdict(
        primary_label=final.primary_label.value,
        secondary_labels=[s.value for s in final.secondary_labels],
        confidence=confidence,
        confidence_reason=reason,
        explanation=final.explanation,
        claimed_quote=final.claimed_quote,
        source_quotes=[{"text": q, **_quote_location(q, passages)} for q in quotes],
        field_comparison=final.field_comparison.model_dump() if not is_unverifiable else {},
        highlights={
            "source_hedges_removed": _keep_in_quote(highlights.source_hedges_removed, quotes),
            "source_conditions_dropped": _keep_in_quote(highlights.source_conditions_dropped, quotes),
            "claimed_causal_terms": _keep_in_quote(highlights.claimed_causal_terms, [final.claimed_quote]),
            "source_causal_terms": _keep_in_quote(highlights.source_causal_terms, quotes),
        },
        quotes_verified=True,
        source_status=status,
        passages=passages,
        source_frame=source_frame.model_dump(),
        prompt_version=prompt_version,
        first_pass_quote_failure=first_pass_failed,
    )


def apply_retraction(verdict: Verdict, paper: Paper) -> Verdict:
    note = paper.retraction_note or "The source is flagged as retracted in scholarly metadata."
    previous = verdict.primary_label
    if previous not in (Label.FAITHFUL, Label.UNVERIFIABLE, Label.RETRACTED_SOURCE) and previous not in verdict.secondary_labels:
        verdict.secondary_labels = [previous, *verdict.secondary_labels]
    verdict.primary_label = Label.RETRACTED_SOURCE.value
    verdict.confidence = "high"
    verdict.confidence_reason = "Retraction status comes from publisher metadata."
    verdict.explanation = f"This source has been retracted or flagged. {note} {verdict.explanation}".strip()
    return verdict


async def run_hop(session: AsyncSession, context: ClaimContext, claim_frame: ClaimFrame, paper: Paper) -> Verdict:
    """Compare one citing claim with one cited paper."""
    status = paper.fulltext_status or "NONE"

    def finish(verdict: Verdict) -> Verdict:
        return apply_retraction(verdict, paper) if paper.is_retracted else verdict

    if paper.language and paper.language != "en":
        return finish(unverifiable(f"The source is in an unsupported language ({paper.language}).", context.sentence, status))
    if (paper.work_type or "") in NON_TEXT_WORK_TYPES and status != "FULL":
        return finish(unverifiable(
            f"The source is a {paper.work_type.replace('-', ' ')} and its text was not supplied.", context.sentence, status
        ))
    if status == "NONE" or not paper.fulltext:
        return finish(unverifiable("Full text unavailable, and no abstract could be read.", context.sentence, "NONE"))

    candidates = await retrieve(session, paper.id, context.sentence)
    if not candidates:
        return finish(unverifiable("The source's text could not be searched.", context.sentence, status))
    passages = await rerank(context.sentence, candidates)

    verdict = await judge_passages(
        context, claim_frame, paper.title or "", status, passages, paper.fulltext,
        degraded=paper.parse_quality == "degraded",
    )
    verdict.retrieval_score = passages[0].score if passages else None
    return finish(verdict)
