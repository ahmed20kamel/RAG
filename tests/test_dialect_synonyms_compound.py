"""Colloquial questions, synonyms, learned terms, and questions of several parts.

Each mechanism is tested on its own traps first — the Arabic that looks like what it
handles and is not — and then where it changes behaviour: the analyser, the keyword
search, ranking, and the answer pipeline. The pipeline pieces are exercised with fakes,
so this suite needs no database, model or network.

Run: python tests/test_dialect_synonyms_compound.py
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from app.core.retrieval import Candidate  # noqa: E402
from app.core.text import normalize  # noqa: E402
from app.services.answer_validation import AnswerValidator  # noqa: E402
from app.services.keyword_index import KeywordIndex  # noqa: E402
from app.services.query_analysis import Intent, QueryAnalyzer  # noqa: E402
from app.services.query_rewrite import (  # noqa: E402
    SynonymLexicon, canonicalize, interpretation_offered, parse_term, term_statement,
)
from app.services.rag_service import (  # noqa: E402
    INSUFFICIENT_ANSWER, PART_MARKER, PART_NOT_FOUND, RagService,
)
from app.services.reranking import FeatureReranker  # noqa: E402

FAILURES: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        if detail:
            print(f"        {detail}")
        FAILURES.append(label)


# ---------------------------------------------------------------------------
# 1. Dialect
# ---------------------------------------------------------------------------


def dialect_is_mapped() -> None:
    print("\n-- 1. colloquial forms become standard, for analysis and search --")
    cases = {
        "كام بندفع عن كل يوم تأخير؟": "كم ندفع عن كل يوم تاخير",
        "إمتى اتمضى العقد؟": "متي وقع العقد",
        "مين المهندس المسؤول؟": "من المهندس المسوول",  # ؤ folds to و in normalisation
        "فين الملف دلوقتي؟": "اين الملف الان",
        "ليه اتأخر التسليم؟": "لماذا اتاخر التسليم",
        "الغرامة هتبقى قد إيه؟": "الغرامه ستكون كم",
    }
    for question, wanted in cases.items():
        got = canonicalize(question).text
        check(got == wanted, f"{question} → {wanted}", f"got {got!r}")


def dialect_traps() -> None:
    print("\n-- 2. words that look colloquial and are not --")
    for text in (
        "تسوية ودية بين الطرفين وحل ودي",   # "دي" inside "ودي"
        "ما الذي أتوقعه من الجلسة؟",        # "أتوقع" is "I expect", not "was signed"
        "ما بنود العقد؟",                   # "بنود" is not the progressive prefix
        "ما بيان الأعمال المنجزة؟",         # nor is "بيان"
        "أرسل الكتاب إلى المقاول",          # "إلى" normalises to "الي" and stays
    ):
        result = canonicalize(text)
        check(not result.changed, f"untouched: {text}", str(result.changes))


def analysis_reads_the_standard_form() -> None:
    print("\n-- 3. the analyser reads the standard form, the model still sees the question --")
    analyzer = QueryAnalyzer()
    analysis = analyzer.analyze("إمتى اتمضى العقد؟")
    check(analysis.intent is Intent.DATE, f"'إمتى' is a date question ({analysis.intent})")
    check(analysis.question == "إمتى اتمضى العقد؟", "the question itself is kept as asked")
    check(analysis.rewritten and analysis.embedding_text == "متى وُقّع العقد؟",
          "and the semantic arm embeds the standard form, correctly spelled",
          repr(analysis.embedding_text))
    check("امتي" not in analysis.keywords and "كام" not in analysis.keywords,
          "colloquial question words are not keywords", str(analysis.keywords))

    plain = analyzer.analyze("ما السقف الأقصى لغرامة التأخير؟")
    check(not plain.rewritten and plain.embedding_text == plain.question,
          "a standard question is embedded exactly as asked")


# ---------------------------------------------------------------------------
# 2. Synonyms and learned terms
# ---------------------------------------------------------------------------


def synonyms_expand() -> None:
    print("\n-- 4. synonyms expand the search --")
    lexicon = SynonymLexicon()
    expansion = lexicon.expand(canonicalize("كم مترًا من الجدار سقط؟").text)
    check("سور" in expansion.terms, "الجدار reaches السور", str(expansion.terms))
    check("جدار" not in expansion.terms, "and the word asked is not repeated as its own synonym")
    check("سور" in expansion.alternatives.get("جدار", ()), "ranking may count سور for جدار")

    quiet = lexicon.expand(canonicalize("ما رقم الهاتف المسجل؟").text)
    check(quiet.terms == [], "a question touching no group gains nothing", str(quiet.terms))


def learned_terms_join_the_lexicon() -> None:
    print("\n-- 5. an approved term becomes a synonym group --")
    lexicon = SynonymLexicon(learned=lambda: [("اختبار", "معاينه")], refresh_seconds=0)
    expansion = lexicon.expand(canonicalize("بأي تاريخ عقد محضر اختبار الموقع؟").text)
    check("معاينه" in expansion.terms, "the learned wording is searched", str(expansion.terms))

    broken = SynonymLexicon(learned=lambda: (_ for _ in ()).throw(RuntimeError("db down")),
                            refresh_seconds=0)
    check(broken.expand("الجدار").terms == ["سور", "حايط"],
          "a lexicon that cannot load its learned half keeps its built-in half")


def terms_are_parsed_conservatively() -> None:
    print("\n-- 6. what counts as a term, and what is a definition --")
    check(parse_term(term_statement("اختبار", "معاينة")) == ("اختبار", "معاينه"),
          "the recorded form reads back")
    check(parse_term("يقصد بالمعاينة في مشروعنا زيارة الموقع وليس الاجتماع")
          == ("معاينه", "زياره الموقع"), "«يقصد بـ X Y» is read")
    check(parse_term("المستخلص = الدفعة الشهرية") == ("المستخلص", "الدفعه الشهريه"),
          "«X = Y» is read")
    for definition in (
        "المقاول هو المسؤول عن التنفيذ",
        "المحتجزات هي نسبة تُستقطع من كل دفعة مستحقة للمقاول وتُفرج عنها عند انتهاء الضمان",
        "اعتمد دائمًا صيغة الجداول في الإجابات",
    ):
        check(parse_term(definition) is None, f"not a synonym: {definition[:40]}")


def an_interpretation_is_read_back() -> None:
    print("\n-- 7. the wording a clarification offered --")
    answer = ("اللفظ الوارد في سؤالك غير مستخدم في المستندات. إن كنت تقصد محضر المعاينة "
              "الميدانية، فالإجابة: 22/07/2026 [7]")
    check(interpretation_offered(answer) == "محضر المعاينه الميدانيه", "the interpretation is extracted")
    check(interpretation_offered("قيمة العقد 1,450,000 درهم [1].") is None,
          "an ordinary answer offers none")


# ---------------------------------------------------------------------------
# 3. Search and ranking
# ---------------------------------------------------------------------------


class _Row:
    def __init__(self, section: str, content: str) -> None:
        self.document_title, self.section, self.heading = "ملف", section, section
        self.content = content


def keyword_search_weighs_synonyms() -> None:
    print("\n-- 8. keyword search: a synonym reaches, the word asked still wins --")
    index = KeywordIndex()
    index.build_from([
        ("sur", "d", KeywordIndex._document_text(_Row("السور", "طول السور المنهار 35 مترًا"))),
        ("jidar", "d", KeywordIndex._document_text(_Row("الجدار", "طول الجدار الداخلي 12 مترًا"))),
        ("other", "d", KeywordIndex._document_text(_Row("الموقع", "مساحة الموقع 900 متر مربع"))),
    ])
    plain = [c for c, _ in index.search("الجدار")]
    check("sur" not in plain, "without expansion the synonym is unreachable", str(plain))
    expanded = [c for c, _ in index.search("الجدار", expansions=("سور",))]
    check("sur" in expanded, "with it, it is reached", str(expanded))
    check(expanded and expanded[0] == "jidar", "and the passage using the word asked ranks first",
          str(expanded))
    check(index.unknown_terms("محضر اختبار الموقع") == ["محضر", "اختبار"],
          "words no passage contains are reported", str(index.unknown_terms("محضر اختبار الموقع")))


def ranking_counts_synonyms() -> None:
    print("\n-- 9. ranking: a synonym covers the term it stands for --")
    analysis = QueryAnalyzer().analyze("ما طول الجدار المنهار؟")

    def candidate(chunk_id: str, content: str) -> Candidate:
        return Candidate(
            chunk_id=chunk_id, document_id="d", document_title="ملف", filename="f.md",
            category="General", section="ملف → الأضرار", section_id=chunk_id,
            heading="الأضرار", parent_section="", content=content,
            vector_score=0.5, fused_score=0.02,
        )

    ranked = FeatureReranker().rerank(analysis, [
        candidate("unrelated", "المنهار من المعدات لا يُحتسب في المطالبة."),
        candidate("synonym", "طول السور المنهار 35 مترًا."),
    ], limit=2)
    check(ranked[0].chunk_id == "synonym", "the passage saying السور outranks mere word overlap",
          str([(c.chunk_id, round(c.rerank_score, 3)) for c in ranked]))


# ---------------------------------------------------------------------------
# 4. Compound questions
# ---------------------------------------------------------------------------


def parts_are_split_in_the_askers_words() -> None:
    print("\n-- 10. parts, standard and colloquial, kept in the asker's words --")
    analyzer = QueryAnalyzer()
    cases = {
        "إمتى اتمضى العقد وإمتى بدأ الشغل؟": ["إمتى اتمضى العقد", "وإمتى بدأ الشغل"],
        "ما رقم الرخصة، وما تاريخ صدورها؟": ["ما رقم الرخصة", "وما تاريخ صدورها"],
        "مين المالك وكمان رقم هويته إيه؟": ["مين المالك", "وكمان رقم هويته إيه"],
        "ما قيمة الغرامة اليومية؟": ["ما قيمة الغرامة اليومية؟"],
    }
    for question, wanted in cases.items():
        analysis = analyzer.analyze(question)
        check(analysis.parts_display == wanted, f"{question}", f"got {analysis.parts_display}")
    check(analyzer.analyze("إمتى اتمضى العقد وإمتى بدأ الشغل؟").parts
          == ["متي وقع العقد", "ومتي بدا الاعمال"], "each part also has its standard form")


def the_prompt_lists_the_parts() -> None:
    print("\n-- 11. the prompt lists the parts and asks for each --")
    analysis = QueryAnalyzer().analyze("ما رقم الرخصة، وما تاريخ صدورها؟")
    directive = RagService._mode_directive(analysis)
    check("1) ما رقم الرخصة" in directive and "2) وما تاريخ صدورها" in directive,
          "both parts are numbered", directive[:160])
    check("لا تُسقط أي جزء" in directive, "and none may be dropped")
    single = QueryAnalyzer().analyze("ما قيمة الغرامة اليومية؟")
    check("أجزاء" not in RagService._mode_directive(single) and
          "عدة أجزاء" not in RagService._mode_directive(single),
          "a single question gets no parts directive")


def part_markers() -> None:
    print("\n-- 12. recognising a numbered part in an answer --")
    for text in ("1) الرقم هو X\n2) التاريخ Y", "**2)** التاريخ", "مقدمة\n2. التاريخ", "2 - التاريخ"):
        check(bool(PART_MARKER(2).search(text)), f"part 2 found in {text!r}")
    check(not PART_MARKER(2).search("بلغت القيمة 2,000 درهم"), "a number inside a sentence is not a marker")


class _FakeLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[str] = []
        self.model = "fake"

    def chat(self, system: str, user: str) -> str:
        self.calls.append(user)
        return self.reply


def _service(llm, retriever=None) -> RagService:
    service = RagService.__new__(RagService)
    service.llm = llm
    service.validator = AnswerValidator()
    service.analyzer = QueryAnalyzer()
    service.retriever = retriever
    service.context_builder = SimpleNamespace(primary_count=3)
    return service


def missing_parts_are_answered_separately() -> None:
    print("\n-- 13. a part the answer left out is answered on its own --")
    analysis = QueryAnalyzer().analyze("ما رقم رخصة البناء، وما تاريخ انتهاء الضمان؟")
    llm = _FakeLLM("ينتهي الضمان في 15/03/2027 [2].")
    service = _service(llm)
    answer, added = service._complete_parts(analysis, "1) رقم رخصة البناء B-100 [1].", "[1] … [2] …")
    check(added == 1 and len(llm.calls) == 1, f"one separate generation ({added})")
    check("**2) وما تاريخ انتهاء الضمان**" in answer and "15/03/2027" in answer,
          "appended under its number", answer[-120:])

    complete = "1) رقم رخصة البناء B-100 [1].\n2) ينتهي الضمان في 15/03/2027 [2]."
    llm = _FakeLLM("لا ينبغي أن يُستدعى")
    answer, added = _service(llm)._complete_parts(analysis, complete, "")
    check(added == 0 and not llm.calls and answer == complete, "a complete answer is left alone")

    llm = _FakeLLM(INSUFFICIENT_ANSWER)
    answer, added = _service(llm)._complete_parts(analysis, "1) رقم رخصة البناء B-100 [1].", "")
    check(PART_NOT_FOUND in answer, "a part the evidence cannot answer says so under its number")


class _FakeRetriever:
    """Returns a different passage per part, keyed on a word in the part."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def retrieve(self, analysis, top_k=None, category=None, document_ids=None):
        self.queries.append(analysis.question)
        key = "licence" if "رخصه" in analysis.search_text else "warranty"
        return [SimpleNamespace(chunk_id=f"{key}-{i}") for i in range(3)]


