"""Step 5: hybrid retrieval over a cited paper's chunks, then LLM rerank."""

import re
import uuid
from dataclasses import dataclass, field

from rank_bm25 import BM25Okapi
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from drifttrace.db.models import Chunk
from drifttrace.ingest.embeddings import embed
from drifttrace.llm import LLMRefusal, complete_json
from drifttrace.llm.prompts import load_prompt
from drifttrace.schemas import RerankResult

CANDIDATES = 20
FINAL_PASSAGES = 3
RRF_K = 60
SECTION_PRIOR = {
    "results": 1.2, "abstract": 1.15, "discussion": 1.1, "conclusion": 1.05,
    "other": 1.0, "introduction": 0.9, "methods": 0.85,
}


@dataclass
class Passage:
    id: int
    text: str
    section: str | None = None
    kind: str | None = None
    page: int | None = None
    citations: list[dict] = field(default_factory=list)
    score: float = 0.0


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def fuse(bm25_order: list[int], vector_order: list[int], kinds: list[str | None]) -> list[tuple[int, float]]:
    """Reciprocal rank fusion, weighted by how likely each section is to report a finding."""
    scores: dict[int, float] = {}
    for order in (bm25_order, vector_order):
        for rank, index in enumerate(order):
            scores[index] = scores.get(index, 0.0) + 1.0 / (RRF_K + rank + 1)
    weighted = {i: s * SECTION_PRIOR.get(kinds[i] or "other", 1.0) for i, s in scores.items()}
    return sorted(weighted.items(), key=lambda item: item[1], reverse=True)


async def retrieve(session: AsyncSession, paper_id: uuid.UUID, claim: str) -> list[Passage]:
    chunks = list(
        await session.scalars(select(Chunk).where(Chunk.paper_id == paper_id).order_by(Chunk.ordinal))
    )
    if not chunks:
        return []

    corpus = [_tokens(c.text) or ["_"] for c in chunks]
    bm25_scores = BM25Okapi(corpus).get_scores(_tokens(claim))
    bm25_order = sorted(range(len(chunks)), key=lambda i: bm25_scores[i], reverse=True)[:CANDIDATES]

    query_vector = (await embed([claim]))[0]
    positions = {c.id: i for i, c in enumerate(chunks)}
    nearest = await session.scalars(
        select(Chunk.id)
        .where(Chunk.paper_id == paper_id, Chunk.embedding.is_not(None))
        .order_by(Chunk.embedding.cosine_distance(query_vector))
        .limit(CANDIDATES)
    )
    vector_order = [positions[chunk_id] for chunk_id in nearest]

    ranked = fuse(bm25_order, vector_order, [c.section_kind for c in chunks])[:CANDIDATES]
    return [
        Passage(
            id=rank, text=chunks[index].text, section=chunks[index].section, kind=chunks[index].section_kind,
            page=chunks[index].page, citations=chunks[index].citations or [], score=score,
        )
        for rank, (index, score) in enumerate(ranked)
    ]


def format_passages(passages: list[Passage]) -> str:
    return "\n\n".join(f"[Passage {p.id} | {p.section or 'Unlabeled section'}]\n{p.text}" for p in passages)


async def rerank(claim: str, candidates: list[Passage]) -> list[Passage]:
    if len(candidates) <= FINAL_PASSAGES:
        return candidates
    try:
        result = await complete_json(
            load_prompt("rerank"), RerankResult, "rerank",
            claim=claim, limit=str(FINAL_PASSAGES), passages=format_passages(candidates),
        )
    except LLMRefusal:
        # Reranking only orders passages. The fused retrieval order is a sound fallback, and the
        # judge still gets its own chance to assess (or decline) the hop.
        return candidates[:FINAL_PASSAGES]
    by_id = {p.id: p for p in candidates}
    chosen = [by_id[i] for i in dict.fromkeys(result.passage_ids) if i in by_id][:FINAL_PASSAGES]
    # The judge needs the closest text even when nothing bears on the claim, so that an
    # "unsupported" verdict can still quote what the paper actually says.
    return chosen or candidates[:FINAL_PASSAGES]
