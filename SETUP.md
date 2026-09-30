# Setting this up from nothing

What the source alone does not give you, and the order to do it in.

---

## 1. Hardware — read this before anything else

Answer latency is decided almost entirely by whether the generation model fits in GPU
memory. A model that spills into system RAM does not fail; it gets slow, and the
slowdown is roughly proportional to how much spilled.

Measured on a reference deployment:

| model size | GPU memory | result |
|---|---|---|
| 12.2 GB | 12 GB card — 84% resident | 8–30 s per answer |
| 18.7 GB | same card — 43% resident | 60–180 s per answer |

Size the card to the model, not the other way round. A model's memory need is its weight
file plus roughly 0.5–1 GB for the context window and runtime.

The CPU is not the constraint. On the reference deployment a 24-core CPU sat idle while
the GPU was the bottleneck.

---

## 2. Dependencies

**Qdrant** — the vector store.

```bash
docker compose up -d qdrant
```

It must be running before the API starts. If it is not, the API comes up and answers
every question with "not enough information", because retrieval finds nothing.

**Ollama** — generation and embeddings. They can be on different machines: generation on
whatever has the GPU, embeddings anywhere.

```bash
ollama pull <generation-model>
ollama pull bge-m3            # or another multilingual embedding model
```

For access across a network, Ollama must bind beyond localhost (`OLLAMA_HOST=0.0.0.0`)
and the firewall must allow its port.

---

## 3. Install

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows;  source .venv/bin/activate elsewhere
pip install -r requirements.txt

cp .env.example .env
python -m alembic upgrade head
python -m app.cli bootstrap       # reads BOOTSTRAP_* from .env
python run.py
```

Client:

```bash
cd web && npm install && npm run build
```

The API serves the built client from `web/dist` when it exists, and runs as an API-only
service when it does not.

---

## 4. Configuration that matters

Everything is documented inline in `.env.example`. The values worth thinking about:

| | |
|---|---|
| `OLLAMA_MODEL` | the generation model |
| `OLLAMA_NUM_CTX` | context window. Raising it costs GPU memory and can push the model out of the card |
| `EMBEDDING_MODEL` | must handle the languages in your corpus |
| `QDRANT_URL` | the vector store |
| `MAX_CONTEXT_CHARS` | how much evidence reaches the model per answer |
| `WEB_SEARCH_ENABLED` | off by default. Leave it off unless external sources are wanted |
| `INTEGRATION_ENABLED` | off by default. The machine-to-machine API |

`OLLAMA_TIMEOUT` should exceed the slowest answer the deployment will produce. On a
model that spills to RAM this can be minutes.

---

## 5. Your own documents

The system ships with no corpus. Upload through the client, or `POST /api/documents/upload`.

Markdown, PDF, DOCX and XLSX. A document is not searchable until its status reads
`completed`; ingestion runs in the background and takes seconds to minutes depending on
size and whether OCR is needed.

### How documents are written changes how well they answer

This is the largest free improvement available, and it is worth saying to whoever
maintains the corpus:

- **Put dates in section headings.** `## Ruling (05/03/2026)` rather than `## Ruling`.
  The system reads dates from headings to decide what is most recent.
- **Say which version is in force.** Words like "in force", "current", "as corrected",
  "deleted", "superseded" are read as currency markers, and a passage carrying them is
  preferred over the version it replaced.
- **Mark corrections rather than deleting.** A struck-through row keeps the history and
  tells the system the row no longer applies.
- **Put values next to the words that name them.** `Maximum penalty | 50,000 AED`
  rather than a bare number in a column called `Value`. A number with nothing naming it
  is a number that cannot be retrieved.
- **Always write the unit.** It is what stops an amount being compared with a duration.
- **Keep a section about one subject.** A section covering ten topics is retrieved for
  every question and answers none of them.

---

## 6. Verifying your changes

The `tests/` directory holds standalone scripts, not a pytest suite. Offline ones need
nothing; the rest read `RAG_BASE_URL` and need a running server.

```bash
python tests/test_parsing_and_chunking.py     # offline
python tests/test_multiformat_foundation.py   # offline
python tests/test_permissions.py              # needs a server
```

Nineteen suites run offline and need neither a server nor a model. The parser suites read
sample documents that are generated rather than shipped, so run the builders once first:

```bash
python tests/corpus/build_docx_corpus.py
python tests/corpus/build_xlsx_corpus.py
python tests/corpus/build_pdf_corpus.py
python tests/corpus/build_borderless_corpus.py
```

They write Word, Excel and PDF files covering the shapes a parser has to survive: real
heading styles and bold lines pretending to be headings, borderless tables, mixed
languages, Arabic stored as display glyphs, scanned pages, broken formula references and
truncated files.

The Word set borrows a workbook to test that a renamed file is rejected on its contents
rather than its extension, and runs the spreadsheet builder itself if it has not run.

