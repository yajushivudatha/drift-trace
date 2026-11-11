"""End-to-end pipeline and API tests against a real Postgres, with the model and network stubbed."""

import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from drifttrace.api.main import app
from drifttrace.db.models import Chunk, Hop, LLMCache, LLMCall, Paper, Reference, Trace, TraceEvent, TraceNode
from drifttrace.db.session import session_scope
from drifttrace.ingest.service import write_chunks
from drifttrace.ingest.text import build_paragraph
from drifttrace.llm import set_transport
from drifttrace.pipeline.trace import run_trace
from drifttrace.schemas import CitationMarker, ParsedDoc, RefEntry, Section
from tests.fake_llm import ScriptedLLM, judgment

pytestmark = pytest.mark.db

SENTENCE = "Compound X treats inflammatory disease in humans [1]."


def para(text: str, refs: dict[str, list[str]] | None = None):
    markers = []
    for marker, keys in (refs or {}).items():
        start = text.index(marker)
        markers.append(CitationMarker(marker=marker, ref_keys=keys, start=start, end=start + len(marker)))
    return build_paragraph(text, markers, page=1)


async def make_paper(doi: str, title: str, paragraphs, refs: dict[str, str] | None = None, status: str = "FULL", **kw) -> Paper:
    doc = ParsedDoc(
        sections=[Section(title="Results", kind="results", paragraphs=paragraphs)] if paragraphs else [],
        references=[RefEntry(key=k, index=i, doi=v, title=f"Reference to {v}") for i, (k, v) in enumerate((refs or {}).items())],
    )
    async with session_scope() as session:
        paper = Paper(
            id=uuid.uuid4(), doi=doi, title=title, authors=kw.pop("authors", ["Ann Lee"]), year=2020, language="en", fulltext_status=status,
            fulltext_attempt="thorough", parsed=doc.model_dump(mode="json") if paragraphs else None,
            fulltext=doc.full_text() if paragraphs else "Only an abstract about something else entirely.",
            abstract=None if paragraphs else "Only an abstract about something else entirely.", **kw,
        )
        session.add(paper)
        await session.flush()
        for ref in doc.references:
            session.add(Reference(citing_paper_id=paper.id, ref_index=ref.index, ref_key=ref.key, title=ref.title, resolved_doi=ref.doi))
        await write_chunks(session, paper, doc if paragraphs else None)
        await session.commit()
        return paper


async def make_trace(claim: str = SENTENCE, doi: str = "10.1/a", input_type: str = "doi", **settings) -> uuid.UUID:
    async with session_scope() as session:
        trace = Trace(input_type=input_type, input_claim=claim, settings={"doi": doi, "max_depth": 4, "max_branches": 3, "mode": "fast", **settings})
        session.add(trace)
        await session.commit()
        return trace.id


async def load(trace_id):
    async with session_scope() as session:
        trace = await session.get(Trace, trace_id)
        nodes = list(await session.scalars(select(TraceNode).where(TraceNode.trace_id == trace_id)))
        hops = list(await session.scalars(select(Hop).where(Hop.trace_id == trace_id)))
    return trace, nodes, hops


async def chain_papers():
    await make_paper("10.1/a", "Citing", [para(SENTENCE, {"[1]": ["b0"]})], {"b0": "10.1/b"})
    await make_paper("10.1/b", "Middle", [para("Compound X reduces inflammation [7].", {"[7]": ["b0"]})], {"b0": "10.1/c"})
    await make_paper("10.1/c", "Origin", [para("Compound X reduced IL-6 by 12% in obese mice at 500 mg/kg.")])