def each_part_brings_evidence() -> None:
    print("\n-- 14. each part is searched, and its evidence placed where the budget keeps it --")
    analysis = QueryAnalyzer().analyze("ما رقم رخصة البناء، وما تاريخ انتهاء الضمان؟")
    retriever = _FakeRetriever()
    service = _service(_FakeLLM(""), retriever)
    whole = [SimpleNamespace(chunk_id=f"whole-{i}") for i in range(6)]
    merged = service._with_part_evidence(analysis, whole, SimpleNamespace(category=None, document_ids=None))
    ids = [c.chunk_id for c in merged]
    check(len(retriever.queries) == 2, f"two part searches ({len(retriever.queries)})")
    check(ids[:3] == ["whole-0", "whole-1", "whole-2"], "the leading evidence stays first")
    check({"licence-0", "warranty-0"} <= set(ids[3:7]), "each part's best passage follows it", str(ids))

    short = QueryAnalyzer().analyze("ما رقم رخصة الشركة وما رقم رخصة الجار ومتى صدرت كل منهما؟")
    retriever = _FakeRetriever()
    _service(_FakeLLM(""), retriever)._with_part_evidence(
        short, [], SimpleNamespace(category=None, document_ids=None))
    check(any("رخصه" in normalize(q) and "صدرت" in q for q in retriever.queries),
          "a part too short to search alone is searched with the part before it", str(retriever.queries))
    check(all(not q.startswith("و") for q in retriever.queries),
          "and each part is searched without its joining «و»", str(retriever.queries))
    check(any("رخصة" in q for q in retriever.queries),
          "in the asker's spelling, not the normalised form", str(retriever.queries))


def main() -> None:
    print("dialect, synonyms and compound questions")
    dialect_is_mapped()
    dialect_traps()
    analysis_reads_the_standard_form()
    synonyms_expand()
    learned_terms_join_the_lexicon()
    terms_are_parsed_conservatively()
    an_interpretation_is_read_back()
    keyword_search_weighs_synonyms()
    ranking_counts_synonyms()
    parts_are_split_in_the_askers_words()
    the_prompt_lists_the_parts()
    part_markers()
    missing_parts_are_answered_separately()
    each_part_brings_evidence()

    print("\n" + "=" * 64)
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("colloquial questions, synonyms and compound questions are handled")


if __name__ == "__main__":
    main()
