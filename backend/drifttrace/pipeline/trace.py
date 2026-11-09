"""Steps 3, 7 and 8: build the citation graph, following claims until they reach an origin."""

import logging
import re
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from drifttrace.db.models import Hop, LLMCall, Paper, Reference, Trace, TraceEvent, TraceNode
from drifttrace.db.session import session_scope
from drifttrace.external.http import Polite
from drifttrace.ingest.embeddings import embed
from drifttrace.ingest.service import PaperNotFound, ensure_fulltext, resolve_paper, resolve_reference
from drifttrace.llm import trace_scope
from drifttrace.pipeline.judge import Verdict, extract_claim, run_hop, unverifiable
from drifttrace.pipeline.locate import ClaimContext, find_sentence, unique_ref_keys
from drifttrace.pipeline.quotes import squash
from drifttrace.schemas import CitationMarker, ParsedDoc
from drifttrace.taxonomy import SEVERITY, Label, ScoredHop, fidelity_score, worst_path

log = logging.getLogger(__name__)
NOT_AN_ORIGIN = {Label.UNVERIFIABLE, Label.UNSUPPORTED_ATTRIBUTION, Label.SECONDHAND_CITATION}


class TraceInputError(ValueError):
    """The trace cannot start. The message is written for the user."""


class Cancelled(Exception):
    pass


async def emit(trace_id: uuid.UUID, kind: str, message: str, **data) -> None:
    async with session_scope() as session:
        session.add(TraceEvent(trace_id=trace_id, kind=kind, message=message, data=data))
        await session.commit()


def short_cite(paper: Paper | None) -> str:
    if paper is None:
        return "Unknown source"
    surname = (paper.authors or ["Unknown"])[0].split()[-1]
    suffix = " et al." if len(paper.authors or []) > 2 else ""
    return f"{surname}{suffix} {paper.year or 'n.d.'}"


def is_self_citation(citing: Paper | None, cited: Paper | None) -> bool:
    if citing is None or cited is None:
        return False

    def identity(name: str) -> tuple[str, str]:
        parts = name.lower().split()
        return (parts[-1], parts[0][0]) if parts else ("", "")

    return bool({identity(a) for a in citing.authors or []} & {identity(a) for a in cited.authors or []})


@dataclass
class Target:
    reference: Reference | None
    marker: str | None
    paper: Paper | None = None
    label: str | None = None


@dataclass
class Work:
    node_id: uuid.UUID
    paper: Paper | None
    context: ClaimContext
    targets: list[Target]
    depth: int
    path: frozenset[uuid.UUID]