async def test_multi_hop_chain_reaches_origin_and_scores(clean_db):
    await chain_papers()
    llm = ScriptedLLM({
        "Middle": judgment("SECONDHAND_CITATION", SENTENCE, ["Compound X reduces inflammation [7]."], secondary=["SCOPE_GENERALIZATION"]),
        "Origin": judgment("CONDITION_DROPPED", "Compound X reduces inflammation [7].", ["Compound X reduced IL-6 by 12% in obese mice at 500 mg/kg."]),
    })
    set_transport(llm)
    trace_id = await make_trace()
    await run_trace(trace_id)
    trace, nodes, hops = await load(trace_id)

    assert trace.status == "done", trace.error
    assert sorted(n.depth for n in nodes) == [0, 1, 2]
    assert [n.depth for n in nodes if n.is_origin] == [2]
    assert {h.primary_label for h in hops} == {"SECONDHAND_CITATION", "CONDITION_DROPPED"}
    assert all(h.quotes_verified for h in hops)
    # 0 + secondary 18 x .25 = 4.5 on hop 1, and 8 on hop 2.
    assert trace.fidelity_score == 88
    async with session_scope() as session:
        events = list(await session.scalars(select(TraceEvent).where(TraceEvent.trace_id == trace_id)))
        calls = await session.scalar(select(func.count(LLMCall.id)).where(LLMCall.trace_id == trace_id))
    assert {"status", "node", "hop", "cost"} <= {e.kind for e in events}
    assert calls > 0, "LLM calls are attributed to the trace"


async def test_hallucinated_quote_is_retried_then_discarded(clean_db):
    await make_paper("10.1/a", "Citing", [para(SENTENCE, {"[1]": ["b0"]})], {"b0": "10.1/c"})
    await make_paper("10.1/c", "Origin", [para("Compound X reduced IL-6 by 12% in obese mice at 500 mg/kg.")])
    bad = judgment("MAGNITUDE_DISTORTION", SENTENCE, ["Compound X reduced IL-6 by 90% in humans at every dose."])
    llm = ScriptedLLM({"Origin": [bad, bad]})
    set_transport(llm)
    trace_id = await make_trace()
    await run_trace(trace_id)
    trace, nodes, hops = await load(trace_id)

    assert [kind for kind, _ in llm.calls].count("judge") == 2, "retried exactly once"
    assert "exact-quote check" in llm.calls[-1][1], "the retry tells the model what failed"
    assert hops[0].primary_label == "UNVERIFIABLE"
    assert hops[0].source_quotes == [] and hops[0].quotes_verified is False
    assert trace.fidelity_score is None
    assert not any(n.is_origin for n in nodes)


async def test_retry_success_keeps_verdict(clean_db):
    await make_paper("10.1/a", "Citing", [para(SENTENCE, {"[1]": ["b0"]})], {"b0": "10.1/c"})
    source = "Compound X reduced IL-6 by 12% in obese mice at 500 mg/kg."
    await make_paper("10.1/c", "Origin", [para(source)])
    llm = ScriptedLLM({"Origin": [judgment("SCOPE_GENERALIZATION", SENTENCE, ["invented text not in the paper"]), judgment("SCOPE_GENERALIZATION", SENTENCE, [source])]})
    set_transport(llm)
    trace_id = await make_trace()
    await run_trace(trace_id)
    _, _, hops = await load(trace_id)
    assert hops[0].primary_label == "SCOPE_GENERALIZATION" and hops[0].quotes_verified


async def test_refused_claim_extraction_marks_hops_unverifiable_and_finishes(clean_db):
    await make_paper("10.1/a", "Citing", [para(SENTENCE, {"[1]": ["b0", "b1"]})], {"b0": "10.1/b", "b1": "10.1/c"})
    await make_paper("10.1/b", "Middle", [para("Compound X reduces inflammation.")])
    await make_paper("10.1/c", "Origin", [para("Compound X reduced IL-6 by 12% in obese mice at 500 mg/kg.")])
    llm = ScriptedLLM({}, refuse={"extract"})
    set_transport(llm)
    trace_id = await make_trace()
    await run_trace(trace_id)
    trace, _, hops = await load(trace_id)

    assert trace.status == "done", trace.error
    assert len(hops) == 2 and all(h.primary_label == "UNVERIFIABLE" for h in hops)
    assert all("declined" in h.explanation for h in hops)
    assert [kind for kind, _ in llm.calls].count("extract") == 1, "one claim, one extraction, even when refused"
    assert trace.fidelity_score is None


