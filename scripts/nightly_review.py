"""The nightly pass: tidy what the day left behind, then write a report for the morning.

Run every night by the "RAG Nightly" task. Two jobs:

1. **Catch up on filing.** Documents that were never sorted into a folder — uploaded
   before the organizer existed, or while the model was busy — are sorted now, a few
   per night so the graphics card is free again by morning.
2. **Report.** What was asked and how much of it was answered, what the system learned
   on its own, what waits for a reviewer, what was uploaded and what failed. Written to
   data/reports/ and shown on the monitoring page.

Usage: python scripts/nightly_review.py [--hours 24] [--organize 20]
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.core.domain import TERMINAL_FAILURES  # noqa: E402
from app.models.database import session_scope  # noqa: E402
from app.models.document import Document  # noqa: E402
from app.models.knowledge import ChunkRecord  # noqa: E402
from app.models.knowledge_items import KnowledgeItem  # noqa: E402
from app.models.metrics import RequestMetric  # noqa: E402
from app.models.question_log import QuestionRecord  # noqa: E402
from app.services.metrics import REASON_LABELS  # noqa: E402
from app.services.organizer import DocumentOrganizer  # noqa: E402

REPORTS = ROOT / "data" / "reports"
TYPE_LABELS = {"fact": "معلومة", "correction": "تصحيح", "terminology": "مرادف", "preference": "تفضيل",
               "rule": "قاعدة", "procedure": "إجراء"}


def organize_backlog(limit: int) -> list[str]:
    """Sort up to `limit` documents that never were. Returns what was placed."""
    if limit <= 0:
        return []
    settings = get_settings()
    from app.services.llm import OllamaLLMClient

    llm = OllamaLLMClient(base_url=settings.ollama_base_url, model=settings.ollama_model,
                          temperature=0.0, num_ctx=8192, timeout=settings.ollama_timeout,
                          think=settings.ollama_think, keep_alive=settings.ollama_keep_alive)
    organizer = DocumentOrganizer(llm=llm, enabled=settings.auto_organize_enabled)
    with session_scope() as session:
        pending = [
            d.id for d in session.scalars(select(Document).where(Document.status == "completed"))
            if not (d.extra_metadata or {}).get("organized")
        ][:limit]
    placed = []
    for doc_id in pending:
        with session_scope() as session:
            text = "\n".join(session.scalars(
                select(ChunkRecord.content).where(ChunkRecord.document_id == doc_id)
                .order_by(ChunkRecord.chunk_index).limit(3)
            ))
        try:
            result = organizer.organize(doc_id, text)
        except Exception as exc:  # noqa: BLE001 - one document must not stop the night
            placed.append(f"- تعذّر تصنيف مستند ({type(exc).__name__})")
            continue
        if result is not None:
            with session_scope() as session:
                name = session.get(Document, doc_id).filename
            placed.append(f"- {name} ← {result.project or 'بدون مشروع'} / {result.kind}")
    return placed


def report(hours: int, organized: list[str]) -> str:
    since = datetime.now(UTC) - timedelta(hours=hours)
    with session_scope() as session:
        metrics = list(session.scalars(select(RequestMetric).where(RequestMetric.created_at >= since)))
        learned = list(session.scalars(select(KnowledgeItem).where(KnowledgeItem.created_at >= since)))
        waiting = session.scalar(select(KnowledgeItem.id).where(KnowledgeItem.status.in_(["pending", "in_review"])).limit(1))
        waiting_count = len(list(session.scalars(select(KnowledgeItem.id).where(KnowledgeItem.status.in_(["pending", "in_review"])))))
        uploads = list(session.scalars(select(Document).where(Document.uploaded_at >= since)))
        log = list(session.scalars(select(QuestionRecord).where(QuestionRecord.created_at >= since)))
        resolved_ids = [r.resolved_by for r in log if r.resolved_by]
        resolutions = {r.id: r for r in session.scalars(select(QuestionRecord).where(QuestionRecord.id.in_(resolved_ids)))} if resolved_ids else {}
        session.expunge_all()

    outcomes = Counter(m.outcome for m in metrics)
    total = len(metrics)
    answered = outcomes.get("answered", 0)
    times = [m.total_ms / 1000 for m in metrics if m.total_ms]
    reasons = Counter(m.refusal_reason for m in metrics if m.outcome == "refused")
    users = len({m.user_id for m in metrics if m.user_id})
    remembered = sum(1 for m in metrics if (m.stages or {}).get("remembered"))

    personal = [k for k in learned if str(k.scope) == "user" and str(k.status) == "active"]
    from_rephrasing = [k for k in personal if "learned-from-rephrasing" in (k.tags or [])]
    by_type = Counter(TYPE_LABELS.get(str(k.type), str(k.type)) for k in personal)
    failed = [d for d in uploads if d.status in {str(s) for s in TERMINAL_FAILURES} or str(d.status).startswith("failed")]

    lines = [f"# تقرير الليلة — {datetime.now().strftime('%Y-%m-%d')}", "", f"آخر {hours} ساعة.", "", "## الأسئلة", ""]
    if total:
        lines += [
            f"- **{total}** سؤالًا من **{users}** مستخدمًا.",
            f"- أُجيب **{answered}** ({answered * 100 // total}%)، ورُفض {outcomes.get('refused', 0)}، "
            f"واقتُرح تفسير في {outcomes.get('clarified', 0)}، وأخطاء {outcomes.get('error', 0)}.",
        ]
        if times:
            lines.append(f"- زمن الإجابة: الوسيط {statistics.median(times):.0f} ث، والأطول {max(times):.0f} ث.")
        if remembered:
            lines.append(f"- {remembered} سؤالًا متكررًا أُجيب من الذاكرة فورًا.")
        if reasons:
            lines += ["", "**أسباب الرفض:**"]
            lines += [f"- {REASON_LABELS.get(r, r or 'غير محدد')}: {n}" for r, n in reasons.most_common(5)]
        missed = [m for m in metrics if m.outcome != "answered" and (m.question or "").strip()]
        if missed:
            lines += ["", f"**أسئلة لم يُجب عنها ({len(missed)}) — راجعها لتعرف ما ينقص:**"]
            lines += [f"- «{m.question.strip()[:140]}»" for m in missed[:10]]
    else:
        lines.append("- لم تُطرح أسئلة.")

    lines += ["", "## ما تعلّمه النظام وحده", ""]
    if personal:
        lines.append(f"- **{len(personal)}** عنصرًا شخصيًا نُفّذ فورًا لأصحابه: "
                     + "، ".join(f"{label} {n}" for label, n in by_type.most_common()) + ".")
        if from_rephrasing:
            lines.append(f"- منها **{len(from_rephrasing)}** مرادفًا تعلّمه من إعادة صياغة الأسئلة.")
    else:
        lines.append("- لا جديد.")
    follow_ups = sum(1 for r in log if r.subject)
    wordings = [(r, resolutions.get(r.resolved_by)) for r in log if r.resolved_by]
    disliked = [r for r in log if r.feedback == "down"]
    if follow_ups:
        lines.append(f"- **{follow_ups}** سؤال متابعة فُهم في سياق السؤال الذي قبله.")
    if wordings:
        lines.append(f"- **{len(wordings)}** صياغة فشلت ثم نجحت بكلمات أخرى — تُبحث بالثانية من الآن:")
        lines += [f"  - «{a.question[:80]}» ← «{b.question[:80]}»" for a, b in wordings[:10] if b is not None]
    if disliked:
        lines += ["", f"**إجابات قيّمها أصحابها 👎 ({len(disliked)}) — لن تُعاد من الذاكرة، وتستحق مراجعة:**"]
        lines += [f"- «{r.question[:120]}»" for r in disliked[:10]]
    lines.append(f"- بانتظار مراجعتك (معرفة مشتركة): **{waiting_count}**." if waiting else "- لا شيء بانتظار المراجعة.")

    lines += ["", "## المستندات", ""]
    lines.append(f"- رُفع **{len(uploads)}** مستندًا، وتعذّرت معالجة **{len(failed)}**." if uploads else "- لم يُرفع شيء.")
    lines += [f"  - ⚠️ {d.filename}: {(d.error_message or '')[:120]}" for d in failed[:10]]
    if organized:
        lines += ["", f"**صُنّف الليلة {len(organized)} مستندًا لم يكن مصنّفًا:**", *organized]
    return "\n".join(lines) + "\n"


def main() -> int:
    # Run by a scheduled task whose output is a log file: UTF-8, or the Arabic report
    # fails to print on a Windows console code page.
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--organize", type=int, default=20, help="documents to sort at most")
    args = parser.parse_args()
    try:
        organized = organize_backlog(args.organize)
    except Exception as exc:  # noqa: BLE001 - the report is still worth writing
        organized = [f"- تعذّر تشغيل التصنيف: {exc}"]
    text = report(args.hours, organized)
    try:
        from app.services.file_export import KEEP_DAYS, FileStore

        removed = FileStore(ROOT / "data" / "exports").remove_older_than(KEEP_DAYS)
        if removed:
            text += f"\n- حُذف {removed} ملفًا أُنشئ في المحادثات قبل أكثر من {KEEP_DAYS} أيام.\n"
    except Exception:  # noqa: BLE001 - kept another night rather than failing the report
        pass
    try:
        from app.config import get_settings
        from app.container import Container
        from app.services.index_health import index_health

        text += "\n\n" + "\n".join(index_health(Container(get_settings()))) + "\n"
    except Exception as exc:  # noqa: BLE001 - the rest of the report still stands
        text += f"\n## صحة الفهرسة\n\n- تعذّر الفحص: {exc}\n"
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"report-{datetime.now():%Y-%m-%d}.md").write_text(text, encoding="utf-8")
    (REPORTS / "latest.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
