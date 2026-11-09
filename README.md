# DriftTrace

Scientific claims mutate as they pass through citations. DriftTrace takes a claim, follows it back through the papers it cites, and shows where and how the meaning changed at each hop, with the exact text from both papers.

## Run it

You need Docker and an [Anthropic API key](https://console.anthropic.com/).

```bash
cp .env.example .env     # then set ANTHROPIC_API_KEY and CONTACT_EMAIL
docker compose up --build
```

Open <http://localhost:3000>. The first build takes a while: it installs PyTorch, and the first trace downloads the embedding model. GROBID is an `amd64` image, so on Apple Silicon it runs under emulation and is slower.

To explore the interface before adding a key, load a trace built from invented papers. It is labeled as sample data wherever it appears:

```bash
docker compose exec backend drifttrace seed-sample
```

## How a trace works

1. Resolve the paper's metadata and retraction status (OpenAlex, then Semantic Scholar and Crossref).
2. Fetch open-access full text (upload, Unpaywall, Europe PMC, arXiv, publisher OA) and parse it into sections, sentences and citation markers.
3. Find the claim sentence and resolve each citation to a DOI.
4. Extract a structured reading of the claim.
5. Retrieve the most relevant passages from the cited paper (BM25 plus embeddings, then an LLM rerank).
6. Judge the claim against those passages and verify every quotation against the stored text.
7. If the source only cites someone else for the point, continue from there.
8. Score the chain. The formula is shown in the app and printed in every report.

Rules enforced in code rather than left to the model, in `backend/drifttrace/pipeline/judge.py` and `quotes.py`:

- A quotation must occur in the stored text. Tolerance covers whitespace, line-break hyphens, ligatures and typographic quotes, and numbers must match exactly. A verdict whose quotes fail is retried once, then discarded as unverifiable.
- A source read only as an abstract can never be labeled unsupported, and its confidence is capped at medium.
- Sources that could not be read add no penalty, and a trace with nothing readable has no score rather than a score of zero.

## Development

The backend needs Python 3.11 or later, Postgres with pgvector, and Redis. The frontend needs Node 22 or later.

```bash
cd backend && pip install -e '.[dev]'
alembic upgrade head
uvicorn drifttrace.api.main:app --reload
arq drifttrace.worker.WorkerSettings

cd frontend && npm install && npm run dev
```

Command-line tools:

```bash
drifttrace ingest 10.1371/journal.pone.0005738          # print sentences with resolved citations
trace-hop 10.1371/journal.pone.0005738 "<sentence>"     # judge one sentence
```

Tests:

```bash
TEST_DATABASE_URL=postgresql+psycopg://... pytest       # unit and pipeline tests, with external APIs and the model stubbed
TEST_DATABASE_URL=postgresql+psycopg://... pytest --live # also ingests five real open-access papers
```

`python -m tests.e2e_worker` runs the real worker with canned model answers, which exercises the whole stack without an API key. Its verdicts mean nothing.

Prompts are versioned files in `backend/prompts/`. The highest `.vN.md` for a name is used.

## Evaluation

`make eval` runs the single-hop judge over `eval/gold.jsonl` and reports per-label precision and recall, a confusion matrix, the quote-failure rate and the cost per hop. It calls the model and spends API credit. Run it before merging any prompt change.

The gold set has 70 hops. 48 are adapted from [SciFact](https://github.com/allenai/scifact) (CC BY-NC 2.0). 22 were written by the developers to cover kinds of drift SciFact lacks, and have not been reviewed by domain experts. `eval/build_gold.py` documents the mapping. Review mode exports expert labels in the same format, and those should gradually replace the authored hops.

## Status

| Milestone | State |
|---|---|
| M0 Scaffold | Done. `docker compose` has not been run yet (see below) |
| M1 Ingestion | Done. Five real papers ingested live |
| M2 Single-hop judge and eval harness | Code done. No baseline score yet |
| M3 Recursion, scoring, queue, SSE | Done |
| M4 Frontend | Done |
| M5 Feedback, Review, Library, Export, Share | Done |
| M6 Dark mode, accessibility, cost dashboard, error states | Done |

Not yet verified:

- **The judge has never run against the real model.** Every test uses a stand-in for the LLM, so label quality is unmeasured. Run `make eval` first. The code targets `claude-opus-5-5` by default, and the request shape has not been accepted by the live API.
- **`docker compose up` has not been run.** The services were tested individually, outside Docker, on Python 3.11.
- **GROBID has not been run live.** The TEI parser is tested on a fixture modeled on GROBID's format. Until GROBID is confirmed, treat PDF parsing as unproven. Sources available from Europe PMC do not depend on it, and without GROBID PDFs fall back to plain text at lower confidence.

Known limitations:

- Plain-text PDF extraction cannot see superscript citation markers, so such papers need GROBID or Europe PMC.
- Europe PMC's full-text endpoint can return errors for minutes at a time. A paper affected this way shows as abstract-only with a note, and is retried on its next use.
- Single user, with no authentication, by design for v1.
