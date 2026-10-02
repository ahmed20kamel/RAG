# Retrieval and Validation Fix — Engineering Report

**Date:** 2026-09-25
**Scope:** temporal retrieval, metadata-vs-substantive ranking, conflict precision,
completeness-vs-correctness reporting.
**Not in scope, and not done:** agentic/iterative retrieval, model training, architecture
redesign, weakening any gate.

---

## 1. Root causes

### A — Temporal retrieval

The machinery to sweep a document's dated sections already existed and worked:
`KnowledgeStore.dated_sections()` returns sections newest-first, `Candidate.timeline_rank`
carries the ordering, and `_reserve_timeline_slots()` guarantees them a place. **It was
gated on `Intent.TIMELINE` alone.**

The cue list for that intent held the plural `"اخر تحديثات"` but not the singular
`"احدث تحديث"`. Measured directly:

```
ما آخر مرحلة وصلت إليها القضية بحسب أحدث تحديث موثق في الملف؟  →  Intent.FACT
آخر تحديثات القضية بالتواريخ                                    →  Intent.TIMELINE
```

Two phrasings of one question; only one reached the dated evidence. The decisive section
was indexed and retrievable the whole time and never entered the candidate pool.

Compounding it, **nothing in the ranking knew one date is later than another.** Even
inside the pool, a section dated 13/08 had no advantage over one dated 23/07.

### B — Metadata crowding

Sections titled "سجل إصدارات هذا الملف" and "الملفات والتقارير المنتجة" match the words
*file*, *update* and *latest* lexically. A question about the subject that happens to use
those words pulled them to the top. Measured before the fix, on three "latest" phrasings:

```
bookkeeping share of the evidence budget:  5/8,  2/8,  7/8
```

Up to **87.5 %** of the context went to the document's own changelog.

### C — Conflict false positives

One line: `_values()` called `numeric_tokens()`, whose docstring reads *"Numbers, dates
and identifiers"*. Dates and identifiers were collected **as values**. `21/06/2026`
contributed `21`, `06` and `2026`; `fb/2026-147` contributed `2026` and `147`; `v1.4.4`
contributed `1`, `4`, `4`.

Two passages then "conflicted" whenever they shared any two content words — `ملف`,
`كامل`, `حالة` — and differed anywhere in those digits. In a file of tables that is
almost any two passages.

```
before:  47 questions → 47 with conflicts → 531 total, mean 11.3, max 12 (the ceiling)
```

Every answer, on every question, including unrelated ones about other documents.

### D — Completeness vs correctness

`coverage.completeness_score` (how much retrieved evidence reached the answer) was
rendered beside `validation.complete` (whether the answer covered the question) under one
word. "100 %" next to "غير مكتملة" reads as the system contradicting itself.

---

## 2. Files changed

| File | Tier | Change |
|---|---|---|
| `app/services/temporal.py` | **new** | temporal intent, evidence dates, recency weighting |
| `app/services/evidence_class.py` | **new** | bookkeeping-vs-subject classification |
| `app/services/query_analysis.py` | frozen → declared | two signals read alongside intent |
| `app/services/reranking.py` | frozen → declared | two conditional scored features |
| `app/services/conflict_detector.py` | frozen → declared | comparison unit redesigned |
| `app/services/retriever.py` | declared | dated sweep widened; recency slot reservation |
| `app/core/retrieval.py` | declared | explainability fields on `Candidate` |
| `tests/eval/run_eval.py` | **stays frozen** | additive only: `correctness` beside `completeness` |
| `tests/rag_core_checksums.py` | tracking | disclosure of what was opened and what stayed shut |
| `web/src/i18n/{ar,en}.ts` | ui | the two readings labelled apart |
| `web/src/components/chat/AnswerDetails.tsx` | ui | each reading shown with its own label |

---

## 3. What changed in each file

**`temporal.py`** — `read_intent()` reads latest / earliest / explicit-date from a fixed
cue list matched at Arabic-aware word boundaries. `evidence_date()` reads the date a
passage is about, heading first, discarding future dates. `recency_weight()` /
`age_weight()` decay with a 45-day half-life, measured against the newest evidence *in the
same pool* rather than against today.

Precedence: an explicit date switches the generic preference **off** entirely; otherwise
latest beats earliest; otherwise nothing applies.

