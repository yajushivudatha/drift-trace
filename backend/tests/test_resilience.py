"""How the pipeline behaves when the model declines, truncates, or an API misbehaves. No database."""

import json
from types import SimpleNamespace

import httpx
import pytest

from drifttrace.external.http import Polite
from drifttrace.llm import LLMRefusal, LLMUnavailable, set_transport
from drifttrace.llm import client as llm
from drifttrace.llm.client import RawResponse, answer_text, complete_json
from drifttrace.llm.prompts import load_prompt
from drifttrace.pipeline import retrieve, trace
from drifttrace.pipeline.locate import ClaimContext
from drifttrace.pipeline.retrieve import FINAL_PASSAGES, Passage, rerank
from drifttrace.schemas import ClaimFrame
from tests.fake_llm import frame


@pytest.fixture
def no_db(monkeypatch):
    """Stand in for the LLM cache and call log, and collect what would have been recorded."""
    recorded: list[tuple] = []
    stored: dict[str, dict] = {}

    async def record(*args) -> None:
        recorded.append(args)

    async def cache_get(key: str) -> dict | None:
        return stored.get(key)

    async def cache_put(key: str, response: dict) -> None:
        stored[key] = response

    monkeypatch.setattr(llm, "_record", record)
    monkeypatch.setattr(llm, "_cache_get", cache_get)
    monkeypatch.setattr(llm, "_cache_put", cache_put)
    yield SimpleNamespace(recorded=recorded, stored=stored)
    set_transport(None)


def scripted(*responses: RawResponse):
    queue = list(responses)
    calls: list[str] = []

    async def transport(system, user, model, schema, effort) -> RawResponse:
        calls.append(user)
        return queue.pop(0)

    transport.calls = calls
    return transport


async def extract(sentence: str = "Compound X treats disease.") -> ClaimFrame:
    return await complete_json(
        load_prompt("claim_extract"), ClaimFrame, "extract", before="(none)", sentence=sentence, after="(none)"
    )


async def test_refusal_is_raised_and_its_tokens_are_recorded(no_db):
    set_transport(scripted(RawResponse("", 900, 40, "test-model", stop_reason="refusal")))
    with pytest.raises(LLMRefusal):
        await extract()
    assert [(r[2], r[3], r[4]) for r in no_db.recorded] == [(900, 40, False)]
    assert not no_db.stored, "a refusal is never cached"


async def test_truncated_response_is_retried_and_counted(no_db):
    transport = scripted(
        RawResponse('{"subject": "compound', 900, 16000, "test-model", stop_reason="max_tokens"),
        RawResponse(json.dumps(frame()), 900, 80, "test-model"),
    )
    set_transport(transport)
    result = await extract()
    assert result.subject == "compound X"
    assert len(transport.calls) == 2
    assert [r[3] for r in no_db.recorded] == [16000, 80], "the truncated attempt's output tokens are billed"


async def test_always_truncated_gives_a_readable_error(no_db):
    truncated = RawResponse("{", 900, 16000, "test-model", stop_reason="max_tokens")
    set_transport(scripted(*[truncated] * 5))
    with pytest.raises(LLMUnavailable, match="LLM_MAX_TOKENS"):
        await extract()


def block(kind: str, text: str = "") -> SimpleNamespace:
    return SimpleNamespace(type=kind, text=text)


def test_answer_text_drops_output_from_a_declined_model():
    assert answer_text([block("thinking"), block("text", '{"a": '), block("text", "1}")]) == '{"a": 1}'
    assert answer_text([block("text", '{"partial'), block("fallback"), block("text", '{"a": 1}')]) == '{"a": 1}'
    assert answer_text([block("thinking")]) == ""


async def test_rerank_refusal_falls_back_to_retrieval_order(monkeypatch):
    async def refuse(*_, **__):
        raise LLMRefusal("declined")

    monkeypatch.setattr(retrieve, "complete_json", refuse)
    candidates = [Passage(id=i, text=f"passage {i}") for i in range(10)]
    chosen = await rerank("claim", candidates)
    assert [p.id for p in chosen] == list(range(FINAL_PASSAGES))


async def test_claim_frame_is_extracted_once_per_work(monkeypatch):
    calls = 0

    async def fake_extract(_context):
        nonlocal calls
        calls += 1
        return ClaimFrame.model_validate(frame())

    monkeypatch.setattr(trace, "extract_claim", fake_extract)
    work = trace.Work(None, None, ClaimContext(sentence="s"), [], 0, frozenset())
    assert await work.frame() is await work.frame()
    assert calls == 1


async def test_claim_frame_refusal_is_not_requested_again(monkeypatch):
    calls = 0

    async def refuse(_context):
        nonlocal calls
        calls += 1
        raise LLMRefusal("declined")

    monkeypatch.setattr(trace, "extract_claim", refuse)
    work = trace.Work(None, None, ClaimContext(sentence="s"), [], 0, frozenset())
    for _ in range(3):
        with pytest.raises(LLMRefusal):
            await work.frame()
    assert calls == 1


@pytest.mark.parametrize(
    ("header", "low", "high"),
    [
        ("7", 7.0, 7.0),
        ("not a date", 1.0, 1.0),  # falls back to 2**attempt
        ("Wed, 21 Oct 2015 07:28:00 GMT", 0.0, 0.0),  # in the past
        ("Fri, 31 Dec 9999 23:59:59 GMT", 60.0, 60.0),  # capped
    ],
)
def test_retry_after_parsing(header, low, high):
    response = httpx.Response(429, headers={"retry-after": header})
    assert low <= Polite._retry_after(response, 0) <= high


async def test_gives_up_without_a_final_wait(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("drifttrace.external.http.asyncio.sleep", fake_sleep)
    transport = httpx.MockTransport(lambda request: httpx.Response(503, headers={"retry-after": "30"}))
    async with Polite(retries=3) as http:
        http.client = httpx.AsyncClient(transport=transport)
        http._pace = lambda host: _done()
        assert await http.get("https://example.org/x") is None
        assert http.transient_failures == 1
    assert sleeps == [30.0, 30.0], "waits between attempts, not after the last one"


async def _done() -> None:
    return None
