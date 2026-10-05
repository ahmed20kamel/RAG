"""One-time repair of what two early bugs left behind. Safe to run again.

1. **Documents without an owner.** For about an hour on 2026-10-04 the server ran a model
   that had no owner column, so documents uploaded then were saved with none: invisible
   to the person who uploaded them, visible only to administrators. The server's own log
   records every upload as "Upload by <email>: <filename>"; a document is given back to
   the person the log names for that filename at that time. A document the log says
   nothing about is left as it is and listed — an owner is never guessed.

2. **Synonyms learned under the first, loose rules.** Rephrase learning paired words from
   questions that were not rephrasings of each other ("واجمالي ≈ بانواع", "whem ≈
   geralyn"). Every synonym learned that way before the stricter rules is archived — not
   deleted — and is learned again, correctly, if the person rephrases again. Synonyms a
   person taught deliberately are not touched.

Prints what it would do. With --apply, does it and writes data/reports/legacy_fix.md.

Run: python scripts/fix_legacy_data.py [--apply]
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select  # noqa: E402

from app.core.knowledge import KnowledgeScope, KnowledgeStatus, KnowledgeType  # noqa: E402
from app.models.auth import User  # noqa: E402
from app.models.database import session_scope  # noqa: E402
from app.models.document import Document  # noqa: E402
from app.models.knowledge_items import KnowledgeItem, KnowledgeVersion  # noqa: E402

LOGS = ROOT / "data" / "logs"
REPORT = ROOT / "data" / "reports" / "legacy_fix.md"
#: Owners were introduced on this day; anything older is from before owners and stays
#: an administrators' document by design.
OWNERS_SINCE = datetime(2026, 10, 4, tzinfo=UTC)
#: The stricter rephrase rules went live after this moment.
STRICT_RULES_FROM = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
#: How far a log line's time may be from the document's upload time.
MATCH_WINDOW = timedelta(minutes=15)

UPLOAD_LINE = re.compile(
    r"^(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| INFO\s+\| [\w.]+ \| Upload by (?P<email>\S+): (?P<name>.+?)\s*$"
)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def uploads_in_logs() -> list[tuple[datetime, str, str]]:
    """(time in UTC, email, filename) for every upload any log file recorded.

    Log times are this machine's local time, which is what the server wrote them in.
    """
    found: list[tuple[datetime, str, str]] = []
    for path in sorted(LOGS.glob("*.log*")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            match = UPLOAD_LINE.match(line)
            if match is None:
                continue
            local = datetime.strptime(match["time"], "%Y-%m-%d %H:%M:%S").astimezone()
            found.append((local.astimezone(UTC), match["email"], match["name"]))
    return found


def owners(apply: bool) -> list[str]:
    lines: list[str] = []
    uploads = uploads_in_logs()
    with session_scope() as db:
        users = {u.email: u.id for u in db.scalars(select(User))}
        orphans = list(db.scalars(
            select(Document).where(Document.owner_id.is_(None), Document.uploaded_at >= OWNERS_SINCE)
        ))
        for document in orphans:
            uploaded = _as_utc(document.uploaded_at)
            matches = [
                (abs(when - uploaded), email) for when, email, name in uploads
                if name == document.filename and abs(when - uploaded) <= MATCH_WINDOW
            ]
            emails = {email for _, email in matches}
            if len(emails) != 1:
                why = "لا ذكر له في السجل" if not emails else f"السجل يذكر أكثر من رافع: {', '.join(sorted(emails))}"
                lines.append(f"- ⚠️ بقي بلا مالك: {document.filename} ({why})")
                continue
            email = emails.pop()
            owner = users.get(email)
            if owner is None:
                lines.append(f"- ⚠️ بقي بلا مالك: {document.filename} (الرافع {email} لم يعد مستخدمًا)")
                continue
            if apply:
                document.owner_id = owner
            lines.append(f"- ✅ {document.filename} ← {email}")
        if not apply:
            db.rollback()
    return lines


def loose_synonyms(apply: bool) -> list[str]:
    from app.config import get_settings
    from app.container import Container

    lines: list[str] = []
    service = Container(get_settings()).knowledge_service if apply else None
    with session_scope() as db:
        items = [
            item for item in db.scalars(select(KnowledgeItem).where(
                KnowledgeItem.type == KnowledgeType.TERMINOLOGY,
                KnowledgeItem.scope == KnowledgeScope.USER,
                KnowledgeItem.status == KnowledgeStatus.ACTIVE,
            ))
            if "learned-from-rephrasing" in (item.tags or []) and _as_utc(item.created_at) < STRICT_RULES_FROM
        ]
        for item in items:
            author = db.get(User, item.created_by) if item.created_by else None
            version = db.get(KnowledgeVersion, item.active_version_id) if item.active_version_id else None
            label = ((version.content if version is not None else "") or str(item.id))[:80]
            if apply and author is not None:
                service.transition(db, author, item, KnowledgeStatus.ARCHIVED,
                                   reason="تعلّمه النظام بقواعد أولى فضفاضة؛ يُتعلّم من جديد بالقواعد الحالية.")
            lines.append(f"- 🗄️ أُرشف: {label}" if author is not None else f"- ⚠️ بلا مؤلف، لم يُلمس: {label}")
        if not apply:
            db.rollback()
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    apply = parser.parse_args().apply

    owner_lines = owners(apply)
    synonym_lines = loose_synonyms(apply)
    report = "\n".join([
        f"# إصلاح البيانات القديمة — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "" if apply else "\n**تجربة فقط — لم يُغيَّر شيء.**",
        "", "## مستندات بلا مالك", "", *(owner_lines or ["- لا شيء."]),
        "", "## مرادفات تعلّمها النظام بالقواعد الأولى", "", *(synonym_lines or ["- لا شيء."]), "",
    ])
    print(report)
    if apply:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(report, encoding="utf-8")
        # Shown on the monitoring page's report card until tonight's report replaces it,
        # so the outcome can be read without opening the server.
        latest = REPORT.parent / "latest.md"
        previous = latest.read_text(encoding="utf-8") if latest.exists() else ""
        latest.write_text(report + ("\n---\n\n" + previous if previous else ""), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