async def test_refused_rerank_still_reaches_a_verdict(clean_db):
    filler = "The cohort was followed for twelve weeks with fasting blood draws and standardized diets. " * 2
    long_paragraphs = [para(f"Compound X was studied in cohort {i} and reduced IL-6 by {i}% in obese mice. {filler}") for i in range(6)]
    await make_paper("10.1/a", "Citing", [para(SENTENCE, {"[1]": ["b0"]})], {"b0": "10.1/c"})
    await make_paper("10.1/c", "Origin", long_paragraphs)
    quote = "Compound X was studied in cohort 1 and reduced IL-6 by 1% in obese mice."
    llm = ScriptedLLM({"Origin": judgment("SCOPE_GENERALIZATION", SENTENCE, [quote])}, refuse={"rerank"})
    set_transport(llm)
    trace_id = await make_trace()
    await run_trace(trace_id)
    trace, _, hops = await load(trace_id)

    assert trace.status == "done", trace.error
    assert "rerank" in [kind for kind, _ in llm.calls], "enough passages to need reranking"
    assert hops[0].primary_label == "SCOPE_GENERALIZATION"


async def test_abstract_only_source_is_never_unsupported(clean_db):
    await make_paper("10.1/a", "Citing", [para(SENTENCE, {"[1]": ["b0"]})], {"b0": "10.1/c"})
    await make_paper("10.1/c", "Origin", [], status="ABSTRACT_ONLY")
    set_transport(ScriptedLLM({"Origin": judgment("UNSUPPORTED_ATTRIBUTION", SENTENCE, ["Only an abstract about something else"])}))
    trace_id = await make_trace()
    await run_trace(trace_id)
    trace, _, hops = await load(trace_id)
    assert hops[0].primary_label == "UNVERIFIABLE"
    assert "full text unavailable" in hops[0].explanation
    assert trace.fidelity_score is None


async def test_no_text_source_makes_no_llm_judgment(clean_db):
    await make_paper("10.1/a", "Citing", [para(SENTENCE, {"[1]": ["b0"]})], {"b0": "10.1/c"})
    async with session_scope() as session:
        session.add(Paper(doi="10.1/c", title="Origin", fulltext_status="NONE", fulltext_attempt="thorough", language="en"))
        await session.commit()
    llm = ScriptedLLM({})
    set_transport(llm)
    trace_id = await make_trace()
    await run_trace(trace_id)
    _, nodes, hops = await load(trace_id)
    assert hops[0].primary_label == "UNVERIFIABLE" and "judge" not in [k for k, _ in llm.calls]
    assert [n.stop_reason for n in nodes if n.depth == 1] == ["no_fulltext"]


async def test_retracted_source_becomes_primary_and_keeps_finding(clean_db):
    await make_paper("10.1/a", "Citing", [para(SENTENCE, {"[1]": ["b0"]})], {"b0": "10.1/c"})
    source = "Compound X reduced IL-6 by 12% in obese mice at 500 mg/kg."
    await make_paper("10.1/c", "Origin", [para(source)], is_retracted=True, retraction_note="Retraction notice: x")
    set_transport(ScriptedLLM({"Origin": judgment("CAUSAL_INFLATION", SENTENCE, [source])}))
    trace_id = await make_trace()
    await run_trace(trace_id)
    trace, _, hops = await load(trace_id)
    assert hops[0].primary_label == "RETRACTED_SOURCE"
    assert hops[0].secondary_labels == ["CAUSAL_INFLATION"]
    assert trace.fidelity_score == 60  # 35 + 18 x 0.25 = 39.5


async def test_citation_cycle_stops(clean_db):
    await make_paper("10.1/a", "Citing", [para(SENTENCE, {"[1]": ["b0"]})], {"b0": "10.1/b"})
    await make_paper("10.1/b", "Middle", [para("Compound X reduces inflammation [7].", {"[7]": ["b0"]})], {"b0": "10.1/a"})
    set_transport(ScriptedLLM({"Middle": judgment("SECONDHAND_CITATION", SENTENCE, ["Compound X reduces inflammation [7]."])}))
    trace_id = await make_trace()
    await run_trace(trace_id)
    trace, nodes, hops = await load(trace_id)
    assert trace.status == "done", trace.error
    assert len(nodes) == 2
    assert any(h.is_cycle for h in hops)


async def test_depth_limit_stops(clean_db):
    await chain_papers()
    set_transport(ScriptedLLM({"Middle": judgment("SECONDHAND_CITATION", SENTENCE, ["Compound X reduces inflammation [7]."])}))
    trace_id = await make_trace(max_depth=1)
    await run_trace(trace_id)
    _, nodes, hops = await load(trace_id)
    assert len(hops) == 1
    assert [n.stop_reason for n in nodes if n.depth == 1] == ["max_depth"]


