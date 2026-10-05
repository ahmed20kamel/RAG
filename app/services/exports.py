"""A file asked for in the chat: "حوّل ده لـ PDF", "ملخص المحادثة Word", "مقارنة … في Excel".

Three kinds of request, one result — a file in the reader's own folder and a reply that
links to it:

* **the last answer** — the one just given in this conversation, as it was;
* **the conversation** — summarised by the model from what was asked and answered in it,
  and nothing else;
* **a new question** — answered by the ordinary pipeline, with its checks and sources,
  then delivered as the file. A question the documents cannot answer gets its usual
  reply and no file: an empty or invented document helps nobody.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from sqlalchemy import select

from app.models.database import session_scope
from app.models.question_log import QuestionRecord
from app.schemas.chat import ChatFile, ChatRequest, ChatResponse
from app.services.activity import ACTIVITY
from app.services.file_export import (
    FORMATS, Content, FileRequest, FileStore, detect, parse, render_docx, render_pdf, render_xlsx,
)
from app.services.learning_loop import answered

logger = logging.getLogger(__name__)

#: How much of a conversation the summary reads. Every 1,000 characters costs the model
#: several seconds; the most recent turns matter most and are kept.
MAX_CONVERSATION_CHARS = 9000

SUMMARY_SYSTEM = (
    "أنت تكتب ملخصًا رسميًا موجزًا لمحادثة بين مستخدم ونظام يجيب من مستندات الشركة. "
    "اكتب فقط ما ورد في المحادثة، ولا تضف معلومة أو رقمًا من عندك، وانقل الأرقام والتواريخ كما وردت حرفيًا. "
    "استخدم عناوين Markdown (##) ونقاطًا قصيرة، وجدول Markdown إن كانت هناك أرقام تُقارن. "
    "اكتب بلغة المحادثة."
)
SUMMARY_TEMPLATE = "المحادثة:\n\n{conversation}\n\nاكتب الملخص الآن."


def _filename(title: str, fmt: str) -> str:
    stem = re.sub(r"[^\w\s-]", " ", title, flags=re.UNICODE)
    stem = "_".join(stem.split())[:60].strip("_") or "file"
    return f"{stem}_{datetime.now():%Y-%m-%d}.{fmt}"


class ExportService:
    def __init__(self, rag, store: FileStore, knowledge) -> None:
        self.rag = rag
        self.store = store
        self.knowledge = knowledge

    def handle(self, request: ChatRequest, user) -> ChatResponse | None:
        """A reply with a file, or None when the question asks for no file."""
        if user is None or request.image_text or request.image_marked:
            return None
        wanted = detect(request.question)
        if wanted is None:
            return None
        logger.info("File requested by %s: %s (%s)", user.email, wanted.subject, wanted.format)
        if wanted.subject == "question":
            return self._from_question(wanted, request, user)
        if wanted.subject == "conversation":
            return self._from_conversation(wanted, request, user)
        return self._from_last(wanted, request, user)

    # -- the three sources ---------------------------------------------------
    def _turns(self, user, conversation_id: str | None) -> list[QuestionRecord]:
        if not conversation_id:
            return []
        with session_scope() as db:
            rows = list(db.scalars(
                select(QuestionRecord)
                .where(QuestionRecord.user_id == user.id, QuestionRecord.conversation_id == conversation_id)
                .order_by(QuestionRecord.id)
            ))
            db.expunge_all()
        return rows

    def _from_last(self, wanted: FileRequest, request: ChatRequest, user) -> ChatResponse:
        turns = [t for t in self._turns(user, request.conversation_id) if t.outcome == "answered" and t.answer]
        if not turns:
            return self._reply(
                "لا توجد إجابة سابقة في هذه المحادثة لأحوّلها إلى ملف. اسأل سؤالك أولًا، أو اكتب "
                "مثلًا: «اعمل PDF بأسعار الدهان في أمر التغيير».", grounded=False,
            )
        last = turns[-1]
        previous = ChatResponse.model_validate(last.response) if last.response else None
        sources = previous.sources if previous is not None else []
        return self._deliver(wanted, user, title=last.question, markdown=last.answer, sources=sources,
                             based_on=previous)

    def _from_conversation(self, wanted: FileRequest, request: ChatRequest, user) -> ChatResponse:
        turns = [t for t in self._turns(user, request.conversation_id) if t.answer]
        if not turns:
            return self._reply("هذه المحادثة لا تحتوي أسئلة وإجابات بعد لأُلخّصها.", grounded=False)
        lines: list[str] = []
        for turn in turns:
            lines.append(f"السؤال: {turn.question}\nالإجابة: {turn.answer}")
        text = "\n\n".join(lines)[-MAX_CONVERSATION_CHARS:]
        ticket = ACTIVITY.begin(user.id)
        try:
            summary = self.rag.llm.chat(SUMMARY_SYSTEM, SUMMARY_TEMPLATE.format(conversation=text))
        finally:
            ACTIVITY.end(ticket)
        evidence = "\n".join(t.answer for t in turns)
        names: list[str] = []
        for turn in turns:
            for source in (turn.response or {}).get("sources", []) or []:
                name = source.get("filename", "")
                if name and name not in names:
                    names.append(name)
        title = f"ملخص المحادثة — {turns[0].question[:60]}"
        return self._deliver(wanted, user, title=title, markdown=summary, sources=[], evidence=evidence,
                             source_names=names)

    def _from_question(self, wanted: FileRequest, request: ChatRequest, user) -> ChatResponse:
        question = wanted.question
        if wanted.format == "xlsx":
            # A spreadsheet needs a table to be one; asked for as part of the sentence,
            # because a bracketed instruction is read as a second question.
            question = f"{question.rstrip('؟?. ')} على شكل جدول؟"
        response = self.rag.answer(
            request.model_copy(update={"question": question, "fresh": True}), user=user, channel="file",
        )
        if not answered(response):
            response.answer = (
                "لم أُنشئ ملفًا: لم أجد في المستندات ما يكفي لهذا الطلب.\n\n" + response.answer
            )
            return response
        return self._deliver(wanted, user, title=wanted.question.rstrip("؟?"), markdown=response.answer,
                             sources=response.sources, based_on=response)

    # -- making the file -----------------------------------------------------
    def _evidence(self, sources) -> str:
        ids = [s.chunk_id for s in sources if getattr(s, "chunk_id", "")]
        try:
            records = self.knowledge.chunks_by_ids(ids) if ids else {}
        except Exception:  # noqa: BLE001 - checked against excerpts instead
            records = {}
        texts = [r.content for r in records.values()] + [getattr(s, "excerpt", "") or "" for s in sources]
        return "\n".join(texts)

    def _deliver(self, wanted: FileRequest, user, *, title: str, markdown: str, sources, evidence: str | None = None,
                 source_names: list[str] | None = None, based_on: ChatResponse | None = None) -> ChatResponse:
        names = source_names or []
        for source in sources:
            label = source.filename + (f" — {source.locator}" if getattr(source, "locator", "") else "")
            if label not in names:
                names.append(label)
        file_id = self.store.new_id()
        content = Content(
            reference=f"KB-{datetime.now():%y%m%d}-{file_id[:6].upper()}",
            title=title.strip() or "ملف",
            blocks=parse(markdown, keep_citations=wanted.format != "xlsx"),
            sources=names,
            evidence=evidence if evidence is not None else self._evidence(sources),
            author=getattr(user, "display_name", "") or getattr(user, "email", ""),
        )
        path = self.store.path_for(user.id, file_id, wanted.format)
        unsupported = 0
        if wanted.format == "pdf":
            render_pdf(content, path)
        elif wanted.format == "docx":
            render_docx(content, path)
        else:
            unsupported = render_xlsx(content, path)
        stored = self.store.record(user.id, file_id, _filename(content.title, wanted.format), wanted.format)
        logger.info("File made for %s: %s (%s bytes)", user.email, stored.name, stored.size)

        note = f"جهّزت ملف {FORMATS[wanted.format]}: **{stored.name}** — اضغط «تحميل» أدناه."
        if unsupported:
            note += (f"\n\n⚠️ {unsupported} رقمًا في الجدول لم أجده نصًّا في المصادر، فلوّنته بالأصفر "
                     "وأضفت عليه ملاحظة — راجعه قبل الاعتماد.")
        if wanted.format == "xlsx" and not any(b.kind == "table" for b in content.blocks):
            note += "\n\nلم تكن في الإجابة جداول، فوضعت محتواها صفوفًا في الملف."
        reply = self._reply(note, grounded=True)
        reply.files = [ChatFile(id=stored.id, name=stored.name, format=stored.format, size=stored.size)]
        if based_on is not None:
            reply.sources = based_on.sources
            reply.answer_id = based_on.answer_id
        return reply

    def _reply(self, text: str, grounded: bool) -> ChatResponse:
        return ChatResponse(answer=text, grounded=grounded, model=self.rag.llm.model, answer_source="internal")