**`evidence_class.py`** — `classify_section()` judges a section from its heading and
breadcrumb only, never its body. `query_wants_metadata()` fires only on wordings naming a
document property — a version, a revision, an upload, an inventory, an index — never on
the bare words *file* or *update*.

**`query_analysis.py`** — two fields (`temporal`, `metadata_intent`) and three
properties. `needs_dated_sweep` is what widened the sweep beyond `Intent.TIMELINE`.
Intent classification itself is byte-for-byte unchanged.

**`reranking.py`** — two features added to the existing linear blend:

```
W_RECENCY        0.8   below W_TERM_COVERAGE (0.9), above W_HEADING (0.6)
W_EXPLICIT_DATE  1.1
W_METADATA_PENALTY 0.9  subtracted, never a filter
W_METADATA_BONUS 0.4
```

`W_RECENCY < W_TERM_COVERAGE` is the deliberate bound: a recent passage about something
else can never overtake an older one that answers the question. Dates are parsed only
when the question is temporal.

**`conflict_detector.py`** — `_values()` replaced by `_measurements()`. A `Measurement`
is a value plus the words naming what it measures plus its unit. Dates, identifiers and
version strings are **masked out before** numbers are read. A conflict requires the same
attribute (≥2 shared label words), a compatible unit, and different values.

**`retriever.py`** — the sweep runs on `analysis.needs_dated_sweep`;
`_reserve_recent_slots()` reserves **3** slots for a "latest" question, against
`limit − 2` for a timeline question, because the two want different answers.

---

## 4. Tests added

| Suite | Checks |
|---|---|
| `tests/test_temporal_retrieval.py` | 10 groups: intent, evidence dates, latest, earliest, explicit date, relevance-over-recency, undated evidence, neutral questions, explainability, multiple dates |
| `tests/test_metadata_vs_substantive.py` | 7 groups: section classification, query classification, demotion, preservation, penalty-not-filter, untouched subjects, bookkeeping still winning when it is the answer |
| `tests/test_conflict_precision.py` | 8 groups: what a measurement is, the shapes that filled the ceiling, unit incompatibility, ceiling unreachable, real contradictions surviving, one conflict per attribute, agreement, explainability |
| `tests/test_retrieval_regression_live.py` | 7 groups against the live API; **expectations derived from the corpus at run time**, so it holds meaning on any corpus |

All four pass. `tests/test_authority_and_conflicts.py` — the pre-existing conflict suite —
passes unchanged.

---

## 5–7. Baseline, final, comparison

### 38-question golden gate

| Metric | Before | After | Δ |
|---|---|---|---|
| answer_completeness | 0.956 | **0.980** | +0.024 |
| fully_complete_rate | 0.941 | **0.971** | +0.030 |
| answer_correctness | — | **1.000** | new |
| answered_rate | 0.971 | **1.000** | +0.029 |
| document_accuracy | 0.971 | **1.000** | +0.029 |
| citation_accuracy | 0.971 | **1.000** | +0.029 |
| hallucination_rate | 0.000 | 0.000 | — |
| refusal_accuracy | 1.000 | 1.000 | — |
| latency mean / median / max | 16.6 / 13.5 / 81.6 s | 16.1 / 13.5 / **42.9** s | −0.5 / 0 / −38.7 |

By category: factual 0.917→1.0, entity 0.75→1.0, multi_section 1.0→0.889 (see §11), all
others unchanged at 1.0.

### Conflicts, across all traffic on each build

| | Before | After |
|---|---|---|
| questions | 47 | 58 |
| answers reporting ≥1 conflict | 47 (100 %) | 17 (29 %) |
| total conflicts | 531 | **29** |
| mean per question | 11.3 | **0.5** |
| max | 12 (ceiling) | 3 |

### Live probes

| Probe | Before | After |
|---|---|---|
| "latest" question reaches the newest dated section | **1/5** | **5/5** |
| bookkeeping share of the evidence budget | 5/8, 2/8, 7/8 | 2/8, 1/8, 2/8 |
| metadata question still reaches metadata | — | 2/2 |
| historical question not dragged to the present | — | pass |
| conflicts on three probes | 12, 12, 12 | 0, 0, 0 |
| undated documents unaffected | — | 10/10 |

