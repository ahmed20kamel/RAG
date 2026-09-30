# Document Knowledge Assistant

A retrieval-augmented question answering system over an organisation's own documents.
It answers from what the documents say, cites every passage it used, and refuses when
the evidence does not support an answer.

Arabic and English, in the same corpus and the same query.

---

## What it does

Ask a question in plain language. The system finds the passages that bear on it,
assembles them, and produces an answer in which every claim carries the number of the
passage it came from.

Four kinds of evidence are reported separately and never merged:

| | |
|---|---|
| **sources** | passages from the organisation's own documents |
| **knowledge** | claims a person taught the system and a reviewer approved |
| **web sources** | external pages, when the operator enables web fallback |
| **computed values** | numbers the system worked out, never presented as quoted |

It reads values written as words ("ثلاثون يومًا", "twenty-five thousand") as the
numbers they state, and relative time ("الأسبوع الماضي", "آخر ثلاثين يومًا", "last
month") as the dates it means — resolved against today's date, which the model is told.

Colloquial questions ("كام بندفع؟", "إمتى اتمضى العقد؟") are read in their standard form
for analysis and search, while the model answers the question as it was asked. Synonyms
("الجدار" / "السور") widen the keyword search at half weight, and approved terminology —
including interpretations the system offered and a person confirmed — joins them. A
question of two to four parts is searched part by part and answered part by part, and a
part the combined answer omits is answered on its own.

---

## Design commitments

These are properties the code enforces, not aspirations.

**It refuses rather than guesses.** When retrieval finds nothing that supports an
answer, the system says so. The refusal is produced structurally, not by asking a model
to be careful.

**Source priority is decided in code.** Where two documents disagree, the winner is
chosen from declared metadata — status, authority rank, effective date, version — and
where metadata settles nothing, both values are reported with their sources rather than
one being picked silently.

**Nothing becomes knowledge without a human.** A claim taught in conversation becomes a
pending proposal. Only an explicit approval and activation by a person with the right
capability makes it reachable by an answer.

**A calculated number is labelled as calculated.** Arithmetic the system performs on
retrieved tables is reported in its own block with its operands, and is never presented
as a quotation.

**Versions in force outrank versions they replaced.** Working documents correct
themselves by appending; where a document states plainly which of its versions applies,
that statement is put in front of the model rather than left to ranking.

---

## Architecture

```
question
   → deterministic query analysis (intent, time, scope — no model call)
   → hybrid retrieval:  vector (Qdrant) ⊕ BM25 ⊕ entity lookup ⊕ dated sweep
   → reciprocal rank fusion
   → feature reranking (lexical, structural, temporal, currency)
   → context assembly under a character budget
   → deterministic fact extraction + arithmetic verification
   → generation
   → coverage validation, completion pass where incomplete
   → answer trace
```

The retrieval arms are independent: a question naming an identifier is found by keyword
search when vector similarity misses it, and a dated section with no matching words is
found by the dated sweep when both miss it.

### Components

| | |
|---|---|
| API | FastAPI, session cookies (argon2id), capability-based permissions |
| Vector store | Qdrant — one collection for documents, one for knowledge |
| Embeddings / generation | Ollama — any model, configured per deployment |
| Registry | SQLite (WAL) with Alembic migrations |
| Client | React 18 + TypeScript + Vite, RTL and LTR |

### Ingestion

Markdown, PDF, DOCX and XLSX through one parser interface. Each format produces the same
normalised document model, and each citation carries a locator in the format's own terms
— a page for PDF, a sheet and row range for XLSX, a heading path for Markdown.

PDF handling includes OCR for scanned pages and geometric recovery of borderless tables.
Arabic text is repaired at parse time: presentation forms normalised, bidirectional
controls and tatweel removed.

---

## Running it

### Requirements

- Python 3.11+
- Qdrant (`docker compose up -d qdrant`)
- Ollama with a generation model and an embedding model

### Setup

```bash
python -m venv .venv && .venv/Scripts/activate      # Windows
pip install -r requirements.txt

cp .env.example .env                                 # then edit it
python -m alembic upgrade head
python -m app.cli bootstrap                          # creates the first admin
python run.py
```

Build the client with `cd web && npm install && npm run build`. The API serves it from
`web/dist` when present; without it the API runs unchanged.

### Configuration

Everything is in `.env`, documented inline. The values that matter most:

| | |
|---|---|
| `OLLAMA_MODEL` | the generation model |
| `OLLAMA_NUM_CTX` | context window — size it to the model's memory, not upward |
| `QDRANT_URL` | the vector store |
| `MAX_CONTEXT_CHARS` | how much evidence reaches the model |
| `WEB_SEARCH_ENABLED` | off by default; see below |
| `INTEGRATION_ENABLED` | off by default; see below |

---

## Web fallback

Disabled by default. When enabled, it runs only after the corpus has been asked and has
come back with nothing usable — never in parallel, never preferred.

What it returns is treated as untrusted input: directive phrasings are stripped, results
are reported in their own list, and nothing from the web can enter the knowledge layer
or acquire the standing of a document.

---

## Machine-to-machine API

`POST /api/v1/integrations/erp/query`, disabled by default.

Authentication is an API key plus an HMAC-SHA256 signature over the request body, a
timestamp and a single-use nonce. The secret never travels: only a key id is sent, and
the secret is derived from a master key held in the environment, so a copy of the
database cannot sign anything.

The credential resolves to a read-only service role — it can ask questions and read
document metadata, and cannot teach the system, upload, delete, or read the user list.

---

## Tests

The suites are standalone scripts. Those needing a running server read `RAG_BASE_URL`.

```bash
python tests/test_temporal_retrieval.py       # offline
python tests/test_conflict_precision.py       # offline
python tests/test_permissions.py              # needs a running server
```

Nineteen of the suites run offline against fixtures and need neither a server nor a model.
The parser suites read sample files that are generated rather than shipped — run the
builders in `tests/corpus/` once before them.

An answer is measured on two axes that are kept apart: completeness, meaning how much of
what the answer had to contain is in it, and correctness, meaning whether what it does
contain rests on the right evidence. They fail in different ways, and one number for both
hides whichever failed.

`python scripts/quality_gate.py` runs all of this in one command; see Operations.

---

## Operations

**Monitoring.** Every request leaves one record — outcome, per-stage timings, evidence
retrieved, conflicts — and never the question text. Administrators see it at
`/admin/monitoring`: rates, response-time percentiles, a daily series, why requests were
refused, the slowest answers, component reachability, backup age and free disk, with
alerts against configurable thresholds. `/api/metrics` exposes the same counters in the
Prometheus format for an existing monitoring stack.

**Backups.** `scripts/backup.py` archives the database (through SQLite's online backup
API, so it is consistent while the application writes), the uploaded originals, both
vector collections (as Qdrant snapshots) and `.env`, with a manifest of digests and
counts. `--drill` restores the snapshots into throw-away collections and counts the
points; a backup that has never been restored is not yet a backup. `scripts/restore.py`
moves the current data aside rather than deleting it.

**Quality gate.** `scripts/quality_gate.py` runs the offline suites, checks that no
frozen core file changed without being declared, measures retrieval against an accepted
baseline, and optionally answers a question set several times through a running server.
It exits non-zero on a regression.

**Scheduling.** `scripts/install-maintenance-tasks.ps1` registers a nightly backup, a
weekly restore drill and a nightly quality gate as Windows scheduled tasks.

**Optional reranker.** A local cross-encoder (`RERANKER=cross`) can be added on top of
the feature ranking. It is off by default: on a laptop CPU it cost 7–18 seconds a
question for a small gain in ranking and none in evidence recall. See
`scripts/download_reranker.py`.

---

## Operational notes

- Answers take seconds to minutes depending on the model and how much of it fits in GPU
  memory. A model that does not fit entirely is the usual cause of slow answers.
- Retrieval quality follows document structure. Sections titled with their dates, values
  written beside the words that name them, and corrections marked as corrections all
  make a large difference.
- Nothing is sent outside the deployment unless web fallback is enabled.
