"""Runs the real arq worker with a canned model, to exercise the stack without an API key.

Everything except the model's answers is real: queue, database, paper fetching, parsing, retrieval
and quote verification. Verdicts it produces say nothing about judgment quality. Never use this in
production.

Usage (from /backend): python -m tests.e2e_worker
"""

import json
import re

from arq.worker import run_worker

from drifttrace.llm import set_transport
from drifttrace.llm.client import RawResponse
from drifttrace.worker import WorkerSettings
from tests.fake_llm import frame, judgment


async def canned(system: str, user: str, model: str, schema: dict, effort: str) -> RawResponse:
    if "audit scientific citations" in system:
        claimed = user.split("TARGET SENTENCE: ", 1)[1].split("\nNext sentence:", 1)[0].strip()
        passage = user.split("<source>", 1)[1].split("[Passage", 1)[1].split("]\n", 1)[1].split("\n\n", 1)[0]
        quote = re.split(r"(?<=[.?!])\s", passage, maxsplit=1)[0]
        answer = judgment("HEDGE_LOSS", claimed, [quote], confidence="medium")
        answer["explanation"] = "Canned end-to-end test verdict. Not a real assessment."
        return RawResponse(json.dumps(answer), 1200, 300, model)
    if "bear most directly" in system:
        return RawResponse(json.dumps({"passage_ids": [0, 1, 2]}), 900, 30, model)
    return RawResponse(json.dumps(frame()), 400, 90, model)


if __name__ == "__main__":
    set_transport(canned)
    run_worker(WorkerSettings)