**Latency impact: none measurable.** Mean fell 0.5 s and the maximum fell 38.7 s — both
within the run-to-run variance of the shared remote model host. The new work is a date
regex over ~30 candidates, and only on temporal questions.

---

## 8. Evidence the original failure is fixed

```
ما آخر مرحلة وصلت إليها القضية بحسب أحدث تحديث موثق في الملف؟

before:  📜 13. سجل إصدارات هذا الملف ×4  |  🎯 نتائج المعاينة …     newest section: NOT retrieved
after:   newest dated section retrieved; bookkeeping down to 2 of 8 slots
```

---

## 9. Evidence that unseen cases improve

Four phrasings never used in development, including one in English, all previously
failing, all now passing:

```
ما الوضع الحالي للقضية؟                             before PASS  after PASS
ما أحدث تطور لدينا في هذا الملف؟                     before FAIL  after PASS
أين وصلنا الآن في هذا الموضوع؟                       before FAIL  after PASS
what is the latest stage reached in this matter?   before FAIL  after PASS
```

Plus five unseen questions across four other documents (financial summary, insurance
guide, method statement, review observations) — all answer or refuse cleanly with no
conflict noise. And two gate questions unrelated to this work improved on their own:
**F4 0.50→1.00**, **E1 0.00→1.00**.

---

## 10. Remaining limitations

1. **Only one document in this corpus carries dated section headings** (62 of them; the
   other eight carry none). The live evidence that recency ranking works therefore comes
   from one file. The unit suites carry the rest, on synthetic multi-document pools.
2. **Dates spelled entirely in words without digits** — "الثلاثاء الماضي", "last week" —
   are not read. Relative expressions need a reference point the passage does not state.
3. **Values written as Arabic number words** are still invisible to the conflict detector.
   Pre-existing and already recorded as a test in the suite.
4. **The half-life is 45 days**, chosen against this corpus. A corpus with a different
   rhythm would want a different constant; it is one named value, not a scattered
   assumption.
5. **Packet-level proof was not taken** for any of this; correctness rests on the suites
   and the live probes.

---

## 11. Regressions and trade-offs

### One metric moved down: `multi_section` 1.0 → 0.889 — question **M4**

Investigated rather than explained away.

| Evidence | Finding |
|---|---|
| Historical values of M4 | **0.333** in `final_v4`, `web_off`, `erp_phase1_nowebsearch`; **1.0** in `before_temporal` only |
| Four fresh repeat runs | retrieval byte-identical every time; completeness 1/3 every time |
| Does either new feature apply? | **No** — `temporal=False`, `metadata_query=False` |
| Where do the missing values live? | section `د.3) رخصة البناء (القطعة 17)` — **which is retrieved in all four runs** |
| What did the change do to its evidence? | replaced one changelog slot with a substantive section |

The evidence containing `B1N-2025-016103` and `04/02/2026` reaches the model and the
model does not state it. That is a generation shortfall on a three-part question, it
predates this work, and `0.333` is its normal value in four of five runs. The single
`1.0` was the outlier that happened to land in the baseline.

**No test expectation was changed.** The gold set still demands all three values, and M4
still scores 0.333 against it.

### Trade-offs accepted

- **Conflict recall traded for precision**, as instructed. A contradiction stated in
  prose with no label near the number is now missed. 531 → 29 is the price paid for it,
  and the alternative was a detector nobody read.
- **`W_RECENCY` is capped below term coverage.** A question where the newest evidence is
  genuinely decisive but shares no words with the question may still rank it second.
  Deliberate: the opposite error — recent and irrelevant outranking older and correct —
  is the worse one.

---

## 12. Recommendation for the next phase

1. **Take the gate to three runs and report a mean.** M4 cost an hour to clear because a
   single run cannot distinguish a regression from generation variance. This is the
   highest-value change available and it is cheap.
2. **Then look at generation, not retrieval.** Every retrieval metric on the gate is now
   at 1.000. The remaining gap — M4 — is a model stating two of three values it was
   given. Multi-part questions dropping a part is the next real class of error.
3. **Do not start agentic retrieval yet.** It was correctly excluded here; the single-pass
   pipeline had a reachability bug and a ranking gap, and both are now fixed and measured.
   Adding iteration on top of a pipeline that could not reach its own evidence would have
   hidden the bug behind retries.
4. **Re-tune the half-life when a second dated corpus exists**, not before.
