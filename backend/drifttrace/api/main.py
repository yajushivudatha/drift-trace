"""HTTP API (spec section 10)."""

import asyncio
import json
import secrets
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from sqlalchemy import and_, delete, exists, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from drifttrace.api.serialize import hop_json, paper_detail, paper_summary, trace_json
from drifttrace.config import get_settings
from drifttrace.db.models import Feedback, Hop, LLMCall, Paper, Trace, TraceEvent, TraceNode
from drifttrace.db.session import get_session, session_scope
from drifttrace.export.report import report_markdown, report_pdf
from drifttrace.external.http import MAX_PDF_BYTES, Polite
from drifttrace.external.scholarly import normalize_doi
from drifttrace.ingest.grobid import grobid_alive
from drifttrace.ingest.service import PaperNotFound, ensure_fulltext, ingest_upload, resolve_paper, sentences_of
from drifttrace.schemas import FeedbackIn, GoldIn, ResolveIn, TraceIn

TERMINAL = {"done", "failed", "cancelled"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.queue = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    yield
    await app.state.queue.aclose()


app = FastAPI(title="DriftTrace API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)
Db = Depends(get_session)


def parse_id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(404, "Not found") from exc


async def get_paper(session: AsyncSession, paper_id: str) -> Paper:
    paper = await session.get(Paper, parse_id(paper_id))
    if paper is None:
        raise HTTPException(404, "Paper not found")
    return paper


@app.get("/health")
async def health(request: Request, session: AsyncSession = Db) -> dict:
    settings = get_settings()
    database = True
    try:
        await session.execute(text("select 1"))
    except Exception:  # noqa: BLE001 - health reports failure, it does not raise
        database = False
    try:
        redis_ok = bool(await request.app.state.queue.ping())
    except Exception:  # noqa: BLE001
        redis_ok = False
    return {
        "status": "ok" if database and redis_ok else "degraded",
        "database": database,
        "redis": redis_ok,
        "grobid": await grobid_alive(),
        "llm_configured": settings.llm_configured,
        "contact_email_set": bool(settings.contact_email),
    }


@app.post("/papers/resolve")
async def papers_resolve(body: ResolveIn, session: AsyncSession = Db) -> dict:
    doi = normalize_doi(body.doi or body.url or "")
    if not doi:
        raise HTTPException(422, "That does not look like a DOI or a paper URL.")
    async with Polite() as http:
        try:
            paper = await resolve_paper(session, http, doi)
        except PaperNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
    return paper_detail(paper)


@app.post("/papers/upload")
async def papers_upload(file: UploadFile = File(...), session: AsyncSession = Db) -> dict:
    pdf = await file.read(MAX_PDF_BYTES + 1)
    if len(pdf) > MAX_PDF_BYTES:
        raise HTTPException(413, "That PDF is larger than the 60 MB limit.")
    if not pdf.startswith(b"%PDF"):
        raise HTTPException(422, "That file is not a PDF.")
    async with Polite() as http:
        try:
            paper = await ingest_upload(session, http, pdf, file.filename or "Uploaded PDF")
        except ValueError as exc:
            raise HTTPException(422, f"The PDF could not be read. {exc}") from exc
    return paper_detail(paper)


@app.get("/papers/{paper_id}")
async def papers_get(paper_id: str, session: AsyncSession = Db) -> dict:
    return paper_detail(await get_paper(session, paper_id))


@app.get("/papers/{paper_id}/sentences")
async def papers_sentences(
    paper_id: str, with_citations: bool = Query(default=False), session: AsyncSession = Db
) -> dict:
    paper = await get_paper(session, paper_id)
    async with Polite() as http:
        await ensure_fulltext(session, http, paper, "fast")
    sentences = sentences_of(paper.parsed)
    if with_citations:
        sentences = [s for s in sentences if s["citations"]]
    return {"paper": paper_detail(paper), "sentences": sentences}


@app.get("/papers/{paper_id}/pdf")
async def papers_pdf(paper_id: str, session: AsyncSession = Db) -> FileResponse:
    paper = await get_paper(session, paper_id)
    if not paper.pdf_path or not Path(paper.pdf_path).exists():
        raise HTTPException(404, "No PDF is stored for this paper.")
    return FileResponse(paper.pdf_path, media_type="application/pdf")


@app.post("/traces", status_code=201)
async def traces_create(body: TraceIn, request: Request, session: AsyncSession = Db) -> dict:
    if not get_settings().llm_configured:
        raise HTTPException(409, "Add ANTHROPIC_API_KEY and LLM_MODEL to .env, then restart, to run traces.")
    settings = body.settings.model_dump()
    start_paper_id = None
    if body.input_type in ("doi", "free_text"):
        doi = normalize_doi(body.doi or "")
        if not doi:
            raise HTTPException(422, "Enter a valid DOI or paper URL.")
        settings["doi"] = doi
    else:
        paper = await get_paper(session, body.paper_id or "")
        start_paper_id = paper.id

    trace = Trace(input_type=body.input_type, input_claim=body.claim.strip(), settings=settings, start_paper_id=start_paper_id)
    session.add(trace)
    await session.commit()
    await request.app.state.queue.enqueue_job("trace_job", str(trace.id))
    return {"id": str(trace.id)}


@app.get("/traces/{trace_id}/events")
async def traces_events(trace_id: str, request: Request) -> StreamingResponse:
    """Server-sent events. Replays history, so reconnecting never loses progress."""
    identifier = parse_id(trace_id)
    start_after = int(request.headers.get("last-event-id") or 0)

    async def stream():
        last = start_after
        idle = 0
        while True:
            if await request.is_disconnected():
                return
            async with session_scope() as session:
                events = list(
                    await session.scalars(
                        select(TraceEvent).where(TraceEvent.trace_id == identifier, TraceEvent.id > last).order_by(TraceEvent.id).limit(200)
                    )
                )
                status = await session.scalar(select(Trace.status).where(Trace.id == identifier))
            for event in events:
                last = event.id
                payload = {
                    "id": event.id, "kind": event.kind, "message": event.message, "data": event.data,
                    "at": event.created_at.isoformat(),
                }
                yield f"id: {event.id}\ndata: {json.dumps(payload)}\n\n"
            if status is None or (status in TERMINAL and not events):
                yield "event: end\ndata: {}\n\n"
                return
            idle = 0 if events else idle + 1
            if idle and idle % 40 == 0:
                yield ": keep-alive\n\n"
            await asyncio.sleep(0.35)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/traces/{trace_id}")
async def traces_get(trace_id: str, session: AsyncSession = Db) -> dict:
    data = await trace_json(session, parse_id(trace_id))
    if data is None:
        raise HTTPException(404, "Trace not found")
    return data


@app.post("/traces/{trace_id}/cancel")
async def traces_cancel(trace_id: str, session: AsyncSession = Db) -> dict:
    trace = await session.get(Trace, parse_id(trace_id))
    if trace is None:
        raise HTTPException(404, "Trace not found")
    if trace.status == "queued":
        trace.status = "cancelled"
        session.add(TraceEvent(trace_id=trace.id, kind="status", message="Trace cancelled", data={"status": "cancelled"}))
    elif trace.status == "running":
        trace.status = "cancelling"
    await session.commit()
    return {"status": trace.status}


@app.delete("/traces/{trace_id}", status_code=204)
async def traces_delete(trace_id: str, session: AsyncSession = Db) -> Response:
    await session.execute(delete(Trace).where(Trace.id == parse_id(trace_id)))
    await session.commit()
    return Response(status_code=204)


@app.get("/traces")
async def traces_list(
    q: str | None = None, label: str | None = None, sort: str = "date", order: str = "desc",
    page: int = Query(default=1, ge=1), per_page: int = Query(default=20, ge=1, le=100), session: AsyncSession = Db,
) -> dict:
    filters = []
    if q:
        filters.append(Trace.input_claim.ilike(f"%{q}%"))
    if label:
        filters.append(exists().where(and_(Hop.trace_id == Trace.id, Hop.primary_label == label)))
    column = Trace.fidelity_score if sort == "score" else Trace.created_at
    ordering = column.asc().nulls_last() if order == "asc" else column.desc().nulls_last()
    total = await session.scalar(select(func.count(Trace.id)).where(*filters))
    traces = list(
        await session.scalars(select(Trace).where(*filters).order_by(ordering).offset((page - 1) * per_page).limit(per_page))
    )
    ids = [t.id for t in traces]
    rows = await session.execute(
        select(Hop.trace_id, Hop.primary_label, func.count(Hop.id)).where(Hop.trace_id.in_(ids)).group_by(Hop.trace_id, Hop.primary_label)
    )
    counts: dict[uuid.UUID, dict[str, int]] = {}
    for trace_id, primary, count in rows:
        counts.setdefault(trace_id, {})[primary] = count
    papers = {p.id: p for p in await session.scalars(select(Paper).where(Paper.id.in_([t.start_paper_id for t in traces if t.start_paper_id])))}
    return {
        "total": total, "page": page, "per_page": per_page,
        "items": [
            {
                "id": str(t.id), "claim": t.input_claim, "status": t.status, "fidelity_score": t.fidelity_score,
                "created_at": t.created_at.isoformat(), "label_counts": counts.get(t.id, {}), "is_sample": t.is_sample,
                "paper": paper_summary(papers.get(t.start_paper_id)),
            }
            for t in traces
        ],
    }


@app.post("/hops/{hop_id}/feedback")
async def hops_feedback(hop_id: str, body: FeedbackIn, session: AsyncSession = Db) -> dict:
    hop = await session.get(Hop, parse_id(hop_id))
    if hop is None:
        raise HTTPException(404, "Hop not found")
    if not body.agrees and body.user_label is None:
        raise HTTPException(422, "Choose the correct label.")
    label = hop.primary_label if body.agrees else body.user_label.value
    session.add(Feedback(hop_id=hop.id, agrees=body.agrees, user_label=label, note=body.note))
    await session.commit()
    return {"agrees": body.agrees, "user_label": label, "note": body.note}


@app.get("/review/queue")
async def review_queue(include_done: bool = False, session: AsyncSession = Db) -> dict:
    disagreed = exists().where(and_(Feedback.hop_id == Hop.id, Feedback.agrees.is_(False)))
    query = select(Hop).where(or_(disagreed, Hop.confidence == "low")).options(selectinload(Hop.feedback))
    if not include_done:
        query = query.where(Hop.gold_label.is_(None))
    hops = list(await session.scalars(query.limit(200)))
    node_ids = {h.to_node_id for h in hops} | {h.from_node_id for h in hops}
    nodes = {
        n.id: n
        for n in await session.scalars(select(TraceNode).where(TraceNode.id.in_(node_ids)).options(selectinload(TraceNode.paper)))
    }
    items = []
    for hop in hops:
        items.append(
            {
                **hop_json(hop, hop.feedback), "trace_id": str(hop.trace_id), "gold_note": hop.gold_note,
                "citing": paper_summary(nodes[hop.from_node_id].paper), "cited": paper_summary(nodes[hop.to_node_id].paper),
                "cited_label": nodes[hop.to_node_id].label,
            }
        )
    return {"items": items}


@app.post("/hops/{hop_id}/gold")
async def hops_gold(hop_id: str, body: GoldIn, session: AsyncSession = Db) -> dict:
    hop = await session.get(Hop, parse_id(hop_id))
    if hop is None:
        raise HTTPException(404, "Hop not found")
    hop.gold_label = body.gold_label.value
    hop.gold_note = body.note
    await session.commit()
    return {"gold_label": hop.gold_label, "gold_note": hop.gold_note}


@app.get("/review/export.jsonl", response_class=PlainTextResponse)
async def review_export(session: AsyncSession = Db) -> str:
    """Reviewer gold labels, in the format of eval/gold.jsonl."""
    hops = list(await session.scalars(select(Hop).where(Hop.gold_label.is_not(None))))
    lines = []
    for hop in hops:
        to_node = await session.get(TraceNode, hop.to_node_id, options=[selectinload(TraceNode.paper)])
        lines.append(
            json.dumps(
                {
                    "id": f"review-{hop.id}", "citing_sentence": hop.claimed_quote,
                    "cited_doi": to_node.paper.doi if to_node and to_node.paper else None,
                    "gold_label": hop.gold_label, "provenance": "reviewer", "note": hop.gold_note,
                }
            )
        )
    return "\n".join(lines) + ("\n" if lines else "")


@app.get("/traces/{trace_id}/export")
async def traces_export(trace_id: str, format: str = Query(default="md", pattern="^(md|pdf|json)$"), session: AsyncSession = Db) -> Response:
    data = await trace_json(session, parse_id(trace_id))
    if data is None:
        raise HTTPException(404, "Trace not found")
    name = f"drifttrace-{trace_id[:8]}.{format}"
    headers = {"Content-Disposition": f'attachment; filename="{name}"'}
    if format == "json":
        return JSONResponse(data, headers=headers)
    if format == "md":
        return PlainTextResponse(report_markdown(data), media_type="text/markdown", headers=headers)
    return Response(report_pdf(data), media_type="application/pdf", headers=headers)


@app.post("/traces/{trace_id}/share")
async def traces_share(trace_id: str, session: AsyncSession = Db) -> dict:
    trace = await session.get(Trace, parse_id(trace_id))
    if trace is None:
        raise HTTPException(404, "Trace not found")
    if not trace.share_token:
        trace.share_token = secrets.token_urlsafe(18)
        await session.commit()
    return {"share_token": trace.share_token}


@app.get("/shared/{token}")
async def shared_get(token: str, session: AsyncSession = Db) -> dict:
    trace_id = await session.scalar(select(Trace.id).where(Trace.share_token == token))
    if trace_id is None:
        raise HTTPException(404, "This share link is not valid.")
    data = await trace_json(session, trace_id)
    data["share_token"] = None
    for hop in data["hops"]:
        hop["feedback"] = None
    return data


@app.get("/stats/cost")
async def stats_cost(session: AsyncSession = Db) -> dict:
    by_purpose = await session.execute(
        select(
            LLMCall.purpose,
            func.count(LLMCall.id),
            func.sum(LLMCall.input_tokens),
            func.sum(LLMCall.output_tokens),
            func.sum(LLMCall.cost_usd),
            func.avg(LLMCall.latency_ms).filter(LLMCall.cache_hit.is_(False)),
            func.count(LLMCall.id).filter(LLMCall.cache_hit.is_(True)),
        ).group_by(LLMCall.purpose)
    )
    day = func.date_trunc("day", LLMCall.created_at)
    by_day = await session.execute(select(day, func.sum(LLMCall.cost_usd), func.count(LLMCall.id)).group_by(day).order_by(day))
    traces = await session.scalar(select(func.count(func.distinct(LLMCall.trace_id))))
    purposes = [
        {
            "purpose": row[0], "calls": row[1], "input_tokens": int(row[2] or 0), "output_tokens": int(row[3] or 0),
            "cost_usd": round(float(row[4] or 0), 4), "avg_latency_ms": round(float(row[5] or 0)), "cache_hits": row[6],
        }
        for row in by_purpose
    ]
    total_cost = sum(p["cost_usd"] for p in purposes)
    return {
        "total_cost_usd": round(total_cost, 4),
        "total_calls": sum(p["calls"] for p in purposes),
        "cache_hits": sum(p["cache_hits"] for p in purposes),
        "input_tokens": sum(p["input_tokens"] for p in purposes),
        "output_tokens": sum(p["output_tokens"] for p in purposes),
        "avg_cost_per_trace": round(total_cost / traces, 4) if traces else 0,
        "pricing_configured": get_settings().llm_price_input_per_mtok > 0,
        "by_purpose": purposes,
        "by_day": [{"day": row[0].date().isoformat(), "cost_usd": round(float(row[1] or 0), 4), "calls": row[2]} for row in by_day],
    }
