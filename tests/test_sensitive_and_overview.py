"""Contact and access details withheld unless asked for; overviews kept to the essentials.

Asked about a case file, an answer listed a mobile number, a personal e-mail address and
the passcode of a video hearing, in ten sections and seventy lines. Two things fix it:
masking what the question did not ask for, and answering a request for an overview
with the state of things rather than everything the evidence holds.

The traps matter as much as the catches: a contract number, a complaint reference, a
date or an amount must never be taken for a phone number and masked.

Deterministic and offline.

Run: python tests/test_sensitive_and_overview.py
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from app.schemas.chat import AnswerValidation, ChatResponse  # noqa: E402
from app.services.query_analysis import QueryAnalyzer  # noqa: E402
from app.services.rag_service import OVERVIEW_DIRECTIVE, RagService  # noqa: E402
from app.services.sensitive import MASK_AR, redact  # noqa: E402

FAILURES: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        if detail:
            print(f"        {detail}")
        FAILURES.append(label)


ANSWER = """الخبير المنتدب: رقم القيد 134، الهاتف 0501234567، البريد expert@example.ae.
الاجتماع عبر Zoom (Meeting ID: 812 3456 7890, Passcode: X9Q2LP).
رقم العقد B1N-2023-004410-P01 بتاريخ 09/10/2024 بقيمة 1,450,000 درهم.
الشكوى رقم 310101-1234567، والطلب 1-10000000001، والهوية 784-1990-1234567-1.
الحساب AE07 0331 2345 6789 0123 456 — هاتف المكتب +971 2 123 4567."""


def contact_details_are_withheld() -> None:
    print("\n-- 1. what a summary question does not get --")
    result = redact(ANSWER, "احكيلي عن ملف القضية")
    for gone in ("0501234567", "expert@example.ae", "X9Q2LP", "812 3456 7890",
                 "784-1990-1234567-1", "AE07 0331", "+971 2 123 4567"):
        check(gone not in result.text, f"withheld: {gone}")
    check("Passcode: " + MASK_AR in result.text, "the label stays, only the value is masked")
    check(set(result.masked) == {"phone", "email", "passcode", "meeting_id", "emirates_id", "iban"},
          "every kind is counted", str(result.masked))


def facts_are_never_mistaken_for_contacts() -> None:
    print("\n-- 2. what must survive: identifiers, references, dates, amounts --")
    result = redact(ANSWER, "احكيلي عن ملف القضية")
    for kept in ("134", "B1N-2023-004410-P01", "09/10/2024", "1,450,000", "310101-1234567",
                 "1-10000000001"):
        check(kept in result.text, f"kept: {kept}")
    quiet = redact("قيمة العقد 2,576,923.08 درهم والمدة 540 يومًا، بتاريخ 13/06/2026.", "ما قيمة العقد؟")
    check(quiet.masked == {} and "2,576,923.08" in quiet.text, "an answer with no contacts is untouched")


def asking_releases_the_detail() -> None:
    print("\n-- 3. asked for, it is given --")
    cases = {
        "ما رقم هاتف الخبير؟": ("0501234567", "phone"),
        "ما البريد الإلكتروني للخبير؟": ("expert@example.ae", "email"),
        "ما بيانات الاجتماع وكلمة السر؟": ("X9Q2LP", "passcode"),
        "من هو مالك المشروع وما رقم هويته؟": ("784-1990-1234567-1", "emirates_id"),
        "What is the expert's phone number?": ("0501234567", "phone"),
    }
    for question, (value, kind) in cases.items():
        result = redact(ANSWER, question)
        check(value in result.text and kind not in result.masked, f"{kind} given for: {question}")
    only_phone = redact(ANSWER, "ما رقم هاتف الخبير؟")
    check("expert@example.ae" not in only_phone.text, "asking for one kind releases only that kind")


def the_pipeline_masks_every_answer() -> None:
    print("\n-- 4. the pipeline masks once, after every path, and says so --")
    service = RagService.__new__(RagService)
    service.redact_sensitive = True
    response = ChatResponse(answer=ANSWER, grounded=True, validation=AnswerValidation(complete=True))
    service._withhold_sensitive("احكيلي عن الملف", response)
    check("0501234567" not in response.answer, "the answer is masked")
    check(any(r.startswith("phone:") for r in response.redacted), "and records what", str(response.redacted))
    check(any("حُجبت" in w for w in response.validation.warnings), "and tells the reader")

    service.redact_sensitive = False
    response = ChatResponse(answer=ANSWER, grounded=True)
    service._withhold_sensitive("احكيلي عن الملف", response)
    check("0501234567" in response.answer, "and can be switched off")


def overviews_are_recognised() -> None:
    print("\n-- 5. which questions ask for an overview --")
    analyzer = QueryAnalyzer()
    for question in ("احكيلي عن ملف القضية", "لخص لي الملف", "أعطني ملخص عن المشروع",
                     "Tell me about the PROJECT_SUMMARY.md document", "معلومات عن هذا الملف"):
        check(analyzer.analyze(question).overview, f"overview: {question}")
    for question in ("ما قيمة الدفعات المستلمة في ملخص المدفوعات؟",  # a file called "summary"
                     "PROJECT MIGRATION SUMMARY ما آخر مرحلة؟",        # a name containing "summary"
                     "اشرح معادلة غرامة التأخير",                      # wants the whole explanation
                     "اذكر كل البنود المحذوفة من الملف",               # an enumeration
                     "ما السقف الأقصى لغرامة التأخير؟"):
        check(not analyzer.analyze(question).overview, f"not an overview: {question}")


def an_overview_is_asked_for_the_essentials() -> None:
    print("\n-- 6. the prompt for an overview --")
    overview = QueryAnalyzer().analyze("احكيلي عن ملف القضية")
    directive = RagService._mode_directive(overview)
    check(OVERVIEW_DIRECTIVE in directive, "the overview directive is used")
    check("150 كلمة" in directive and "لا تُنشئ قسمًا يجمع أرقامًا" in directive,
          "short, and no section collecting unrelated figures")
    plain = QueryAnalyzer().analyze("ما السقف الأقصى لغرامة التأخير؟")
    check(OVERVIEW_DIRECTIVE not in RagService._mode_directive(plain), "a factual question does not get it")


def the_language_ignores_file_names() -> None:
    print("\n-- 7. the answer's language is the question's, not its file name's --")
    from app.services.query_analysis import language_of

    arabic = "اكتب الإجابة بالعربية"
    for question, wanted in (
        ("احكيلي عن ملف PROJECT_MIGRATION_SUMMARY.md", "ar"),
        ("ما رقم العقد B1N-2023-004410-P01؟", "ar"),
        ("حسب FIDIC ما مهلة الإخطار؟", "ar"),
        ("Tell me about PROJECT_SUMMARY.md", "en"),
        ("What is the maximum unsupported excavation depth?", "en"),
    ):
        check(language_of(question) == wanted, f"{wanted}: {question}")
        directive = RagService._language_directive(question)
        check((arabic in directive) == (wanted == "ar"), f"  and the directive agrees", directive[:40])


def main() -> None:
    print("sensitive details and overviews")
    the_language_ignores_file_names()
    contact_details_are_withheld()
    facts_are_never_mistaken_for_contacts()
    asking_releases_the_detail()
    the_pipeline_masks_every_answer()
    overviews_are_recognised()
    an_overview_is_asked_for_the_essentials()
    print("\n" + "=" * 64)
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("contact details are withheld unless asked for, and overviews stay short")


if __name__ == "__main__":
    main()
