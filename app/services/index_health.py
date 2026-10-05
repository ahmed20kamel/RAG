"""Whether every document can actually be found — checked, not assumed.

Used by the nightly report and by the monitoring page, which asks on demand.
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.models.database import session_scope
from app.models.document import Document
from app.models.knowledge import ChunkRecord as Chunk


def index_health(container) -> list[str]:
    """Whether every document is fully searchable: processed, cut into passages, and
    each passage indexed for meaning (vectors) and for words (keyword index).

    A document that fails any of these is one whose questions get partial answers or
    none, and nothing else would say so.
    """
    with session_scope() as session:
        documents = list(session.scalars(select(Document)))
        counts = dict(session.execute(
            select(Chunk.document_id, func.count()).group_by(Chunk.document_id)
        ).all())
        session.expunge_all()
    lines = ["## صحة الفهرسة", ""]
    unfinished = [d for d in documents if d.status != "completed"]
    empty = [d for d in documents if d.status == "completed" and not counts.get(d.id)]
    mismatched = []
    vectors_reachable = container.vector_store.health().get("reachable")
    if vectors_reachable:
        for d in documents:
            if d.status == "completed" and counts.get(d.id):
                points = container.vector_store.count_document(d.id)
                if points != counts[d.id]:
                    mismatched.append((d.filename, counts[d.id], points))
    container.keyword_index.ensure_loaded()
    indexed = container.keyword_index.stats().get("chunks", 0)
    total = sum(counts.values())
    healthy = not (unfinished or empty or mismatched) and vectors_reachable and indexed == total
    lines.append(f"- {len(documents)} مستندًا، {total} مقطعًا؛ فهرس الكلمات: {indexed}."
                 + (" ✅ كل شيء مفهرس." if healthy else ""))
    if not vectors_reachable:
        lines.append("- ⚠️ قاعدة المتجهات لا تستجيب — لم يُفحص فهرس المعنى.")
    if indexed != total:
        lines.append(f"- ⚠️ فهرس الكلمات يغطي {indexed} من {total} مقطعًا.")
    lines += [f"- ⚠️ لم تكتمل معالجته ({d.status}): {d.filename}" for d in unfinished[:15]]
    lines += [f"- ⚠️ مكتمل بلا مقاطع: {d.filename}" for d in empty[:15]]
    lines += [f"- ⚠️ مقاطعه {n} ومتجهاته {p}: {name}" for name, n, p in mismatched[:15]]
    return lines
