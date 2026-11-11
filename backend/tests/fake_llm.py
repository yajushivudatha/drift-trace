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
    """Answers by prompt type. `judge` maps a source title to the judgment dict or a list of them.

    Prompt types named in `refuse` ("extract", "rerank", "judge") are declined, as a safety
    classifier would.
    """

    def __init__(self, judge: dict[str, object], refuse: set[str] | None = None) -> None:
        self.judge = {k: (list(v) if isinstance(v, list) else v) for k, v in judge.items()}
        self.refuse = refuse or set()
        self.calls: list[tuple[str, str]] = []

    def _declined(self, kind: str, model: str) -> RawResponse | None:
        return RawResponse("", 300, 0, model, stop_reason="refusal") if kind in self.refuse else None

    async def __call__(self, system: str, user: str, model: str, schema: dict, effort: str) -> RawResponse:
        if "audit scientific citations" in system:
            self.calls.append(("judge", user))
            if declined := self._declined("judge", model):
                return declined
            title = user.split("Paper: ", 1)[1].split("\n", 1)[0]
            answer = self.judge[title]
            if isinstance(answer, list):
                answer = answer.pop(0)
            return RawResponse(json.dumps(answer), 1000, 200, model)
        if "bear most directly" in system:
            self.calls.append(("rerank", user))
            if declined := self._declined("rerank", model):
                return declined
            return RawResponse(json.dumps({"passage_ids": [0, 1]}), 500, 20, model)
        self.calls.append(("extract", user))
        if declined := self._declined("extract", model):
            return declined
        return RawResponse(json.dumps(frame()), 300, 80, model)
