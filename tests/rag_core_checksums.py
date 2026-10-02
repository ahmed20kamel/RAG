"""A fingerprint of the RAG core, so "I did not touch it" is checked rather than claimed.

Multi-format ingestion adds parsers in front of a pipeline that must keep behaving
exactly as it did. The golden set proves the *answers* did not move; this proves the
*code* did not, which is the stronger claim and the cheaper one to check. A phase that
changes a retrieval file has to say so out loud, because this script will name it.

Two tiers, because they carry different promises:

* FROZEN — retrieval, ranking, contracts, coverage, generation. Nothing in an ingestion
  change has any business here. A difference is a defect until proven otherwise.
* DECLARED — the citation surface and the vector payload. These are expected to change,
  and the point of listing them separately is that an unexpected change elsewhere stays
  visible instead of drowning in a list of known edits.

    python tests/rag_core_checksums.py --save phase0_before
    python tests/rag_core_checksums.py --compare phase0_before
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

RESULTS = ROOT / "tests" / "eval" / "results"

#: Touching any of these during an ingestion phase is a defect.
#
#: `run_eval.py` and `dataset.py` stay here permanently and deliberately. The evaluator
#: is the thing that says whether a change helped, and a suite whose scoring can be
#: adjusted alongside the code it scores measures nothing. It was edited once, during the
#: temporal-ranking work, and the edit was additive only: a `correctness` field beside the
#: existing `completeness` field, and a legend explaining that the two measure different
#: things. No existing metric's computation changed, which is what makes the before and
#: after numbers from that work comparable at all. Any future change here needs the same
#: standard — new keys, never altered ones.
FROZEN = [
    "app/services/fact_sheet.py",
    "app/services/contract_builder.py",
    "app/services/evidence_planner.py",
    "app/services/coverage.py",
    "app/services/completion.py",
    "app/services/answer_validation.py",
    "app/services/knowledge_arm.py",
    "app/services/knowledge_service.py",
    "app/services/llm.py",
    "app/core/text.py",
    "app/core/contract.py",
    "app/core/evidence.py",
    "app/core/authority.py",
    "tests/eval/dataset.py",
    "tests/eval/run_eval.py",
    # Frozen from Phase 2 onward: XLSX shipped and passed its gate, so a later phase
    # that needs to change it has to say so rather than adjust it in passing.
    "app/parsers/xlsx_parser.py",
    "app/parsers/markdown_parser.py",
    # Frozen from Phase 3 onward, for the same reason XLSX was.
    "app/parsers/docx_parser.py",
]

#: `retriever.py` and `core/retrieval.py` started out frozen and were moved here during
#: Phase 0, deliberately and not quietly. A citation's locator is produced by the parser
#: and read by the citation builder, and the only path between them runs through the
#: candidate these two files define. Nothing else was opened up: what they are allowed
#: to gain is a field that is carried and never read for ranking, so no query, filter,
#: score or ordering can depend on it. The golden set is what holds that to account.

#: Expected to change, and named here so the change is deliberate rather than noticed.
DECLARED = [
    "app/services/retriever.py",
    "app/core/retrieval.py",
    "app/core/domain.py",
    "app/services/ingestion.py",
    "app/services/chunking.py",
    "app/services/structure.py",
    "app/services/entities.py",
    "app/services/context_builder.py",
    "app/services/vector_store.py",
    "app/services/knowledge_store.py",
    "app/services/keyword_index.py",
    "app/services/document_service.py",
    "app/models/document.py",
    "app/models/knowledge.py",
    "app/container.py",
    "app/config.py",
    # Added in Phase 4: the PDF parser was in neither list, so a change to it went
    # unrecorded by the very tool meant to record changes. Listed now with the
    # module it gained.
    "app/parsers/pdf_parser.py",
    "app/parsers/layout_tables.py",
    "app/parsers/arabic_pdf.py",
    "app/parsers/text_repair.py",
    "app/parsers/tables.py",
    "app/parsers/base.py",
    "app/parsers/detection.py",
    "app/parsers/ocr.py",
    # Moved out of FROZEN when the deterministic arithmetic layer was added, on an
    # explicit instruction to build it into the answer pipeline. What it gained is a
    # block of calculated values, kept separate from the facts block and labelled as
    # calculated, plus the rule that tells the model to say so. Nothing about
    # retrieval, ranking, contracts or coverage was opened up, and the golden set is
    # what holds that to account.
    "app/services/rag_service.py",
    "app/services/arithmetic.py",
    # Added during ERP integration Phase 1. `web_search.py` was in neither list, so the
    # same gap the PDF parser note above records applied to it: a change would have gone
    # unrecorded by the tool meant to record changes. What it gained is a per-call gate
    # on `enabled`, which can close a door the operator left open and can never open one
    # they closed; the default keeps every existing caller's behaviour identical.
    #
    # The integration modules are listed from the start, so the next phase inherits a
    # recorded baseline rather than discovering one is missing.
    # Moved out of FROZEN for the temporal-ranking and conflict-precision work, on an
    # explicit instruction to fix a measured retrieval failure. Named here rather than
    # edited quietly, because ranking is exactly what freezing these was protecting.
    #
    # What was opened:
    #   query_analysis.py  — two signals read alongside the intent, never replacing it:
    #                        what the question says about time, and whether it asks about
    #                        the document as an object. Intent classification itself is
    #                        byte-for-byte unchanged.
    #   reranking.py       — two scored features, both inert unless the question calls
    #                        for them. A question with no temporal wording and no
    #                        metadata wording scores exactly as it did before.
    #   conflict_detector.py — the unit of comparison. It compared sets of digits found
    #                        anywhere in a passage; it now compares a value against the
    #                        words that name what it measures. Nothing about authority
    #                        resolution changed.
    #
    # What stayed shut: retrieval arms, fusion, expansion, the context budget, the
    # answer contract, coverage and generation. The 38-question gate is what holds that
    # to account, and it was run before and after with every metric recorded.
    "app/services/query_analysis.py",
    "app/services/reranking.py",
    "app/services/conflict_detector.py",
    "app/services/temporal.py",
    "app/services/evidence_class.py",
    # Added with the supersession signal. A document appends its corrections rather
    # than replacing what it corrects, so the same fact can have three live answers in
    # one file; this reads which of them says it is the one in force.
    "app/services/supersession.py",
    # The in-force block. Added after two measured attempts to settle version currency
    # by ranking: loose enough to work it demoted half a document, tight enough to be
    # safe it caught nothing. This lifts the document's own statement of currency into
    # the prompt instead, where the ordering of evidence cannot bury it.
    "app/services/in_force.py",
    "app/services/web_search.py",
    "app/services/integration_auth.py",
    "app/services/integration_limits.py",
    "app/services/live_data.py",
    "app/api/routes/integrations.py",
    "app/schemas/integration.py",
    "app/models/integration.py",
    "app/exceptions.py",
    "app/main.py",
    # Added with the best-practice work: numbers written as words, relative dates, the
    # optional cross-encoder, monitoring and backups. Listed from the start so the next
    # change to any of them is recorded rather than discovered.
    #
    # What that work opened: the keyword index gained the digit form of values written
    # in words; the conflict detector reads them as quantities; temporal intent gained
    # a relative date window with the same weight as a named date; the answer pipeline
    # states that window and today's date in the prompt when — and only when — the
    # question names one, and records one monitoring row per request. The cross-encoder
    # is built and measured but off by default: on this hardware it cost 7–18 s a
    # question for a 2–4 % gain in section rank and no gain in evidence recall.
    #
    # What stayed shut: every FROZEN file, including the answer validator — the digits
    # it needs are appended to the evidence it is handed, not taught to it.
    # Colloquial forms, synonyms and compound questions. The analyser reads a standard
    # form of the question and a synonym list; the keyword arm searches synonyms at half
    # weight; ranking counts a synonym as covering its term; a question of two to four
    # parts is searched part by part and a part the answer omits is answered on its own.
    # The question the model reads is never rewritten.
    "app/services/query_rewrite.py",
    # A question that names a file is answered from that file; a name several files
    # share is answered with a question. Retrieval itself is unchanged: this only fills
    # the `document_ids` filter the request already carried.
    "app/services/document_scope.py",
    # Contact and access details masked in answers unless asked for, and overviews
    # answered with the essentials: no facts block and no completion pass for them.
    "app/services/sensitive.py",
    # A document deleted while it was being processed was still written into the
    # knowledge tables and the vector index afterwards. The pipeline now checks before
    # each write and takes back what it wrote; the vector store lists its documents so
    # startup can remove what deleted ones left behind. Retrieval and ranking unchanged.
    # Scanned PDFs gain the tables RAGFlow rebuilt cell by cell, kept only when their
    # numbers match our own reading and their letters are not misread Arabic. Documents
    # gained a project and a folder from folder uploads, and are identified by both
    # with the filename. Retrieval does not read either yet.
    "app/services/table_assist.py",
    "app/schemas/document.py",
    "app/api/routes/documents.py",
    "app/api/routes/chat.py",
    "app/api/routes/chat_stream.py",
    "app/core/number_words.py",
    "app/services/relative_dates.py",
    "app/services/cross_encoder.py",
    "app/services/metrics.py",
    "app/services/backup.py",
    "app/models/metrics.py",
    "app/api/routes/monitoring.py",
    "app/api/routes/health.py",
    "app/schemas/chat.py",
    "app/core/permissions.py",
]


def digest(relative: str) -> str:
    path = ROOT / relative
    if not path.exists():
        return "(missing)"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot() -> dict:
    return {
        "frozen": {f: digest(f) for f in FROZEN},
        "declared": {f: digest(f) for f in DECLARED},
    }


def save(label: str) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"checksums_{label}.json"
    out.write_text(json.dumps(snapshot(), indent=2), encoding="utf-8")
    print(f"{len(FROZEN)} frozen + {len(DECLARED)} declared file(s) recorded")
    print(f"saved → {out}")


def compare(label: str) -> int:
    path = RESULTS / f"checksums_{label}.json"
    if not path.exists():
        print(f"no snapshot named '{label}' — run --save first", file=sys.stderr)
        return 2

    before = json.loads(path.read_text(encoding="utf-8"))
    now = snapshot()

    violations = [
        f for f, value in now["frozen"].items() if before["frozen"].get(f) != value
    ]
    changed = [
        f for f, value in now["declared"].items() if before["declared"].get(f) != value
    ]

    print(f"compared against '{label}'\n")
    if changed:
        print("declared changes (expected):")
        for f in changed:
            print(f"  ~ {f}")
    else:
        print("declared: no changes")

    print()
    if violations:
        print(f"FROZEN FILES CHANGED — {len(violations)}:")
        for f in violations:
            print(f"  ! {f}")
        return 1

    print(f"FROZEN: all {len(FROZEN)} files unchanged.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--save", metavar="LABEL")
    parser.add_argument("--compare", metavar="LABEL")
    args = parser.parse_args()

    if args.save:
        save(args.save)
    elif args.compare:
        raise SystemExit(compare(args.compare))
    else:
        parser.error("pass --save LABEL or --compare LABEL")