async def test_bundle_is_limited_by_branches_and_makes_one_hop_each(clean_db):
    bundle = "Compound X treats inflammatory disease in humans [1-3]."
    await make_paper("10.1/a", "Citing", [para(bundle, {"[1-3]": ["b0", "b1", "b2"]})], {"b0": "10.1/p", "b1": "10.1/q", "b2": "10.1/r"})
    source = "Compound X reduced IL-6 by 12% in obese mice at 500 mg/kg."
    judged = {}
    for name in "PQR":
        await make_paper(f"10.1/{name.lower()}", name, [para(source)])
        judged[name] = judgment("FAITHFUL", bundle, [source])
    set_transport(ScriptedLLM(judged))
    trace_id = await make_trace(claim=bundle, max_branches=2)
    await run_trace(trace_id)
    trace, _, hops = await load(trace_id)
    assert len(hops) == 2
    assert trace.fidelity_score == 100


async def test_self_citation_flagged(clean_db):
    await make_paper("10.1/a", "Citing", [para(SENTENCE, {"[1]": ["b0"]})], {"b0": "10.1/c"}, authors=["Ann Lee", "Bo Kim"])
    source = "Compound X reduced IL-6 by 12% in obese mice at 500 mg/kg."
    await make_paper("10.1/c", "Origin", [para(source)], authors=["A. Lee"])
    set_transport(ScriptedLLM({"Origin": judgment("FAITHFUL", SENTENCE, [source])}))
    trace_id = await make_trace()
    await run_trace(trace_id)
    _, _, hops = await load(trace_id)
    assert hops[0].is_self_citation


async def test_free_text_claim_goes_straight_to_source(clean_db):
    source = "Compound X reduced IL-6 by 12% in obese mice at 500 mg/kg."
    await make_paper("10.1/c", "Origin", [para(source)])
    claim = "A news story says compound X cures arthritis."
    set_transport(ScriptedLLM({"Origin": judgment("SCOPE_GENERALIZATION", claim, [source])}))
    trace_id = await make_trace(claim=claim, doi="10.1/c", input_type="free_text")
    await run_trace(trace_id)
    trace, nodes, hops = await load(trace_id)
    assert trace.status == "done", trace.error
    root = next(n for n in nodes if n.depth == 0)
    assert root.paper_id is None and root.label == "Claim as given"
    assert hops[0].quotes_verified


@pytest.mark.parametrize(
    ("claim", "message"),
    [("Nothing like this sentence appears in the paper at all.", "not found"), ("The effect was robust across all conditions.", "no citations")],
)
async def test_unusable_sentence_fails_with_guidance(clean_db, claim, message):
    await make_paper("10.1/a", "Citing", [para(SENTENCE, {"[1]": ["b0"]}), para("The effect was robust across all conditions.")], {"b0": "10.1/b"})
    set_transport(ScriptedLLM({}))
    trace_id = await make_trace(claim=claim)
    await run_trace(trace_id)
    trace, _, _ = await load(trace_id)
    assert trace.status == "failed" and message in trace.error


async def test_cancelled_trace_stops(clean_db):
    await chain_papers()
    set_transport(ScriptedLLM({}))
    trace_id = await make_trace()
    async with session_scope() as session:
        trace = await session.get(Trace, trace_id)
        trace.status = "queued"
        await session.commit()

    async def cancel_soon():
        async with session_scope() as session:
            trace = await session.get(Trace, trace_id)
            trace.status = "cancelling"
            await session.commit()

    from drifttrace.pipeline import trace as module

    real_start = module.TraceRunner.start

    async def start_then_cancel(self):
        work = await real_start(self)
        await cancel_soon()
        return work

    module.TraceRunner.start = start_then_cancel
    try:
        await run_trace(trace_id)
    finally:
        module.TraceRunner.start = real_start
    loaded, _, hops = await load(trace_id)
    assert loaded.status == "cancelled" and not hops


