"""A sample trace built from invented papers, so the interface can be explored without an API key.

Everything here is fictional. The DOIs use Crossref's reserved test prefix, each record is flagged
`is_sample`, and the interface labels the trace as sample data wherever it appears.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select

from drifttrace.db.models import Hop, Paper, Trace, TraceEvent, TraceNode
from drifttrace.db.session import session_scope
from drifttrace.pipeline.trace import summarize, to_scored
from drifttrace.ingest.text import build_paragraph
from drifttrace.schemas import ClaimFrame, ParsedDoc, RefEntry, Section
from drifttrace.taxonomy import fidelity_score, worst_path

SAMPLE_CLAIM = "Compound X treats inflammatory disease in humans [12]."


def _doc(sections: list[tuple[str, str, list[str]]]) -> ParsedDoc:
    return ParsedDoc(
        sections=[
            Section(title=title, kind=kind, paragraphs=[build_paragraph(t, []) for t in paragraphs])
            for title, kind, paragraphs in sections
        ],
        references=[RefEntry(key="b0", index=0, title="Sample reference")],
    )


def _row(claimed: str, source: str, match: str) -> dict:
    return {"claimed": claimed, "source": source, "match": match}


PAPERS = {
    "citing": dict(
        title="Emerging small-molecule therapies for chronic inflammation",
        authors=["Ada Okafor", "Luis Brandt", "Mei Tanaka"], year=2024, venue="Sample Journal of Therapeutics",
        abstract="We survey small molecules proposed for chronic inflammatory disease.",
        sections=[("Introduction", "introduction", [
            "Chronic inflammation remains difficult to manage.",
            "Compound X treats inflammatory disease in humans [12].",
        ])],
    ),
    "middle": dict(
        title="Compound X and the inflammatory cascade: a narrative review",
        authors=["Sofia Lindqvist", "Tomas Reyes"], year=2021, venue="Sample Reviews in Pharmacology", work_type="review",
        abstract="This review summarizes preclinical work on compound X.",
        sections=[("Preclinical evidence", "results", [
            "Compound X reduces inflammation [7].",
            "Mechanistic studies remain limited and clinical data are absent.",
        ])],
    ),
    "origin": dict(
        title="Attenuation of adipose inflammation by compound X in diet-induced obese mice",
        authors=["Giulia Marchetti", "Hana Novak", "Peter Adeyemi"], year=2018, venue="Sample Experimental Biology",
        abstract="Compound X was tested in a mouse model of diet-induced obesity.",
        sections=[("Results", "results", [
            "Compound X reduced serum IL-6 by 12% in diet-induced obese mice at 500 mg/kg per day for 4 weeks.",
            "No significant effect was observed at 50 mg/kg.",
            "These data may suggest an anti-inflammatory action in this model.",
        ])],
    ),
    "retracted": dict(
        title="Compound X lowers C-reactive protein in a small human cohort",
        authors=["Rami Haddad", "Elise Fournier"], year=2019, venue="Sample Clinical Letters", is_retracted=True,
        retraction_note="Retraction notice: https://doi.org/10.5555/drifttrace-sample-notice",
        abstract="Twelve adults received compound X in an uncontrolled pilot.",
        sections=[("Results", "results", [
            "C-reactive protein was lower after treatment in 12 adults, in an uncontrolled pilot study.",
        ])],
    ),
    "abstract": dict(
        title="Compound X in rheumatoid arthritis: study protocol",
        authors=["Nadia Yilmaz"], year=2022, venue="Sample Trials Protocols", abstract="A protocol for a planned trial.",
        status="ABSTRACT_ONLY", sections=[],
    ),
}


def _frames() -> tuple[dict, dict]:
    origin = ClaimFrame(
        subject="serum IL-6", intervention_or_exposure="compound X", outcome="IL-6 reduced by 12%", relation="reduced",
        causal_strength="causal", population="diet-induced obese mice", conditions=["500 mg/kg per day", "4 weeks"],
        hedges=["may suggest"], magnitude="12%", study_type_implied="animal experiment",
    ).model_dump()
    middle = ClaimFrame(
        subject="inflammation", intervention_or_exposure="compound X", outcome="reduced inflammation", relation="reduces",
        causal_strength="causal", population="", conditions=[], hedges=[], magnitude="", study_type_implied="review",
    ).model_dump()
    return origin, middle


async def seed() -> uuid.UUID:
    async with session_scope() as session:
        old = list(await session.scalars(select(Trace.id).where(Trace.is_sample.is_(True))))
        if old:
            await session.execute(delete(Trace).where(Trace.id.in_(old)))
        await session.execute(delete(Paper).where(Paper.is_sample.is_(True)))

        papers: dict[str, Paper] = {}
        for index, (key, spec) in enumerate(PAPERS.items(), start=1):
            spec = dict(spec)
            sections = spec.pop("sections")
            status = spec.pop("status", "FULL")
            parsed = _doc(sections) if sections else None
            paper = Paper(
                id=uuid.uuid4(), doi=f"10.5555/drifttrace-sample-{index}", is_sample=True, fulltext_status=status,
                fulltext_attempt="thorough", fulltext_source="sample", parsed=parsed.model_dump(mode="json") if parsed else None,
                fulltext=parsed.full_text() if parsed else spec["abstract"], parse_quality="structured" if parsed else None, **spec,
            )
            if status == "ABSTRACT_ONLY":
                paper.fulltext_note = "No open-access full text was found. Only the abstract was checked."
            papers[key] = paper
            session.add(paper)

        trace = Trace(
            id=uuid.uuid4(), input_type="doi", input_claim=SAMPLE_CLAIM, start_paper_id=papers["citing"].id, status="done",
            is_sample=True, settings={"max_depth": 4, "max_branches": 3, "mode": "fast"}, finished_at=datetime.now(timezone.utc),
        )
        session.add(trace)

        origin_frame, middle_frame = _frames()

        def node(key: str, depth: int, **kwargs) -> TraceNode:
            return TraceNode(id=uuid.uuid4(), trace_id=trace.id, paper_id=papers[key].id, label=papers[key].title, depth=depth, **kwargs)

        root = node("citing", 0, claim_text=SAMPLE_CLAIM)
        middle = node("middle", 1, claim_text="Compound X reduces inflammation [7].", claim_frame=middle_frame)
        retracted = node("retracted", 1, stop_reason="origin", claim_text="C-reactive protein was lower after treatment in 12 adults")
        origin = node(
            "origin", 2, is_origin=True, stop_reason="origin", claim_frame=origin_frame,
            claim_text="Compound X reduced serum IL-6 by 12% in diet-induced obese mice at 500 mg/kg per day for 4 weeks.",
        )
        abstract = node("abstract", 2, stop_reason="no_fulltext")
        session.add_all([root, middle, retracted, origin, abstract])
        await session.flush()

        def hop(a: TraceNode, b: TraceNode, **kwargs) -> Hop:
            return Hop(id=uuid.uuid4(), trace_id=trace.id, from_node_id=a.id, to_node_id=b.id, prompt_version="sample", **kwargs)

        hops = [
            hop(
                root, middle, marker="[12]", claimed_quote=SAMPLE_CLAIM, claimed_location={"section": "Introduction", "page": 2},
                source_quotes=[
                    {"text": "Compound X reduces inflammation [7].", "section": "Preclinical evidence", "page": 4},
                    {"text": "Mechanistic studies remain limited and clinical data are absent.", "section": "Preclinical evidence", "page": 4},
                ],
                primary_label="SECONDHAND_CITATION", secondary_labels=["SCOPE_GENERALIZATION", "CAUSAL_INFLATION"], confidence="high",
                confidence_reason="The review says outright that clinical data are absent.",
                explanation="The claim says compound X treats disease in humans. The review says only that it reduces inflammation, cites another paper for that, and notes that clinical data are absent. The word “humans” and the idea of treatment both appear here for the first time.",
                field_comparison={
                    "subject": _row("inflammatory disease", "inflammation", "partial"),
                    "intervention_or_exposure": _row("compound X", "compound X", "yes"),
                    "outcome": _row("disease treated", "inflammation reduced", "no"),
                    "population": _row("humans", "not stated; clinical data absent", "no"),
                    "conditions": _row("", "", "yes"),
                    "causal_strength": _row("causal (treats)", "causal (reduces)", "partial"),
                    "hedging": _row("", "", "yes"),
                    "magnitude": _row("", "", "yes"),
                    "study_type": _row("implied clinical", "narrative review", "no"),
                },
                highlights={"source_hedges_removed": [], "source_conditions_dropped": ["clinical data are absent"], "claimed_causal_terms": ["treats"], "source_causal_terms": ["reduces"]},
                quotes_verified=True, source_status="FULL", retrieval_score=0.05,
            ),
            hop(
                root, retracted, marker="[12]", claimed_quote=SAMPLE_CLAIM, claimed_location={"section": "Introduction", "page": 2},
                source_quotes=[{"text": "C-reactive protein was lower after treatment in 12 adults, in an uncontrolled pilot study.", "section": "Results", "page": 3}],
                primary_label="RETRACTED_SOURCE", secondary_labels=["MAGNITUDE_DISTORTION"], confidence="high",
                confidence_reason="Retraction status comes from publisher metadata.",
                explanation="This source has been retracted. Even before retraction, it reported a lower marker in 12 adults in an uncontrolled pilot, which does not establish treatment of disease.",
                field_comparison={
                    "subject": _row("inflammatory disease", "C-reactive protein", "partial"),
                    "intervention_or_exposure": _row("compound X", "compound X", "yes"),
                    "outcome": _row("disease treated", "lower C-reactive protein", "no"),
                    "population": _row("humans", "12 adults", "partial"),
                    "conditions": _row("", "uncontrolled pilot", "no"),
                    "causal_strength": _row("causal (treats)", "descriptive (was lower)", "no"),
                    "hedging": _row("", "", "yes"),
                    "magnitude": _row("", "12 adults", "partial"),
                    "study_type": _row("implied clinical", "uncontrolled pilot", "partial"),
                },
                highlights={"source_hedges_removed": [], "source_conditions_dropped": ["in an uncontrolled pilot study"], "claimed_causal_terms": ["treats"], "source_causal_terms": ["was lower"]},
                quotes_verified=True, source_status="FULL", retrieval_score=0.04,
            ),
            hop(
                middle, origin, marker="[7]", claimed_quote="Compound X reduces inflammation [7].", claimed_location={"section": "Preclinical evidence", "page": 4},
                source_quotes=[
                    {"text": "Compound X reduced serum IL-6 by 12% in diet-induced obese mice at 500 mg/kg per day for 4 weeks.", "section": "Results", "page": 5},
                    {"text": "These data may suggest an anti-inflammatory action in this model.", "section": "Results", "page": 5},
                ],
                primary_label="CONDITION_DROPPED", secondary_labels=["SCOPE_GENERALIZATION", "HEDGE_LOSS"], confidence="high",
                confidence_reason="The dose, species and hedge are all stated in the source sentence.",
                explanation="The review states a general effect. The source measured one marker in obese mice at a high dose for four weeks, and describes the result as something the data may suggest.",
                field_comparison={
                    "subject": _row("inflammation", "serum IL-6", "partial"),
                    "intervention_or_exposure": _row("compound X", "compound X", "yes"),
                    "outcome": _row("reduced inflammation", "IL-6 reduced by 12%", "partial"),
                    "population": _row("", "diet-induced obese mice", "no"),
                    "conditions": _row("", "500 mg/kg per day for 4 weeks", "no"),
                    "causal_strength": _row("causal (reduces)", "causal in this model (reduced)", "yes"),
                    "hedging": _row("", "may suggest", "no"),
                    "magnitude": _row("", "12%", "no"),
                    "study_type": _row("", "animal experiment", "partial"),
                },
                highlights={"source_hedges_removed": ["may suggest"], "source_conditions_dropped": ["in diet-induced obese mice", "at 500 mg/kg per day for 4 weeks"], "claimed_causal_terms": ["reduces"], "source_causal_terms": ["reduced"]},
                quotes_verified=True, source_status="FULL", retrieval_score=0.06,
            ),
            hop(
                middle, abstract, marker="[7]", claimed_quote="Compound X reduces inflammation [7].", claimed_location={"section": "Preclinical evidence", "page": 4},
                source_quotes=[], primary_label="UNVERIFIABLE", secondary_labels=[], confidence="high",
                confidence_reason="Only the abstract was available.",
                explanation="Could not verify: full text unavailable. The abstract does not state this claim, but the body of the paper may.",
                field_comparison={}, highlights={}, quotes_verified=False, source_status="ABSTRACT_ONLY",
            ),
        ]
        session.add_all(hops)
        await session.flush()

        scored = to_scored(hops)
        trace.fidelity_score = fidelity_score(scored, root.id)
        trace.summary = summarize(hops, worst_path(scored, root.id), trace.fidelity_score)
        for kind, message, data in [
            ("status", "Trace started", {"status": "running"}),
            ("log", "Found 2 citation(s) in the claim sentence", {}),
            ("hop", "Verdict: secondhand citation (high confidence)", {}),
            ("status", "Trace complete", {"status": "done"}),
        ]:
            session.add(TraceEvent(trace_id=trace.id, kind=kind, message=message, data=data))
        await session.commit()
        return trace.id
