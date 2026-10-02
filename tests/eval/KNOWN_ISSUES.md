# Known issues in the golden evaluation

Defects that are real, reproducible and deliberately **not** patched. Each one is written
down here rather than fixed because the only available fix would be specific to the
question that exposes it, and a rule aimed at one question stops the golden set from
measuring anything.

An issue leaves this file when a general change removes it — and the evidence for that is
the same measurement that put it here, not an argument.

---

## C1 — derived arithmetic presented as if it were sourced

**Question:** «ما الفرق بين الكشف المالي الأصلي والمعاد حسابه ولماذا اختلفا؟»

**Observed:** the answer computes a per-metre rate and states it as a figure —
`3,784 درهم (121,095 درهم ÷ 32 متر)` and `3,788 درهم (126,788 ÷ 35)`. Neither number
appears in any document. The evaluator flags unsupported values, so the run reports
`hallucination_rate = 0.026` (1 of 38).

**Substance of the answer is otherwise correct.** 121,095 / 126,788 and 32m / 35m are the
gold values, and the explanation of *why* they differ is right. What is wrong is that a
calculation the model performed is presented in the same voice as a quoted figure.

**Frequency:** first seen in `phase1_after`, after 21 clean runs. Re-running C1 alone on
unchanged code reproduced it in **1 of 3** attempts. It is stochastic, not deterministic.

**Ruled out as a Phase 1 regression**, by measurement rather than reasoning:

* every citation in C1's answer carries an empty `locator`, so the branch Phase 1 added
  to the context header never executes for this question — the prompt is byte-identical
  to what it was before;
* every construction of `Candidate` in the codebase is by keyword, so the two fields
  added to that dataclass cannot have shifted anything positionally;
* the reproduction on unchanged code is the direct evidence.

**Why it is not being fixed here.** The honest fixes are general and each is a change to
the RAG core, which ingestion work is not allowed to touch:

1. instruct generation to mark derived values as derived — a prompt change affecting
   every answer, and it needs its own before/after measurement;
2. teach the validator that an arithmetic result whose operands are both cited is
   supported — a change to what "unsupported" means, with consequences well beyond C1.

Either is a candidate for a later phase. Neither belongs in a parser.

**What must never be done about it:** special-casing C1, adding a rule that recognises
this question, or adjusting the evaluator so the flag stops appearing. The flag is
correct. The system did state a number it could not cite.

**Watch for:** `hallucination_rate` above 0.026, or the flag appearing on any question
other than C1. Either would be a new problem rather than this one.

---

## Correction: the cash flow statement does balance

In the final validation report I wrote that the operating section of the Statement of
Cash Flows did not reconcile, and labelled it a **CONFIRMED INCONSISTENCY**. It was not
one. The section balances exactly:

    30,166 − 633,487 − 150,000 + 603,321 = (150,000) = (A)

I had treated the line printed as `(180,166)` as a fourth operand. It is not: it is the
subtotal of the working capital movement, and the deterministic verifier finds that
relation too —

    (633,487) + (150,000) + 603,321 = (180,166)

Both are now confirmed by calculation rather than by reading, which is the point of
having built the verifier. The error was mine, and it was exactly the kind the layer
exists to prevent: a plausible arithmetic claim asserted from a glance at flattened text.

What remains unverified about that document, and is **not** claimed either way:

* whether `Unaudited` on the statement of financial position conflicts with "the
  audited financial statements" in the directors' report — the two were never retrieved
  into the same answer, so nothing has compared them;
* whether the notes range `6-20` in the contents conflicts with `6 to 19` in the
  statement footers.

Both are observations about text, not arithmetic, and the verifier makes no claim about
either.
