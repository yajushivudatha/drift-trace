"""Scripted stand-in for the model, for tests only. It proves plumbing, not judgment."""

import json

from drifttrace.llm.client import RawResponse


def frame(subject: str = "compound X") -> dict:
    return {
        "subject": subject, "intervention_or_exposure": "compound X", "outcome": "inflammation", "relation": "reduces",
        "causal_strength": "causal", "population": "", "conditions": [], "hedges": [], "magnitude": "", "study_type_implied": "",
    }


def row(match: str = "yes") -> dict:
    return {"claimed": "a", "source": "b", "match": match}


def judgment(label: str, claimed: str, quotes: list[str], confidence: str = "high", secondary: list[str] | None = None) -> dict:
    return {
        "primary_label": label,
        "secondary_labels": secondary or [],
        "confidence": confidence,
        "confidence_reason": "Scripted.",
        "explanation": "Scripted explanation.",
        "claimed_quote": claimed,
        "source_quotes": quotes,
        "field_comparison": {
            k: row() for k in (
                "subject", "intervention_or_exposure", "outcome", "population", "conditions",
                "causal_strength", "hedging", "magnitude", "study_type",
            )
        },
        "highlights": {"source_hedges_removed": [], "source_conditions_dropped": [], "claimed_causal_terms": [], "source_causal_terms": []},
    }


class ScriptedLLM:
    """Answers by prompt type. `judge` maps a source title to the judgment dict or a list of them."""

    def __init__(self, judge: dict[str, object]) -> None:
        self.judge = {k: (list(v) if isinstance(v, list) else v) for k, v in judge.items()}
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, system: str, user: str, model: str, schema: dict, effort: str) -> RawResponse:
        if "audit scientific citations" in system:
            self.calls.append(("judge", user))
            title = user.split("Paper: ", 1)[1].split("\n", 1)[0]
            answer = self.judge[title]
            if isinstance(answer, list):
                answer = answer.pop(0)
            return RawResponse(json.dumps(answer), 1000, 200, model)
        if "bear most directly" in system:
            self.calls.append(("rerank", user))
            return RawResponse(json.dumps({"passage_ids": [0, 1]}), 500, 20, model)
        self.calls.append(("extract", user))
        return RawResponse(json.dumps(frame()), 300, 80, model)