class TraceRunner:
    def __init__(self, session: AsyncSession, http: Polite, trace: Trace) -> None:
        self.session = session
        self.http = http
        self.trace = trace
        self.nodes_by_paper: dict[uuid.UUID, TraceNode] = {}
        self.expanded: set[uuid.UUID] = set()
        self.settings = trace.settings

    async def say(self, message: str, kind: str = "log", **data) -> None:
        await emit(self.trace.id, kind, message, **data)

    async def check_cancelled(self) -> None:
        async with session_scope() as other:
            status = await other.scalar(select(Trace.status).where(Trace.id == self.trace.id))
        if status == "cancelling":
            raise Cancelled

    async def report_cost(self) -> None:
        calls, cost = (
            await self.session.execute(
                select(func.count(LLMCall.id), func.coalesce(func.sum(LLMCall.cost_usd), 0.0)).where(
                    LLMCall.trace_id == self.trace.id, LLMCall.cache_hit.is_(False)
                )
            )
        ).one()
        await self.say(f"{calls} model calls so far", kind="cost", calls=calls, cost_usd=round(float(cost), 4))

    async def add_node(self, paper: Paper | None, depth: int, label: str | None = None) -> TraceNode:
        node = TraceNode(id=uuid.uuid4(), trace_id=self.trace.id, paper_id=paper.id if paper else None, label=label, depth=depth)
        self.session.add(node)
        await self.session.commit()
        if paper is not None:
            self.nodes_by_paper[paper.id] = node
        await self.say(
            f"Added {short_cite(paper) if paper else label or 'source'} to the graph", kind="node", node_id=str(node.id), depth=depth,
        )
        return node

    async def rank_targets(self, context: ClaimContext, paper: Paper, citations: list[CitationMarker]) -> list[Target]:
        """Turn citation markers into at most max_branches targets, most relevant first."""
        pairs = unique_ref_keys(citations)
        if not pairs:
            return []
        rows = await self.session.scalars(
            select(Reference).where(Reference.citing_paper_id == paper.id, Reference.ref_key.in_([k for k, _ in pairs]))
        )
        by_key = {r.ref_key: r for r in rows}
        found = [(by_key[k], m) for k, m in pairs if k in by_key]
        if not found:
            return []

        limit = self.settings.get("max_branches", 3)
        if len(found) > limit:
            texts = [r.title or r.raw_text or "" for r, _ in found]
            vectors = np.array(await embed([context.sentence, *texts]))
            similarity = vectors[1:] @ vectors[0]
            order = np.argsort(-similarity)[:limit]
            await self.say(f"This sentence cites {len(found)} papers. Following the {limit} most relevant.")
            found = [found[int(i)] for i in order]
        return [Target(reference=r, marker=m, label=r.title or r.raw_text) for r, m in found]

    async def load_target(self, target: Target) -> Paper | None:
        if target.paper is not None:
            return target.paper
        reference = target.reference
        if reference is None:
            return None
        await self.say(f"Resolving reference: {(target.label or 'untitled')[:90]}")
        doi = await resolve_reference(self.session, self.http, reference)
        if not doi:
            return None
        try:
            paper = await resolve_paper(self.session, self.http, doi)
        except PaperNotFound:
            return None
        reference.resolved_paper_id = paper.id
        await self.session.commit()
        return await self.prefer_published(paper)

    async def prefer_published(self, paper: Paper) -> Paper:
        published = next((v["doi"] for v in paper.related_versions or [] if v.get("relation") == "is-preprint-of"), None)
        if not published:
            return paper
        try:
            version = await resolve_paper(self.session, self.http, published)
        except PaperNotFound:
            return paper
        await self.say(f"Using the published version of this preprint ({published}).")
        return version

    async def store_hop(
        self, work: Work, to_node: TraceNode, target: Target, verdict: Verdict, cycle: bool = False
    ) -> Hop:
        hop = Hop(
            id=uuid.uuid4(), trace_id=self.trace.id, from_node_id=work.node_id, to_node_id=to_node.id, marker=target.marker,
            claimed_quote=verdict.claimed_quote or work.context.sentence,
            claimed_location={"section": work.context.section, "page": work.context.page},
            source_quotes=verdict.source_quotes, primary_label=verdict.primary_label, secondary_labels=verdict.secondary_labels,
            confidence=verdict.confidence, confidence_reason=verdict.confidence_reason, explanation=verdict.explanation,
            field_comparison=verdict.field_comparison, highlights=verdict.highlights, quotes_verified=verdict.quotes_verified,
            source_status=verdict.source_status, is_self_citation=is_self_citation(work.paper, target.paper), is_cycle=cycle,
            retrieval_score=verdict.retrieval_score, prompt_version=verdict.prompt_version,
        )
        self.session.add(hop)
        await self.session.commit()
        await self.say(
            f"Verdict: {verdict.primary_label.replace('_', ' ').lower()} ({verdict.confidence} confidence)",
            kind="hop", hop_id=str(hop.id), label=verdict.primary_label,
        )
        return hop

    def next_claim(self, paper: Paper, verdict: Verdict) -> tuple[ClaimContext, list[CitationMarker]] | None:
        """Find the statement in this paper, and its citations, that the trace should follow next."""
        if not paper.parsed:
            return None
        doc = ParsedDoc.model_validate(paper.parsed)
        quotes = [q["text"] for q in verdict.source_quotes]
        for quote in quotes:
            context = find_sentence(doc, quote)
            if context and context.citations:
                return context, context.citations
        quote_forms = [squash(q) for q in quotes]
        for section in doc.sections:
            for paragraph in section.paragraphs:
                if paragraph.citations and any(form in squash(paragraph.text) for form in quote_forms):
                    sentence = next(
                        (s for s in paragraph.sentences if any(squash(s.text) in form or form in squash(s.text) for form in quote_forms)),
                        paragraph.sentences[0],
                    )
                    return (
                        ClaimContext(sentence=sentence.text, section=section.title or None, page=paragraph.page),
                        paragraph.citations,
                    )
        return None

    async def process_target(self, work: Work, target: Target, queue: deque[Work]) -> None:
        mode = self.settings.get("mode", "fast")
        paper = await self.load_target(target)
        depth = work.depth + 1

        if paper is None:
            node = await self.add_node(None, depth, target.label or "Unresolved reference")
            node.stop_reason = "unresolved"
            await self.session.commit()
            await self.store_hop(
                work, node, target,
                unverifiable("The reference could not be matched to a paper or DOI.", work.context.sentence, "NONE"),
            )
            return

        target.paper = paper
        if paper.id in work.path:
            node = self.nodes_by_paper[paper.id]
            await self.say(f"Citation cycle: {short_cite(paper)} already appears earlier in this chain. Stopping this branch.")
            verdict = unverifiable(
                "This citation forms a cycle back to a paper already in the chain.", work.context.sentence, paper.fulltext_status or "NONE",
            )
            await self.store_hop(work, node, target, verdict, cycle=True)
            return

        await self.say(f"Fetching full text for {short_cite(paper)}")

        async def notify(message: str) -> None:
            await self.say(f"{short_cite(paper)}: {message}")

        await ensure_fulltext(self.session, self.http, paper, mode, notify)
        availability = {"FULL": "full text found", "ABSTRACT_ONLY": "only the abstract is available"}
        await self.say(f"{short_cite(paper)}: {availability.get(paper.fulltext_status, 'no text is available')}")

        node = self.nodes_by_paper.get(paper.id) or await self.add_node(paper, depth, paper.title)
        await self.say(f"Comparing hop {depth} against {short_cite(paper)}")
        claim_frame = await extract_claim(work.context)
        verdict = await run_hop(self.session, work.context, claim_frame, paper)
        await self.store_hop(work, node, target, verdict)
        await self.report_cost()

        primary = verdict.primary_label
        node.claim_frame = verdict.source_frame
        if verdict.source_quotes:
            node.claim_text = verdict.source_quotes[0]["text"]

        should_follow = primary == Label.SECONDHAND_CITATION or (
            paper.work_type == "review" and primary not in (Label.UNVERIFIABLE, Label.UNSUPPORTED_ATTRIBUTION)
        )
        if should_follow and paper.id not in self.expanded:
            if depth >= self.settings.get("max_depth", 4):
                node.stop_reason = "max_depth"
                await self.say(f"Reached the depth limit at {short_cite(paper)}.")
            else:
                followed = self.next_claim(paper, verdict)
                targets = await self.rank_targets(followed[0], paper, followed[1]) if followed else []
                if followed and targets:
                    self.expanded.add(paper.id)
                    queue.append(Work(node.id, paper, followed[0], targets, depth, work.path | {paper.id}))
                    await self.say(f"{short_cite(paper)} cites other work for this point. Following {len(targets)} citation(s).")
                else:
                    node.stop_reason = "no_citations"
        elif not should_follow:
            if primary == Label.UNVERIFIABLE:
                node.stop_reason = "no_fulltext" if verdict.source_status != "FULL" else "unverifiable"
            elif primary not in NOT_AN_ORIGIN:
                node.is_origin = True
                node.stop_reason = "origin"
            else:
                node.stop_reason = "not_found"
        await self.session.commit()

    async def start(self) -> Work:
        trace = self.trace
        mode = self.settings.get("mode", "fast")

        if trace.input_type == "free_text":
            doi = (trace.settings or {}).get("doi")
            await self.say(f"Resolving paper {doi}")
            try:
                paper = await resolve_paper(self.session, self.http, doi)
            except PaperNotFound as exc:
                raise TraceInputError(str(exc)) from exc
            trace.start_paper_id = paper.id
            root = await self.add_node(None, 0, "Claim as given")
            root.claim_text = trace.input_claim
            await self.session.commit()
            return Work(root.id, None, ClaimContext(sentence=trace.input_claim), [Target(None, None, paper=paper)], 0, frozenset())

        if trace.input_type == "upload":
            paper = await self.session.get(Paper, trace.start_paper_id)
            if paper is None:
                raise TraceInputError("The uploaded paper is no longer available. Upload it again.")
        else:
            doi = (trace.settings or {}).get("doi")
            await self.say(f"Resolving paper {doi}")
            try:
                paper = await resolve_paper(self.session, self.http, doi)
            except PaperNotFound as exc:
                raise TraceInputError(str(exc)) from exc
            trace.start_paper_id = paper.id
            await self.session.commit()

        await self.say(f"Fetching full text for {short_cite(paper)}")

        async def notify(message: str) -> None:
            await self.say(f"{short_cite(paper)}: {message}")

        await ensure_fulltext(self.session, self.http, paper, mode, notify)
        if paper.fulltext_status != "FULL" or not paper.parsed:
            raise TraceInputError(
                "The full text of this paper is not openly available, so its citations cannot be read. "
                "Upload the PDF, or use a free-text claim with the DOI of the paper it cites."
            )

        doc = ParsedDoc.model_validate(paper.parsed)
        context = find_sentence(doc, trace.input_claim)
        if context is None:
            raise TraceInputError(
                "That sentence was not found in the paper. Paste it exactly as written, or pick it from the paper's text."
            )
        count = sum(len(c.ref_keys) for c in context.citations)
        if not context.citations:
            raise TraceInputError(
                "That sentence has no citations to follow. Pick a sentence with a citation marker, or use a free-text claim."
            )
        await self.say(f"Found {count} citation(s) in the claim sentence")

        root = await self.add_node(paper, 0, paper.title)
        root.claim_text = context.sentence
        await self.session.commit()
        targets = await self.rank_targets(context, paper, context.citations)
        if not targets:
            raise TraceInputError("The citations in that sentence could not be matched to the paper's reference list.")
        return Work(root.id, paper, context, targets, 0, frozenset({paper.id}))

    async def finish(self) -> None:
        trace = self.trace
        hops = list(await self.session.scalars(select(Hop).where(Hop.trace_id == trace.id)))
        root_id = await self.session.scalar(
            select(TraceNode.id).where(TraceNode.trace_id == trace.id, TraceNode.depth == 0)
        )
        scored = to_scored(hops)
        trace.fidelity_score = fidelity_score(scored, root_id)
        trace.summary = summarize(hops, worst_path(scored, root_id), trace.fidelity_score)
        trace.status = "done"
        trace.finished_at = datetime.now(timezone.utc)
        await self.session.commit()
        await self.report_cost()
        await self.say("Trace complete", kind="status", status="done")

    async def run(self) -> None:
        queue: deque[Work] = deque([await self.start()])
        while queue:
            work = queue.popleft()
            for target in work.targets:
                await self.check_cancelled()
                await self.process_target(work, target, queue)
        await self.finish()


