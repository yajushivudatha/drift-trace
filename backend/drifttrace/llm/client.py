"""The one module that talks to the LLM.

It owns retries, the response cache, token and cost logging, and JSON-schema validation. Everything
else calls `complete_json`.
"""

import copy
import hashlib
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from drifttrace.config import get_settings
from drifttrace.db.models import LLMCache, LLMCall
from drifttrace.db.session import session_scope
from drifttrace.llm.prompts import Prompt

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

_trace_id: ContextVar[uuid.UUID | None] = ContextVar("drifttrace_trace_id", default=None)


class LLMUnavailable(RuntimeError):
    """The LLM is not configured or could not be reached after retries."""


class LLMRefusal(RuntimeError):
    """The model declined the request. Callers record this as unverifiable, never as drift."""


@dataclass
class RawResponse:
    text: str
    input_tokens: int
    output_tokens: int
    model: str
    stop_reason: str = "end_turn"


Transport = Callable[[str, str, str, dict[str, Any], str], Awaitable[RawResponse]]
_transport_override: Transport | None = None


def set_transport(transport: Transport | None) -> None:
    """Replace the network call. Used only by the test suite."""
    global _transport_override
    _transport_override = transport


@contextmanager
def trace_scope(trace_id: uuid.UUID | None):
    """Attribute every LLM call made inside this block to a trace."""
    token = _trace_id.set(trace_id)
    try:
        yield
    finally:
        _trace_id.reset(token)


def api_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Convert a Pydantic model to the JSON-schema subset structured outputs accept."""
    schema = copy.deepcopy(model.model_json_schema())

    def clean(node: Any) -> None:
        if isinstance(node, dict):
            node.pop("title", None)
            node.pop("default", None)
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"].keys())
            for key, value in node.items():
                if key == "properties" and isinstance(value, dict):
                    for prop in value.values():
                        clean(prop)
                else:
                    clean(value)
        elif isinstance(node, list):
            for item in node:
                clean(item)

    clean(schema)
    return schema


def answer_text(content: list[Any]) -> str:
    """The response text, without any partial output from a model that declined and was replaced.

    A `fallback` block marks where another model took over, so only text after the last one counts.
    """
    parts: list[str] = []
    for block in content:
        if block.type == "fallback":
            parts = []
        elif block.type == "text":
            parts.append(block.text)
    return "".join(parts)


_client: anthropic.AsyncAnthropic | None = None


def _anthropic() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        settings = get_settings()
        if not settings.llm_configured:
            raise LLMUnavailable(
                "The LLM is not configured. Set ANTHROPIC_API_KEY and LLM_MODEL in .env, then restart."
            )
        _client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key, max_retries=settings.llm_max_retries
        )
    return _client


async def _call_anthropic(
    system: str, user: str, model: str, schema: dict[str, Any], effort: str
) -> RawResponse:
    settings = get_settings()
    output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": schema}}
    if effort:
        output_config["effort"] = effort

    # Biomedical passages can trip safety classifiers on ordinary science, so a declined request
    # is rerouted to a fallback model instead of stopping a trace.
    kwargs: dict[str, Any] = {}
    if settings.llm_refusal_fallback:
        kwargs = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}

    try:
        async with _anthropic().beta.messages.stream(
            model=model,
            max_tokens=settings.llm_max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config=output_config,
            **kwargs,
        ) as stream:
            message = await stream.get_final_message()
    except anthropic.AuthenticationError as exc:
        raise LLMUnavailable("Anthropic rejected the API key. Check ANTHROPIC_API_KEY in .env.") from exc
    except anthropic.NotFoundError as exc:
        raise LLMUnavailable(f"Anthropic does not recognize LLM_MODEL={model}.") from exc
    except anthropic.RateLimitError as exc:
        raise LLMUnavailable("Anthropic rate limit reached after retries.") from exc
    except anthropic.APIStatusError as exc:
        raise LLMUnavailable(f"Anthropic returned an error ({exc.status_code}): {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise LLMUnavailable("Could not reach the Anthropic API.") from exc

    # Refusals and truncation are returned, not raised, so that `complete_json` records the tokens
    # they cost before deciding what to do with them.
    return RawResponse(
        text=answer_text(message.content),
        input_tokens=message.usage.input_tokens,
        output_tokens=message.usage.output_tokens,
        model=message.model,
        stop_reason=message.stop_reason or "end_turn",
    )


def cost_usd(input_tokens: int, output_tokens: int) -> float:
    settings = get_settings()
    return (
        input_tokens * settings.llm_price_input_per_mtok
        + output_tokens * settings.llm_price_output_per_mtok
    ) / 1_000_000


async def _record(
    purpose: str, model: str, input_tokens: int, output_tokens: int, cache_hit: bool, latency_ms: int
) -> None:
    async with session_scope() as session:
        session.add(
            LLMCall(
                trace_id=_trace_id.get(),
                purpose=purpose,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=0.0 if cache_hit else cost_usd(input_tokens, output_tokens),
                cache_hit=cache_hit,
                latency_ms=latency_ms,
            )
        )
        await session.commit()


async def _cache_get(key: str) -> dict | None:
    async with session_scope() as session:
        cached = await session.scalar(select(LLMCache).where(LLMCache.key == key))
    return cached.response if cached is not None else None


async def _cache_put(key: str, response: dict) -> None:
    async with session_scope() as session:
        await session.execute(insert(LLMCache).values(key=key, response=response).on_conflict_do_nothing())
        await session.commit()


def effort_for(purpose: str) -> str:
    settings = get_settings()
    return {
        "extract": settings.llm_effort_extract,
        "rerank": settings.llm_effort_rerank,
        "judge": settings.llm_effort_judge,
    }.get(purpose, settings.llm_effort_extract)


async def complete_json(
    prompt: Prompt,
    output: type[T],
    purpose: str,
    extra_user: str = "",
    **variables: str,
) -> T:
    """Render a prompt, call the model (or the cache), and return a validated object."""
    settings = get_settings()
    system, user = prompt.render(**variables)
    if extra_user:
        user = f"{user}\n\n{extra_user}"

    model = settings.llm_model or "test-model"
    effort = effort_for(purpose)
    schema = api_schema(output)
    key = hashlib.sha256(
        json.dumps([model, effort, system, user, schema], sort_keys=True).encode()
    ).hexdigest()

    cached = await _cache_get(key)
    if cached is not None:
        await _record(purpose, model, 0, 0, True, 0)
        return output.model_validate(cached)

    transport = _transport_override or _call_anthropic
    last_error: Exception | None = None
    for attempt in range(settings.llm_max_retries):
        started = time.monotonic()
        raw = await transport(system, user, model, schema, effort)
        latency = int((time.monotonic() - started) * 1000)
        await _record(purpose, raw.model, raw.input_tokens, raw.output_tokens, False, latency)
        if raw.stop_reason == "refusal":
            raise LLMRefusal("The model declined to assess this passage.")
        if raw.stop_reason == "max_tokens":
            last_error = ValueError(f"the response reached LLM_MAX_TOKENS={settings.llm_max_tokens}")
            log.warning("%s attempt %d was truncated at max_tokens", purpose, attempt + 1)
            continue
        try:
            parsed = output.model_validate(json.loads(raw.text))
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = exc
            log.warning("%s attempt %d returned invalid output: %s", purpose, attempt + 1, exc)
            continue
        await _cache_put(key, parsed.model_dump(mode="json"))
        return parsed

    raise LLMUnavailable(f"{purpose} returned invalid output after retries: {last_error}")
