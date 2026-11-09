"""JSON shapes returned by the API and used by the exporters."""

import uuid
from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from drifttrace.db.models import Feedback, Hop, LLMCall, Paper, Trace, TraceNode
from drifttrace.pipeline.trace import short_cite, to_scored
from drifttrace.taxonomy import SCORE_FORMULA, SEVERITY, worst_path


def paper_summary(paper: Paper | None) -> dict | None:
    if paper is None:
        return None
    return {
        "id": str(paper.id), "doi": paper.doi, "title": paper.title, "authors": paper.authors or [],
        "year": paper.year, "venue": paper.venue, "short_cite": short_cite(paper),
        "fulltext_status": paper.fulltext_status, "fulltext_source": paper.fulltext_source,
        "fulltext_note": paper.fulltext_note, "is_retracted": paper.is_retracted,
        "retraction_note": paper.retraction_note, "is_oa": paper.is_oa, "work_type": paper.work_type,
        "related_versions": paper.related_versions or [], "parse_quality": paper.parse_quality,
        "has_pdf": bool(paper.pdf_path), "is_sample": paper.is_sample,
    }


def paper_detail(paper: Paper) -> dict:
    return {**paper_summary(paper), "abstract": paper.abstract}


def hop_json(hop: Hop, feedback: list[Feedback] | None = None) -> dict:
    latest = max(feedback, key=lambda f: f.created_at) if feedback else None
    return {
        "id": str(hop.id), "from": str(hop.from_node_id), "to": str(hop.to_node_id), "marker": hop.marker,
        "claimed_quote": hop.claimed_quote, "claimed_location": hop.claimed_location or {},
        "source_quotes": hop.source_quotes or [], "primary_label": hop.primary_label,
        "secondary_labels": hop.secondary_labels or [], "severity": SEVERITY.get(hop.primary_label, 0),
        "confidence": hop.confidence, "confidence_reason": hop.confidence_reason, "explanation": hop.explanation,
        "field_comparison": hop.field_comparison or {}, "highlights": hop.highlights or {},
        "quotes_verified": hop.quotes_verified, "source_status": hop.source_status,
        "is_self_citation": hop.is_self_citation, "is_cycle": hop.is_cycle, "gold_label": hop.gold_label,
        "feedback": (
            {"agrees": latest.agrees, "user_label": latest.user_label, "note": latest.note} if latest else None
        ),
    }


async def trace_json(session: AsyncSession, trace_id: uuid.UUID, include_events: bool = False) -> dict | None:
    trace = await session.get(Trace, trace_id)
    if trace is None:
        return None
    nodes = list(
        await session.scalars(
            select(TraceNode).where(TraceNode.trace_id == trace_id).options(selectinload(TraceNode.paper)).order_by(TraceNode.depth)
        )
    )
    hops = list(
        await session.scalars(select(Hop).where(Hop.trace_id == trace_id).options(selectinload(Hop.feedback)))
    )
    appearances = Counter(str(h.to_node_id) for h in hops)
    root = next((n.id for n in nodes if n.depth == 0), None)
    worst = worst_path(to_scored(hops), root) if root else []
    counts = Counter(h.primary_label for h in hops)
    calls, cost = (
        await session.execute(
            select(func.count(LLMCall.id), func.coalesce(func.sum(LLMCall.cost_usd), 0.0)).where(
                LLMCall.trace_id == trace_id, LLMCall.cache_hit.is_(False)
            )
        )
    ).one()
    start = await session.get(Paper, trace.start_paper_id) if trace.start_paper_id else None

    return {
        "id": str(trace.id), "input_type": trace.input_type, "claim": trace.input_claim,
        "status": trace.status, "error": trace.error, "settings": trace.settings or {},
        "fidelity_score": trace.fidelity_score, "summary": trace.summary, "is_sample": trace.is_sample,
        "share_token": trace.share_token, "created_at": trace.created_at.isoformat(),
        "finished_at": trace.finished_at.isoformat() if trace.finished_at else None,
        "score_formula": SCORE_FORMULA, "label_counts": counts, "start_paper": paper_summary(start),
        "llm_calls": calls, "cost_usd": round(float(cost), 4),
        "worst_path": [str(h.id) for h in worst],
        "nodes": [
            {
                "id": str(n.id), "depth": n.depth, "is_origin": n.is_origin, "stop_reason": n.stop_reason,
                "label": n.label, "claim_text": n.claim_text, "claim_frame": n.claim_frame,
                "times_cited": appearances[str(n.id)], "paper": paper_summary(n.paper),
            }
            for n in nodes
        ],
        "hops": [hop_json(h, h.feedback) for h in hops],
    }