def to_scored(hops: list[Hop]) -> list[ScoredHop]:
    return [
        ScoredHop(h.id, h.from_node_id, h.to_node_id, h.primary_label, tuple(h.secondary_labels or []), h.confidence)
        for h in hops
    ]


def summarize(hops: list[Hop], worst: list[ScoredHop], score: int | None) -> str:
    """A plain summary built from the hop records, so that it cannot say more than they do."""
    total = len(hops)
    unverifiable_count = sum(h.primary_label == Label.UNVERIFIABLE for h in hops)
    if score is None:
        return (
            f"None of the {total} citation{'s' if total != 1 else ''} checked could be verified against a readable source, "
            "so this trace has no fidelity score. That does not mean the claim is unsupported."
        )
    drifted = [h for h in hops if SEVERITY.get(h.primary_label, 0) > 0]
    parts = [f"{total} citation{'s were' if total != 1 else ' was'} checked and {len(drifted)} showed drift."]
    if worst:
        worst_hop = max(worst, key=lambda h: SEVERITY.get(h.primary_label, 0))
        if SEVERITY.get(worst_hop.primary_label, 0) > 0:
            parts.append(f"The most serious change was {re.sub('_', ' ', worst_hop.primary_label).lower()}.")
    if unverifiable_count:
        parts.append(f"{unverifiable_count} could not be verified, and the score does not count them.")
    return " ".join(parts)


async def run_trace(trace_id: uuid.UUID) -> None:
    """Entry point for the worker. Records every failure on the trace for the user to read."""
    async with session_scope() as session, Polite() as http:
        trace = await session.get(Trace, trace_id)
        if trace is None or trace.status not in ("queued",):
            return
        trace.status = "running"
        await session.commit()
        runner = TraceRunner(session, http, trace)
        try:
            with trace_scope(trace_id):
                await runner.say("Trace started", kind="status", status="running")
                await runner.run()
        except Cancelled:
            await session.rollback()
            trace.status = "cancelled"
            trace.finished_at = datetime.now(timezone.utc)
            await session.commit()
            await runner.say("Trace cancelled", kind="status", status="cancelled")
        except Exception as exc:  # noqa: BLE001 - any failure must end up visible on the trace
            await session.rollback()
            message = str(exc) if exc.__class__.__name__ in ("TraceInputError", "LLMUnavailable") else (
                "The trace stopped because of an internal error. Details are in the worker log."
            )
            if message.startswith("The trace stopped"):
                log.exception("Trace %s failed", trace_id)
            trace.status = "failed"
            trace.error = message
            trace.finished_at = datetime.now(timezone.utc)
            await session.commit()
            await runner.say(message, kind="status", status="failed")