async def test_llm_cache_avoids_a_second_call(clean_db):
    await chain_papers()
    source = "Compound X reduces inflammation [7]."
    llm = ScriptedLLM({"Middle": judgment("FAITHFUL", SENTENCE, [source])})
    set_transport(llm)
    first = await make_trace()
    await run_trace(first)
    calls_after_first = len(llm.calls)
    second = await make_trace()
    await run_trace(second)
    assert len(llm.calls) == calls_after_first
    async with session_scope() as session:
        assert await session.scalar(select(func.count()).select_from(LLMCache)) > 0
        hits = await session.scalar(select(func.count(LLMCall.id)).where(LLMCall.cache_hit.is_(True)))
    assert hits > 0


async def test_chunks_have_embeddings(clean_db):
    await make_paper("10.1/c", "Origin", [para("Compound X reduced IL-6 by 12% in obese mice at 500 mg/kg.")])
    async with session_scope() as session:
        assert await session.scalar(select(func.count(Chunk.id)).where(Chunk.embedding.is_not(None))) == 1


# --- HTTP API ----------------------------------------------------------------------------------


class FakeQueue:
    def __init__(self):
        self.jobs = []

    async def enqueue_job(self, name, trace_id):
        self.jobs.append((name, trace_id))

    async def ping(self):
        return True


@pytest.fixture
async def client():
    app.state.queue = FakeQueue()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def finished_trace():
    await chain_papers()
    set_transport(ScriptedLLM({
        "Middle": judgment("SECONDHAND_CITATION", SENTENCE, ["Compound X reduces inflammation [7]."], confidence="low"),
        "Origin": judgment("CONDITION_DROPPED", "Compound X reduces inflammation [7].", ["Compound X reduced IL-6 by 12% in obese mice at 500 mg/kg."]),
    }))
    trace_id = await make_trace()
    await run_trace(trace_id)
    return str(trace_id)


async def test_api_trace_lifecycle(clean_db, client):
    created = await client.post("/traces", json={"input_type": "doi", "doi": "https://doi.org/10.1000/a", "claim": SENTENCE})
    assert created.status_code == 201
    assert app.state.queue.jobs == [("trace_job", created.json()["id"])]
    assert (await client.post("/traces", json={"input_type": "doi", "doi": "nonsense", "claim": SENTENCE})).status_code == 422
    assert (await client.post("/traces", json={"input_type": "doi", "doi": "10.1000/a", "claim": SENTENCE, "settings": {"max_depth": 9}})).status_code == 422

    trace_id = await finished_trace()
    data = (await client.get(f"/traces/{trace_id}")).json()
    assert len(data["nodes"]) == 3 and len(data["hops"]) == 2
    assert data["label_counts"]["CONDITION_DROPPED"] == 1
    assert len(data["worst_path"]) == 2 and "Fidelity" in data["score_formula"]

    stream = await client.get(f"/traces/{trace_id}/events")
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert "Trace complete" in stream.text and "event: end" in stream.text

    listed = (await client.get("/traces", params={"label": "CONDITION_DROPPED", "sort": "score"})).json()
    assert listed["total"] == 1 and listed["items"][0]["label_counts"]["SECONDHAND_CITATION"] == 1
    assert (await client.get("/traces", params={"q": "absent phrase"})).json()["total"] == 0


async def test_api_feedback_review_gold(clean_db, client):
    trace_id = await finished_trace()
    data = (await client.get(f"/traces/{trace_id}")).json()
    low = next(h for h in data["hops"] if h["confidence"] == "low")
    high = next(h for h in data["hops"] if h["confidence"] == "high")

    assert (await client.post(f"/hops/{high['id']}/feedback", json={"agrees": False})).status_code == 422
    response = await client.post(f"/hops/{high['id']}/feedback", json={"agrees": False, "user_label": "FAITHFUL", "note": "reads fine"})
    assert response.json()["user_label"] == "FAITHFUL"
    assert (await client.post(f"/hops/{low['id']}/feedback", json={"agrees": True})).json()["user_label"] == low["primary_label"]

    queue = (await client.get("/review/queue")).json()["items"]
    assert {i["id"] for i in queue} == {low["id"], high["id"]}, "disagreements and low confidence both queue"

    assert (await client.post(f"/hops/{high['id']}/gold", json={"gold_label": "FAITHFUL"})).status_code == 200
    assert {i["id"] for i in (await client.get("/review/queue")).json()["items"]} == {low["id"]}
    exported = (await client.get("/review/export.jsonl")).text
    assert '"gold_label": "FAITHFUL"' in exported and "10.1/c" in exported


