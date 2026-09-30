"""Completes an answer that provably left a requirement unmet.

It never re-asks the original question. It hands the model its own answer, the specific
requirements still open and the exact evidence for them, and asks for those additions
only — so a completion can add what was missed but cannot introduce anything the
evidence does not state, and cannot quietly drop what was already right.
"""

from __future__ import annotations

import logging

from app.core.contract import AnswerContract
from app.core.evidence import EvidenceItem
from app.services.coverage import CoverageResult
from app.services.llm import OllamaLLMClient

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """أنت تُكمل إجابة ناقصة، ولا تُعيد كتابتها.

القواعد:
1. أضف فقط العناصر المطلوبة المذكورة أدناه، بالاعتماد على الأدلة المرفقة وحدها.
2. لا تحذف أي معلومة صحيحة وردت في الإجابة الحالية، ولا تغيّر صياغتها ولا معناها.
3. انقل الأسماء والأرقام والتواريخ كما وردت في الأدلة حرفيًا.
4. أرفق بكل عنصر تضيفه رقم مصدره بين قوسين.
5. لا تضف شيئًا غير موجود في الأدلة، ولو بدا مكملًا للمعنى.
6. أعد الإجابة كاملة بعد الإضافة، بنفس لغة الإجابة الحالية، دون أي شرح لما فعلته."""

USER_TEMPLATE = """السؤال الأصلي: {question}

الإجابة الحالية:
{answer}

ما زال ناقصًا — أضف هذه العناصر:
{missing}

الأدلة المؤيدة لها:
{evidence}

المصادر الكاملة:
{context}

أعد الإجابة كاملة متضمنة ما سبق."""


class CompletionEngine:
    def __init__(self, llm: OllamaLLMClient, max_passes: int = 1) -> None:
        self.llm = llm
        self.max_passes = max_passes

    def complete(
        self,
        contract: AnswerContract,
        coverage: CoverageResult,
        answer: str,
        extra: list[EvidenceItem] | None = None,
        context: str = "",
    ) -> str | None:
        """A fuller answer, or None when there is nothing to add.

        ``extra`` carries omissions found by the answer validator rather than by the
        contract — evidence the question's own scope says belongs in the answer even
        though no single requirement names it.

        The sources are sent alongside the gap list. Extracted values are only what the
        fact sheet could name; a value that lives in a source and nowhere else is
        invisible to the gap list, and a completion that cannot read the sources can
        neither find it nor cite anything it adds. What keeps the pass targeted is the
        gap list and the caller's rule that a replacement may only add, never lose.
        """
        seen = set()
        missing: list[EvidenceItem] = []
        for item in [*coverage.missing, *(extra or [])]:
            if item.dedupe_key in seen:
                continue
            seen.add(item.dedupe_key)
            missing.append(item)
        if not missing or self.max_passes < 1:
            return None

        logger.info(
            "Targeted completion for %s unmet requirement(s) and %s omitted value(s)",
            len(coverage.unmet), len(missing) - len(coverage.missing),
        )
        requirements = "\n".join(
            f"- {entry.requirement.part} ({entry.requirement.kind})"
            for entry in coverage.unmet
        ) or f"- {contract.question}"
        evidence = "\n".join(f"- {item.describe()}" for item in missing)

        return self.llm.chat(
            SYSTEM_PROMPT,
            USER_TEMPLATE.format(
                question=contract.question,
                answer=answer.strip(),
                missing=requirements,
                evidence=evidence,
                context=context.strip() or "(لا تتوفر مصادر إضافية)",
            ),
        )
