"""Score the single-hop judge against eval/gold.jsonl. Run from /backend, or with `make eval`.

Reports per-label precision and recall, a confusion matrix, the quote-verification failure rate and
the average cost per hop. Any prompt change must be run through this before it is merged.
"""

import argparse
import asyncio
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

from drifttrace.db.models import LLMCall
from drifttrace.db.session import session_scope
from drifttrace.external.http import Polite
from drifttrace.ingest.service import ensure_fulltext, resolve_paper
from drifttrace.pipeline.judge import Verdict, extract_claim, judge_passages, run_hop
from drifttrace.pipeline.locate import ClaimContext
from drifttrace.pipeline.retrieve import Passage
from drifttrace.taxonomy import Label

GOLD = Path(__file__).parent / "gold.jsonl"
LAST_RUN = Path(__file__).parent / "last_run.json"
CONCURRENCY = 4


def metrics(pairs: list[tuple[str, str]]) -> dict:
    """Per-label precision and recall from (gold, predicted) pairs."""
    labels = sorted({label for pair in pairs for label in pair})
    report = {}
    for label in labels:
        tp = sum(g == label and p == label for g, p in pairs)
        predicted = sum(p == label for _, p in pairs)
        actual = sum(g == label for g, _ in pairs)
        report[label] = {
            "precision": round(tp / predicted, 3) if predicted else None,
            "recall": round(tp / actual, 3) if actual else None,
            "support": actual,
        }
    return report


def confusion(pairs: list[tuple[str, str]]) -> dict[str, dict[str, int]]:
    counts = Counter(pairs)
    labels = sorted({label for pair in pairs for label in pair})
    return {g: {p: counts[(g, p)] for p in labels if counts[(g, p)]} for g in labels}


async def judge_item(item: dict) -> Verdict:
    context = ClaimContext(sentence=item["citing_sentence"])
    frame = await extract_claim(context)
    if item.get("source_text"):
        passage = Passage(id=0, text=item["source_text"], section="Abstract", kind="abstract")
        return await judge_passages(context, frame, item.get("source_title", ""), "FULL", [passage], item["source_text"])
    async with session_scope() as session, Polite() as http:
        paper = await resolve_paper(session, http, item["cited_doi"])
        await ensure_fulltext(session, http, paper, "fast")
        return await run_hop(session, context, frame, paper)


async def evaluate(items: list[dict]) -> dict:
    started = datetime.now(timezone.utc)
    gate = asyncio.Semaphore(CONCURRENCY)
    results: list[dict] = []

    async def one(item: dict) -> None:
        async with gate:
            verdict = await judge_item(item)
        results.append({"item": item, "verdict": verdict})
        print(f"{item['id']:<20} gold={item['gold_label']:<26} got={verdict.primary_label}")

    await asyncio.gather(*(one(item) for item in items))

    pairs = [(r["item"]["gold_label"], r["verdict"].primary_label) for r in results]
    async with session_scope() as session:
        cost = await session.scalar(select(func.coalesce(func.sum(LLMCall.cost_usd), 0.0)).where(LLMCall.created_at >= started))
    total = len(results) or 1
    return {
        "n": len(results),
        "accuracy": round(sum(g == p for g, p in pairs) / total, 3),
        "per_label": metrics(pairs),
        "confusion": confusion(pairs),
        "first_pass_quote_failure_rate": round(sum(r["verdict"].first_pass_quote_failure for r in results) / total, 3),
        "quote_failure_rate": round(sum(r["verdict"].quote_failure for r in results) / total, 3),
        "avg_cost_per_hop_usd": round(float(cost) / total, 4),
        "errors": [
            {"id": r["item"]["id"], "gold": r["item"]["gold_label"], "got": r["verdict"].primary_label}
            for r in results if r["item"]["gold_label"] != r["verdict"].primary_label
        ],
    }


def print_report(report: dict) -> None:
    print(f"\nHops: {report['n']}   Accuracy: {report['accuracy']}")
    print(f"Quote failures: {report['first_pass_quote_failure_rate']:.1%} on first try, {report['quote_failure_rate']:.1%} after retry")
    print(f"Average cost per hop: ${report['avg_cost_per_hop_usd']:.4f}\n")
    print(f"{'Label':<28}{'Precision':>10}{'Recall':>10}{'Support':>10}")
    for label, row in report["per_label"].items():
        print(f"{label:<28}{str(row['precision']):>10}{str(row['recall']):>10}{row['support']:>10}")
    print("\nConfusion (gold -> predicted)")
    for gold, row in report["confusion"].items():
        print(f"  {gold}: {row}")


def load_gold(path: Path, limit: int | None = None) -> list[dict]:
    items = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    for item in items:
        Label(item["gold_label"])
    return items[:limit] if limit else items


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, help="Evaluate only the first N hops")
    args = parser.parse_args()
    report = await evaluate(load_gold(GOLD, args.limit))
    print_report(report)
    LAST_RUN.write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