All nineteen offline suites pass on a machine with no OCR installed except two checks in
`test_pdf_parser.py`, which read a scanned page and need the Arabic language data in
section 7.

### The quality gate

```bash
python scripts/quality_gate.py                 # offline suites, core fingerprint, retrieval
python scripts/quality_gate.py --full 3        # and the question set, answered three times
python scripts/quality_gate.py --accept --reason "..."   # make this run the baseline
```

It exits non-zero on a regression. The retrieval and answer stages need an evaluation
set in `tests/eval/`; without one they are skipped and say so. Accepting a new baseline
is explicit and needs a reason, and the previous one is kept in its history: a gate
that moves its own bar measures nothing.

### Build your own evaluation set

No question set is included — one is specific to a corpus and has to be written for
yours. Its shape: a question, the values a correct answer must contain, the document it
should rest on, and whether the corpus can answer it at all.

Its shape: a question, the values a correct answer must contain, the document it should
rest on, and whether the corpus can answer it at all. Include questions the corpus
**cannot** answer; refusal accuracy is as important a measurement as any other.

Note on reading the results: a single run is not a measurement. Generation varies between
runs, and on the reference deployment two questions oscillated between full and partial
credit across identical runs. Run a set three times and report the mean and the range
before concluding that anything regressed.

---

## 7. Optional: OCR for scanned documents

Scanned PDFs are read through Tesseract, which is looked for in `TESSERACT_CMD`, then on
the PATH, then in the usual install locations. Its language data is read from
`data/tessdata/` beside the application if that directory exists, and from the system
installation otherwise.

Arabic OCR needs `ara.traineddata`, and page-orientation detection needs
`osd.traineddata`; both are published by the Tesseract project. Without them scanned
documents are refused with a message saying so rather than indexed as empty, and every
other format is unaffected.

---

## 8. Backups and scheduled maintenance

```bash
python scripts/backup.py            # take one now
python scripts/backup.py --verify   # digests, database integrity, row counts
python scripts/backup.py --drill    # and restore the vectors into throw-away collections
```

Each archive holds the database, the uploaded files, both vector collections and `.env`.
`.env` is included for `INTEGRATION_MASTER_KEY`: integration secrets are derived from it
and stored nowhere, so losing it invalidates every credential issued. **The archives
therefore hold secrets.** Store them as such.

Set `BACKUP_MIRROR_DIR` to a second physical disk or an internal share. A backup on the
same disk as the data does not survive that disk. Seven daily and four weekly archives
are kept by default.

To schedule it all, from an ordinary PowerShell window:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-maintenance-tasks.ps1
```

This registers a backup at 02:00, a restore drill on Sundays at 03:00 and the quality
gate at 03:30. `-Remove` takes them away again.

To restore, stop the application, then:

```bash
python scripts/restore.py backups/rag-backup-<time>.zip          # dry run: verifies only
python scripts/restore.py backups/rag-backup-<time>.zip --yes    # replaces the live data
```

The current files are moved into `data/pre-restore-<time>/`. The archived `.env` is
written beside the live one, never over it: whether to run with the restored master key
is a decision, because it revives the credentials issued before the backup and
invalidates any issued since.

---

## 9. Monitoring

Administrators get a Monitoring page. It reads one record per request and shows
outcomes, response-time percentiles by stage, a daily series, refusal reasons, the
slowest answers, component reachability, backup age and free disk.

Alerts fire past `MONITOR_P95_MS`, `MONITOR_ERROR_RATE` and `MONITOR_REFUSAL_RATE`, once
there are enough requests to mean anything, and whenever the last good backup is older
than `BACKUP_MAX_AGE_HOURS`.

A frequent refusal reason of "evidence found, but not for this wording" means people ask
in words the documents do not use. That is fixed in the documents, not in the code.

For Prometheus, set `METRICS_TOKEN` and scrape `/api/metrics` with
`Authorization: Bearer <token>`.

---

## 10. Optional: the cross-encoder reranker

```bash
pip install -r requirements-reranker.txt
python scripts/download_reranker.py            # or --small for the faster model
```

then `RERANKER=cross` in `.env`. It adds a model's reading of each passage on top of the
feature ranking, never instead of it, and falls back to the feature ranking if the model
is missing, fails, or runs past `RERANKER_BUDGET_MS`.

Measure before keeping it. The retrieval evaluation reports section rank and evidence
recall with and without it; on the reference machine the gain did not justify the
latency, which is why it ships off.

---

## 11. Two things that will bite

**After a machine restart** — Qdrant runs in a container. If Docker is not running, the
container is not running, and the system answers everything with "not enough
information" while appearing perfectly healthy.

**Clipboard in the browser** — over plain HTTP on a LAN address, browsers do not expose
the clipboard API. The client has a fallback, but anything else added later that copies
text needs the same treatment or it will fail silently for every user.
