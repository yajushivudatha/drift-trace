import importlib.util
from pathlib import Path

import pytest

from drifttrace.llm import set_transport
from tests.fake_llm import ScriptedLLM, judgment

ROOT = Path(__file__).parents[2] / "eval"
spec = importlib.util.spec_from_file_location("run_eval", ROOT / "run_eval.py")
run_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_eval)


def test_metrics():
    pairs = [("A", "A"), ("A", "B"), ("B", "B")]
    report = run_eval.metrics(pairs)
    assert report["A"] == {"precision": 1.0, "recall": 0.5, "support": 2}
    assert report["B"]["precision"] == 0.5
    assert run_eval.confusion(pairs)["A"] == {"A": 1, "B": 1}


def test_gold_file_is_valid_and_covers_the_taxonomy():
    items = run_eval.load_gold(run_eval.GOLD)
    assert 50 <= len(items) <= 100
    assert len({i["id"] for i in items}) == len(items)
    assert {i["gold_label"] for i in items} >= {
        "FAITHFUL", "DIRECTION_CONTRADICTION", "UNSUPPORTED_ATTRIBUTION", "HEDGE_LOSS", "CAUSAL_INFLATION",
        "MAGNITUDE_DISTORTION", "SCOPE_GENERALIZATION", "CONDITION_DROPPED", "SECONDHAND_CITATION",
    }
    assert all(i.get("source_text") or i.get("cited_doi") for i in items)


@pytest.mark.db
async def test_harness_counts_quote_failures(clean_db):
    claim = "Compound X reduces inflammation."
    text = "Compound X reduced IL-6 by 12% in obese mice."
    items = [
        {"id": "good", "citing_sentence": claim, "source_title": "Good", "source_text": text, "gold_label": "FAITHFUL"},
        {"id": "bad", "citing_sentence": claim, "source_title": "Bad", "source_text": text, "gold_label": "HEDGE_LOSS"},
    ]
    invented = judgment("HEDGE_LOSS", claim, ["this quote does not exist anywhere"])
    set_transport(ScriptedLLM({"Good": judgment("FAITHFUL", claim, [text]), "Bad": [invented, invented]}))
    report = await run_eval.evaluate(items)
    assert report["n"] == 2 and report["accuracy"] == 0.5
    assert report["quote_failure_rate"] == 0.5
    assert report["errors"] == [{"id": "bad", "gold": "HEDGE_LOSS", "got": "UNVERIFIABLE"}]
    assert report["avg_cost_per_hop_usd"] > 0