async def test_api_export_share_delete(clean_db, client):
    trace_id = await finished_trace()
    for fmt, needle in (("md", b"Drift path"), ("json", b'"hops"'), ("pdf", b"%PDF")):
        response = await client.get(f"/traces/{trace_id}/export", params={"format": fmt})
        assert response.status_code == 200 and needle in response.content
    token = (await client.post(f"/traces/{trace_id}/share")).json()["share_token"]
    assert (await client.post(f"/traces/{trace_id}/share")).json()["share_token"] == token
    shared = (await client.get(f"/shared/{token}")).json()
    assert shared["share_token"] is None and len(shared["hops"]) == 2
    assert (await client.get("/shared/wrong")).status_code == 404

    assert (await client.delete(f"/traces/{trace_id}")).status_code == 204
    assert (await client.get(f"/traces/{trace_id}")).status_code == 404
    _, nodes, hops = await load(uuid.UUID(trace_id))
    assert not nodes and not hops


async def test_api_cost_stats_and_sentences(clean_db, client):
    await finished_trace()
    stats = (await client.get("/stats/cost")).json()
    assert stats["total_calls"] > 0 and stats["total_cost_usd"] > 0 and stats["by_purpose"]
    async with session_scope() as session:
        paper = await session.scalar(select(Paper).where(Paper.doi == "10.1/a"))
    data = (await client.get(f"/papers/{paper.id}/sentences", params={"with_citations": "true"})).json()
    assert len(data["sentences"]) == 1 and data["sentences"][0]["citations"][0]["marker"] == "[1]"
    assert (await client.get("/papers/not-a-uuid")).status_code == 404


async def test_upload_rejects_non_pdf(clean_db, client):
    response = await client.post("/papers/upload", files={"file": ("x.pdf", b"not a pdf", "application/pdf")})
    assert response.status_code == 422


async def test_sample_trace_is_flagged_and_quotes_are_in_its_text(clean_db, client):
    from drifttrace.pipeline.quotes import quote_in_text
    from drifttrace.sample import seed

    trace_id = await seed()
    await seed()  # reseeding replaces it
    data = (await client.get("/traces")).json()
    assert data["total"] == 1 and data["items"][0]["is_sample"] is True
    trace_id = data["items"][0]["id"]
    full = (await client.get(f"/traces/{trace_id}")).json()
    assert full["fidelity_score"] is not None
    async with session_scope() as session:
        texts = {str(p.id): p.fulltext for p in await session.scalars(select(Paper))}
    nodes = {n["id"]: n for n in full["nodes"]}
    for hop in full["hops"]:
        for quote in hop["source_quotes"]:
            assert quote_in_text(quote["text"], texts[nodes[hop["to"]]["paper"]["id"]])


async def test_timeout_does_not_mark_full_text_as_settled(clean_db, monkeypatch):
    from drifttrace.external import scholarly as api
    from drifttrace.ingest import service

    class Http:
        transient_failures = 0

    http = Http()

    async def urls(_http, _paper, _mode):
        return [], None

    results = iter([None, JATS])

    async def epmc(client, _doi):
        answer = next(results)
        if answer is None:
            client.transient_failures += 1
        return answer

    monkeypatch.setattr(service, "_pdf_urls", urls)
    monkeypatch.setattr(api, "europepmc_fulltext_xml", epmc)
    async with session_scope() as session:
        paper = Paper(doi="10.1/slow", title="Slow", abstract="An abstract.")
        session.add(paper)
        await session.commit()
        await service.ensure_fulltext(session, http, paper, "fast")
        assert paper.fulltext_status == "ABSTRACT_ONLY" and "timed out" in paper.fulltext_note
        await service.ensure_fulltext(session, http, paper, "fast")
        assert paper.fulltext_status == "FULL", "the next request tries again"
        await service.ensure_fulltext(session, http, paper, "fast")


JATS = (Path(__file__).parent / "fixtures" / "sample.jats.xml").read_text()
