"""The knowledge lifecycle: propose, edit, review, retrieve.

Two rules are enforced here rather than left to callers, because a caller that forgets
either of them produces exactly the failure this layer exists to prevent.

Nothing reaches an answer unless its status is ACTIVE. That is a filter inside the query,
not a check applied to the rows afterwards, so a missing condition returns nothing rather
than leaking a claim nobody approved.

A version is never rewritten. An edit appends a row and repoints the item, so the wording
a reviewer actually approved stays readable after the item has moved on.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session as DbSession

from app.core.knowledge import (
    BEHAVIOURAL_TYPES,
    FACTUAL_TYPES,
    RETRIEVABLE_STATUSES,
    KnowledgeScope,
    KnowledgeStatus,
    KnowledgeType,
    ReviewDecision,
    can_transition,
    is_self_activating,
)
from app.core.permissions import Permission, permissions_for
from app.exceptions import AuthorizationError, DocumentNotFoundError, ValidationError
from app.models.auth import User
from app.models.knowledge_items import (
    KnowledgeItem,
    KnowledgeReview,
    KnowledgeUsage,
    KnowledgeVersion,
)

logger = logging.getLogger(__name__)

MAX_CONTENT_CHARS = 4000
MAX_TAGS = 12
#: Ceiling on what the knowledge arm may hand one answer. A knowledge base with
#: thousands of approved items must not turn every question into an unbounded read.
MAX_RETRIEVED_ITEMS = 40


def _utcnow() -> datetime:
    return datetime.now(UTC)


def question_digest(question: str) -> str:
    """Links repeat asks without keeping a searchable log of what people typed."""
    return hashlib.sha256(question.strip().lower().encode()).hexdigest()


@dataclass(slots=True)
class ProposedKnowledge:
    """What a person supplies when they teach the system something."""

    type: KnowledgeType
    content: str
    scope: KnowledgeScope = KnowledgeScope.USER
    source_text: str = ""
    source_document_id: str | None = None
    source_section_id: str | None = None
    confidence: float = 0.5
    explanation: str = ""
    tags: tuple[str, ...] = ()
    team_id: str | None = None
    department: str = ""


@dataclass(slots=True)
class ActiveKnowledge:
    """One approved item as the answer path sees it, with its provenance attached."""

    item_id: str
    version_id: str
    type: KnowledgeType
    scope: KnowledgeScope
    content: str
    source_text: str
    source_document_id: str | None
    confidence: float
    version_no: int
    tags: list[str]

    @property
    def is_factual(self) -> bool:
        """Factual items may be weighed against a document. Behavioural ones never are."""
        return self.type in FACTUAL_TYPES

    @property
    def is_behavioural(self) -> bool:
        return self.type in BEHAVIOURAL_TYPES


class KnowledgeService:
    def __init__(self, index=None, embedder=None) -> None:
        # Both optional: the service is usable — and tested — without a vector store.
        self.index = index
        self.embedder = embedder

    # -- authority --------------------------------------------------------
    @staticmethod
    def may_approve(user: User, item: KnowledgeItem) -> bool:
        """Approval authority is the role *and* the reach of the item.

        A knowledge manager answers for their own team; making something true for the
        whole company is an administrator's decision, because nobody else can see all
        the places it would land.
        """
        caps = permissions_for(user.role)
        if Permission.KNOWLEDGE_APPROVE not in caps:
            return False
        if item.scope in (KnowledgeScope.GLOBAL, KnowledgeScope.DEPARTMENT):
            return Permission.USER_MANAGE in caps
        if item.scope == KnowledgeScope.TEAM:
            return Permission.USER_MANAGE in caps or (
                user.team_id is not None and user.team_id == item.owner_team_id
            )
        return True

    @staticmethod
    def may_edit(user: User, item: KnowledgeItem) -> bool:
        caps = permissions_for(user.role)
        if Permission.KNOWLEDGE_EDIT in caps:
            return True
        # An author may always correct their own claim, including one already in use.
        # That is the safer rule, not the looser one: an edit withdraws the approval and
        # takes the item out of answers immediately, so the person who knows it is wrong
        # can stop it at once instead of waiting for a reviewer to be free. Archived
        # items are excluded because they are already out of use, and bringing one back
        # is a reviewer's decision.
        return (
            Permission.KNOWLEDGE_EDIT_OWN in caps
            and item.created_by == user.id
            and item.status != KnowledgeStatus.ARCHIVED
        )

    @staticmethod
    def may_read(user: User, item: KnowledgeItem) -> bool:
        """A personal item belongs to its owner and to whoever administers the system."""
        if item.scope != KnowledgeScope.USER:
            return True
        if item.owner_user_id == user.id:
            return True
        return Permission.USER_MANAGE in permissions_for(user.role)

    # -- writing ----------------------------------------------------------
    def propose(self, db: DbSession, user: User, proposal: ProposedKnowledge) -> KnowledgeItem:
        """Records a claim. It does not make it true.

        The single exception is a personal preference, which affects nobody else and so
        activates immediately for its owner.
        """
        content = proposal.content.strip()
        if not content:
            raise ValidationError("محتوى المعرفة مطلوب.")
        if len(content) > MAX_CONTENT_CHARS:
            raise ValidationError(f"المحتوى يتجاوز {MAX_CONTENT_CHARS} حرف.")
        if not 0.0 <= proposal.confidence <= 1.0:
            raise ValidationError("الثقة يجب أن تكون بين 0 و 1.")

        tags = [t.strip() for t in proposal.tags if t.strip()][:MAX_TAGS]

        scope = proposal.scope
        team_id = proposal.team_id or user.team_id
        if scope == KnowledgeScope.TEAM and not team_id:
            raise ValidationError("نطاق الفريق يتطلب انتماء المستخدم إلى فريق.")

        self_activating = is_self_activating(proposal.type, scope)
        now = _utcnow()

        item = KnowledgeItem(
            id=str(uuid.uuid4()),
            type=proposal.type,
            scope=scope,
            status=KnowledgeStatus.ACTIVE if self_activating else KnowledgeStatus.PENDING,
            owner_user_id=user.id if scope == KnowledgeScope.USER else None,
            owner_team_id=team_id if scope == KnowledgeScope.TEAM else None,
            department=proposal.department.strip(),
            created_by=user.id,
            tags=tags,
            created_at=now,
            updated_at=now,
            activated_at=now if self_activating else None,
        )
        db.add(item)
        db.flush()

        version = self._write_version(
            db,
            item,
            user,
            content=content,
            source_text=proposal.source_text.strip(),
            source_document_id=proposal.source_document_id,
            source_section_id=proposal.source_section_id,
            confidence=proposal.confidence,
            explanation=proposal.explanation.strip(),
            change_reason="",
        )
        item.active_version_id = version.id

        self._record(
            db,
            item,
            version.id,
            ReviewDecision.ACTIVATED if self_activating else ReviewDecision.SUBMITTED,
            user,
            from_status="",
            to_status=item.status,
            reason="تفضيل شخصي — لا يؤثر على غير صاحبه" if self_activating else "",
        )
        self._sync_index(db, item)
        logger.info(
            "Knowledge proposed: %s (%s/%s) by %s -> %s",
            item.id, item.type, item.scope, user.email, item.status,
        )
        return item

    def edit(
        self,
        db: DbSession,
        user: User,
        item: KnowledgeItem,
        *,
        content: str,
        change_reason: str = "",
        source_text: str | None = None,
        confidence: float | None = None,
        explanation: str | None = None,
    ) -> KnowledgeVersion:
        """Appends a new version. The previous one stays exactly as it was."""
        if not self.may_edit(user, item):
            raise AuthorizationError("لا تملك صلاحية تعديل هذا العنصر.")
        content = content.strip()
        if not content:
            raise ValidationError("محتوى المعرفة مطلوب.")

        current = self.active_version(db, item)
        version = self._write_version(
            db,
            item,
            user,
            content=content,
            source_text=current.source_text if source_text is None else source_text.strip(),
            source_document_id=current.source_document_id if current else None,
            source_section_id=current.source_section_id if current else None,
            confidence=current.confidence if confidence is None else confidence,
            explanation=current.explanation if explanation is None else explanation.strip(),
            change_reason=change_reason.strip(),
        )
        item.active_version_id = version.id
        item.updated_at = _utcnow()

        # Editing an approved claim withdraws the approval: what a reviewer agreed to
        # is a specific wording, and this is no longer that wording.
        if item.status in (KnowledgeStatus.APPROVED, KnowledgeStatus.ACTIVE):
            previous = item.status
            item.status = KnowledgeStatus.PENDING
            item.approved_by = None
            item.approved_at = None
            item.activated_at = None
            self._record(
                db, item, version.id, ReviewDecision.EDITED, user,
                from_status=previous, to_status=item.status,
                reason=change_reason or "تعديل المحتوى يستلزم إعادة الاعتماد",
            )
            self._sync_index(db, item)
            logger.info("Knowledge %s returned to review after an edit", item.id)
        else:
            self._record(
                db, item, version.id, ReviewDecision.EDITED, user,
                from_status=item.status, to_status=item.status, reason=change_reason,
            )
        return version

    def transition(
        self,
        db: DbSession,
        user: User,
        item: KnowledgeItem,
        target: KnowledgeStatus,
        *,
        reason: str = "",
    ) -> KnowledgeItem:
        """Moves an item, refusing any step the state machine does not allow."""
        current = KnowledgeStatus(item.status)
        if not can_transition(current, target):
            raise ValidationError(f"لا يمكن الانتقال من '{current}' إلى '{target}'.")

        decision = {
            KnowledgeStatus.IN_REVIEW: ReviewDecision.STARTED_REVIEW,
            KnowledgeStatus.APPROVED: ReviewDecision.APPROVED,
            KnowledgeStatus.ACTIVE: ReviewDecision.ACTIVATED,
            KnowledgeStatus.REJECTED: ReviewDecision.REJECTED,
            KnowledgeStatus.ARCHIVED: ReviewDecision.ARCHIVED,
            KnowledgeStatus.PENDING: (
                ReviewDecision.RESTORED
                if current is KnowledgeStatus.ARCHIVED
                else ReviewDecision.SUBMITTED
            ),
        }[target]

        caps = permissions_for(user.role)
        if target in (KnowledgeStatus.APPROVED, KnowledgeStatus.ACTIVE, KnowledgeStatus.REJECTED):
            if not self.may_approve(user, item):
                raise AuthorizationError("لا تملك صلاحية اعتماد هذا النطاق.")
        elif target is KnowledgeStatus.ARCHIVED:
            if Permission.KNOWLEDGE_ARCHIVE not in caps and item.created_by != user.id:
                raise AuthorizationError("لا تملك صلاحية أرشفة هذا العنصر.")
        elif target is KnowledgeStatus.IN_REVIEW and Permission.KNOWLEDGE_APPROVE not in caps:
            raise AuthorizationError("لا تملك صلاحية بدء المراجعة.")

        if target is KnowledgeStatus.REJECTED and not reason.strip():
            # An author who is told only "no" cannot act on it.
            raise ValidationError("سبب الرفض مطلوب.")
        if item.active_version_id is None and target in (
            KnowledgeStatus.APPROVED, KnowledgeStatus.ACTIVE
        ):
            raise ValidationError("لا يوجد محتوى لاعتماده.")

        now = _utcnow()
        item.status = target
        item.updated_at = now
        if target is KnowledgeStatus.APPROVED:
            item.approved_by, item.approved_at = user.id, now
        elif target is KnowledgeStatus.ACTIVE:
            item.activated_at = now
            if item.approved_by is None:
                item.approved_by, item.approved_at = user.id, now
        elif target is KnowledgeStatus.ARCHIVED:
            item.archived_at = now
        elif target is KnowledgeStatus.PENDING:
            item.archived_at = None

        self._record(
            db, item, item.active_version_id, decision, user,
            from_status=current, to_status=target, reason=reason.strip(),
        )
        self._sync_index(db, item)
        logger.info("Knowledge %s: %s -> %s by %s", item.id, current, target, user.email)
        return item

    # -- reading ----------------------------------------------------------
    @staticmethod
    def get(db: DbSession, item_id: str) -> KnowledgeItem:
        item = db.get(KnowledgeItem, item_id)
        if item is None:
            raise DocumentNotFoundError(f"لا يوجد عنصر معرفة بالمعرّف '{item_id}'.")
        return item

    @staticmethod
    def active_version(db: DbSession, item: KnowledgeItem) -> KnowledgeVersion | None:
        if item.active_version_id is None:
            return None
        return db.get(KnowledgeVersion, item.active_version_id)

    @staticmethod
    def versions(db: DbSession, item_id: str) -> list[KnowledgeVersion]:
        return list(
            db.scalars(
                select(KnowledgeVersion)
                .where(KnowledgeVersion.item_id == item_id)
                .order_by(KnowledgeVersion.version_no.desc())
            )
        )

    @staticmethod
    def reviews(db: DbSession, item_id: str) -> list[KnowledgeReview]:
        return list(
            db.scalars(
                select(KnowledgeReview)
                .where(KnowledgeReview.item_id == item_id)
                .order_by(KnowledgeReview.created_at.asc())
            )
        )

    @staticmethod
    def usages(db: DbSession, item_id: str, limit: int = 50) -> list[KnowledgeUsage]:
        return list(
            db.scalars(
                select(KnowledgeUsage)
                .where(KnowledgeUsage.item_id == item_id)
                .order_by(KnowledgeUsage.used_at.desc())
                .limit(limit)
            )
        )

    def search(
        self,
        db: DbSession,
        user: User,
        *,
        types: list[str] | None = None,
        scopes: list[str] | None = None,
        statuses: list[str] | None = None,
        query: str | None = None,
        tag: str | None = None,
        owner_id: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[int, list[KnowledgeItem]]:
        """Knowledge Center listing. Personal items stay personal."""
        filters = []
        if types:
            filters.append(KnowledgeItem.type.in_(types))
        if scopes:
            filters.append(KnowledgeItem.scope.in_(scopes))
        if statuses:
            filters.append(KnowledgeItem.status.in_(statuses))
        if owner_id:
            filters.append(KnowledgeItem.created_by == owner_id)

        # Someone else's personal note is not listable, whatever the other filters say.
        if Permission.USER_MANAGE not in permissions_for(user.role):
            filters.append(
                or_(
                    KnowledgeItem.scope != KnowledgeScope.USER,
                    KnowledgeItem.owner_user_id == user.id,
                )
            )

        statement = select(KnowledgeItem).where(*filters)
        if query:
            pattern = f"%{query.strip()}%"
            statement = statement.join(
                KnowledgeVersion, KnowledgeVersion.id == KnowledgeItem.active_version_id
            ).where(KnowledgeVersion.content.ilike(pattern))

        total = db.scalar(
            select(func.count()).select_from(statement.subquery())
        ) or 0
        rows = list(
            db.scalars(
                statement.order_by(KnowledgeItem.updated_at.desc()).limit(limit).offset(offset)
            )
        )
        if tag:
            needle = tag.strip().lower()
            rows = [r for r in rows if any(needle == str(t).lower() for t in (r.tags or []))]
        return total, rows

    def active_for(
        self, db: DbSession, user: User, *, limit: int = MAX_RETRIEVED_ITEMS
    ) -> list[ActiveKnowledge]:
        """Everything approved that this person's answers may draw on.

        The status filter is part of the query. Rejected, archived, draft and pending
        items are not fetched and then discarded — they are never selected, so a bug in
        later code cannot surface one.
        """
        reach = [KnowledgeScope.GLOBAL]
        if user.team_id:
            reach.append(KnowledgeScope.TEAM)
        reach.append(KnowledgeScope.DEPARTMENT)

        scope_filter = or_(
            KnowledgeItem.scope.in_([s.value for s in reach]),
            # A personal item reaches its owner and nobody else.
            (KnowledgeItem.scope == KnowledgeScope.USER)
            & (KnowledgeItem.owner_user_id == user.id),
        )
        team_filter = or_(
            KnowledgeItem.scope != KnowledgeScope.TEAM,
            KnowledgeItem.owner_team_id == user.team_id,
        )

        rows = db.execute(
            select(KnowledgeItem, KnowledgeVersion)
            .join(KnowledgeVersion, KnowledgeVersion.id == KnowledgeItem.active_version_id)
            .where(
                KnowledgeItem.status.in_([s.value for s in RETRIEVABLE_STATUSES]),
                scope_filter,
                team_filter,
            )
            .order_by(KnowledgeItem.updated_at.desc())
            .limit(limit)
        ).all()

        return [
            ActiveKnowledge(
                item_id=item.id,
                version_id=version.id,
                type=KnowledgeType(item.type),
                scope=KnowledgeScope(item.scope),
                content=version.content,
                source_text=version.source_text,
                source_document_id=version.source_document_id,
                confidence=version.confidence,
                version_no=version.version_no,
                tags=list(item.tags or []),
            )
            for item, version in rows
        ]

    @staticmethod
    def record_usage(
        db: DbSession,
        entries: list[tuple[ActiveKnowledge, str, bool]],
        *,
        question: str,
        user_id: str | None,
        answer_id: str = "",
        scores: dict[str, float] | None = None,
    ) -> int:
        """Notes which items reached an answer, and which of them disagreed with a document."""
        if not entries:
            return 0
        digest = question_digest(question)
        now = _utcnow()
        for knowledge, influence, conflicted in entries:
            db.add(
                KnowledgeUsage(
                    id=str(uuid.uuid4()),
                    item_id=knowledge.item_id,
                    version_id=knowledge.version_id,
                    question_hash=digest,
                    user_id=user_id,
                    influence=influence,
                    conflicted=conflicted,
                    answer_id=answer_id,
                    score=(scores or {}).get(knowledge.item_id, 0.0),
                    used_at=now,
                )
            )
        return len(entries)

    # -- semantic index ---------------------------------------------------
    def _sync_index(self, db: DbSession, item: KnowledgeItem) -> None:
        """Keeps the vector index equal to the ACTIVE set.

        Anything that is not ACTIVE is removed rather than left behind, so a vector can
        never outlive the approval that justified it — an archived claim disappears from
        search in the same operation that archived it.

        Every call is best-effort. A reviewer must not be blocked because an embedding
        host is down; the arm falls back to lexical selection until the index catches up.
        """
        if self.index is None:
            return

        if item.status != KnowledgeStatus.ACTIVE or item.active_version_id is None:
            self.index.remove(item.id)
            return

        # Directives are not searched semantically: a rule applies because it was
        # approved, not because it resembles the question.
        if KnowledgeType(item.type) in BEHAVIOURAL_TYPES:
            self.index.remove(item.id)
            return
        if self.embedder is None:
            return

        version = db.get(KnowledgeVersion, item.active_version_id)
        if version is None:
            return
        try:
            text = f"{version.content}\n{version.source_text}".strip()
            vector = self.embedder.embed_one(text)
        except Exception:  # noqa: BLE001
            logger.warning("Could not embed knowledge item %s; it stays lexical-only", item.id)
            return

        self.index.upsert(
            item_id=item.id,
            version_id=version.id,
            vector=vector,
            payload={
                "type": item.type,
                "scope": item.scope,
                "owner_user_id": item.owner_user_id or "",
                "owner_team_id": item.owner_team_id or "",
            },
        )

    # -- internals --------------------------------------------------------
    @staticmethod
    def _write_version(
        db: DbSession,
        item: KnowledgeItem,
        user: User,
        *,
        content: str,
        source_text: str,
        source_document_id: str | None,
        source_section_id: str | None,
        confidence: float,
        explanation: str,
        change_reason: str,
    ) -> KnowledgeVersion:
        highest = db.scalar(
            select(func.max(KnowledgeVersion.version_no)).where(
                KnowledgeVersion.item_id == item.id
            )
        )
        version = KnowledgeVersion(
            id=str(uuid.uuid4()),
            item_id=item.id,
            version_no=(highest or 0) + 1,
            content=content,
            source_text=source_text,
            source_document_id=source_document_id,
            source_section_id=source_section_id,
            confidence=confidence,
            explanation=explanation,
            created_by=user.id,
            change_reason=change_reason,
        )
        db.add(version)
        db.flush()
        return version

    @staticmethod
    def _record(
        db: DbSession,
        item: KnowledgeItem,
        version_id: str | None,
        decision: ReviewDecision,
        user: User,
        *,
        from_status: str,
        to_status: str,
        reason: str,
    ) -> None:
        db.add(
            KnowledgeReview(
                id=str(uuid.uuid4()),
                item_id=item.id,
                version_id=version_id,
                decision=decision,
                from_status=str(from_status),
                to_status=str(to_status),
                reviewer_id=user.id,
                reason=reason,
            )
        )
