"""Knowledge Center: teach, list, review, and trace what an item touched."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import ContainerDep, CurrentUserDep, SessionDep, require
from app.core.knowledge import KnowledgeScope, KnowledgeStatus, KnowledgeType
from app.core.permissions import Permission, permissions_for
from app.exceptions import AuthorizationError
from app.models.auth import User
from app.models.knowledge_items import KnowledgeItem, KnowledgeUsage
from app.schemas.knowledge import (
    KnowledgeDecisionRequest,
    KnowledgeDetailResponse,
    KnowledgeEditRequest,
    KnowledgeItemResponse,
    KnowledgeListResponse,
    KnowledgeReviewResponse,
    KnowledgeStatsResponse,
    KnowledgeUsageResponse,
    KnowledgeVersionResponse,
    TeachRequest,
)
from app.services.knowledge_service import ProposedKnowledge

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


def _present(
    session, service, item: KnowledgeItem, viewer: User
) -> KnowledgeItemResponse:
    """Flattens an item with its live version and what this viewer may do to it."""
    version = service.active_version(session, item)
    names: dict[str, str] = {}
    for user_id in (item.created_by, item.approved_by):
        if user_id and user_id not in names:
            row = session.get(User, user_id)
            names[user_id] = row.display_name or row.email if row else ""

    return KnowledgeItemResponse(
        id=item.id,
        type=KnowledgeType(item.type),
        scope=KnowledgeScope(item.scope),
        status=KnowledgeStatus(item.status),
        content=version.content if version else "",
        source_text=version.source_text if version else "",
        source_document_id=version.source_document_id if version else None,
        confidence=version.confidence if version else 0.0,
        version_no=version.version_no if version else 1,
        explanation=version.explanation if version else "",
        tags=list(item.tags or []),
        owner_user_id=item.owner_user_id,
        owner_team_id=item.owner_team_id,
        department=item.department,
        created_by=item.created_by,
        created_by_name=names.get(item.created_by or "", ""),
        approved_by=item.approved_by,
        approved_by_name=names.get(item.approved_by or "", ""),
        created_at=item.created_at,
        updated_at=item.updated_at,
        approved_at=item.approved_at,
        activated_at=item.activated_at,
        archived_at=item.archived_at,
        can_edit=service.may_edit(viewer, item),
        can_approve=service.may_approve(viewer, item),
        can_archive=(
            Permission.KNOWLEDGE_ARCHIVE in permissions_for(viewer.role)
            or item.created_by == viewer.id
        ),
    )


@router.get("", response_model=KnowledgeListResponse)
def list_knowledge(
    session: SessionDep,
    container: ContainerDep,
    user: CurrentUserDep,
    type: list[str] | None = Query(default=None),
    scope: list[str] | None = Query(default=None),
    status: list[str] | None = Query(default=None),
    q: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    mine: bool = Query(default=False),
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> KnowledgeListResponse:
    service = container.knowledge_service
    total, rows = service.search(
        session,
        user,
        types=type,
        scopes=scope,
        statuses=status,
        query=q,
        tag=tag,
        owner_id=user.id if mine else None,
        limit=limit,
        offset=offset,
    )
    return KnowledgeListResponse(
        total=total, items=[_present(session, service, row, user) for row in rows]
    )


@router.get("/stats", response_model=KnowledgeStatsResponse)
def knowledge_stats(
    session: SessionDep, container: ContainerDep, _user: CurrentUserDep
) -> KnowledgeStatsResponse:
    """Counted in the database rather than by listing every row."""

    def tally(column) -> dict[str, int]:
        return {
            str(value): int(count)
            for value, count in session.execute(select(column, func.count()).group_by(column))
        }

    by_status = tally(KnowledgeItem.status)
    return KnowledgeStatsResponse(
        total=session.scalar(select(func.count()).select_from(KnowledgeItem)) or 0,
        by_status=by_status,
        by_type=tally(KnowledgeItem.type),
        by_scope=tally(KnowledgeItem.scope),
        pending_review=by_status.get(KnowledgeStatus.PENDING, 0)
        + by_status.get(KnowledgeStatus.IN_REVIEW, 0),
        active=by_status.get(KnowledgeStatus.ACTIVE, 0),
    )


@router.post("", response_model=KnowledgeItemResponse, status_code=201)
def teach(
    body: TeachRequest,
    session: SessionDep,
    container: ContainerDep,
    user: User = require(Permission.KNOWLEDGE_PROPOSE),
) -> KnowledgeItemResponse:
    """Records what someone taught the system. It does not make it true."""
    service = container.knowledge_service
    item = service.propose(
        session,
        user,
        ProposedKnowledge(
            type=body.type,
            content=body.content,
            scope=body.scope,
            source_text=body.source_text,
            source_document_id=body.source_document_id,
            source_section_id=body.source_section_id,
            confidence=body.confidence,
            explanation=body.explanation,
            tags=tuple(body.tags),
            team_id=body.team_id,
            department=body.department,
        ),
    )
    session.commit()
    return _present(session, service, item, user)


@router.get("/{item_id}", response_model=KnowledgeDetailResponse)
def get_knowledge(
    item_id: str, session: SessionDep, container: ContainerDep, user: CurrentUserDep
) -> KnowledgeDetailResponse:
    service = container.knowledge_service
    item = service.get(session, item_id)
    if not service.may_read(user, item):
        raise AuthorizationError("هذا العنصر شخصي ولا يخصّك.")

    base = _present(session, service, item, user)
    usages = service.usages(session, item_id)
    return KnowledgeDetailResponse(
        **base.model_dump(),
        versions=[KnowledgeVersionResponse.model_validate(v) for v in service.versions(session, item_id)],
        reviews=[KnowledgeReviewResponse.model_validate(r) for r in service.reviews(session, item_id)],
        usages=[KnowledgeUsageResponse.model_validate(u) for u in usages],
        usage_count=session.scalar(
            select(func.count()).select_from(KnowledgeUsage).where(KnowledgeUsage.item_id == item_id)
        )
        or 0,
    )


@router.patch("/{item_id}", response_model=KnowledgeItemResponse)
def edit_knowledge(
    item_id: str,
    body: KnowledgeEditRequest,
    session: SessionDep,
    container: ContainerDep,
    user: CurrentUserDep,
) -> KnowledgeItemResponse:
    """Appends a version. An approved item returns to review, because what a reviewer
    agreed to was a specific wording and this is no longer that wording."""
    service = container.knowledge_service
    item = service.get(session, item_id)
    service.edit(
        session,
        user,
        item,
        content=body.content,
        change_reason=body.change_reason,
        source_text=body.source_text,
        confidence=body.confidence,
        explanation=body.explanation,
    )
    session.commit()
    return _present(session, service, item, user)


def _decide(session, container, user, item_id: str, target: KnowledgeStatus, reason: str):
    service = container.knowledge_service
    item = service.transition(session, user, service.get(session, item_id), target, reason=reason)
    session.commit()
    return _present(session, service, item, user)


@router.post("/{item_id}/submit", response_model=KnowledgeItemResponse)
def submit(
    item_id: str,
    body: KnowledgeDecisionRequest,
    session: SessionDep,
    container: ContainerDep,
    user: CurrentUserDep,
) -> KnowledgeItemResponse:
    return _decide(session, container, user, item_id, KnowledgeStatus.PENDING, body.reason)


@router.post("/{item_id}/review", response_model=KnowledgeItemResponse)
def start_review(
    item_id: str,
    body: KnowledgeDecisionRequest,
    session: SessionDep,
    container: ContainerDep,
    user: User = require(Permission.KNOWLEDGE_APPROVE),
) -> KnowledgeItemResponse:
    return _decide(session, container, user, item_id, KnowledgeStatus.IN_REVIEW, body.reason)


@router.post("/{item_id}/approve", response_model=KnowledgeItemResponse)
def approve(
    item_id: str,
    body: KnowledgeDecisionRequest,
    session: SessionDep,
    container: ContainerDep,
    user: User = require(Permission.KNOWLEDGE_APPROVE),
) -> KnowledgeItemResponse:
    """Approved, but not yet in use — activation is the separate, deliberate step."""
    return _decide(session, container, user, item_id, KnowledgeStatus.APPROVED, body.reason)


@router.post("/{item_id}/activate", response_model=KnowledgeItemResponse)
def activate(
    item_id: str,
    body: KnowledgeDecisionRequest,
    session: SessionDep,
    container: ContainerDep,
    user: User = require(Permission.KNOWLEDGE_APPROVE),
) -> KnowledgeItemResponse:
    """The only step that lets an item reach an answer."""
    return _decide(session, container, user, item_id, KnowledgeStatus.ACTIVE, body.reason)


@router.post("/{item_id}/reject", response_model=KnowledgeItemResponse)
def reject(
    item_id: str,
    body: KnowledgeDecisionRequest,
    session: SessionDep,
    container: ContainerDep,
    user: User = require(Permission.KNOWLEDGE_APPROVE),
) -> KnowledgeItemResponse:
    return _decide(session, container, user, item_id, KnowledgeStatus.REJECTED, body.reason)


@router.post("/{item_id}/archive", response_model=KnowledgeItemResponse)
def archive(
    item_id: str,
    body: KnowledgeDecisionRequest,
    session: SessionDep,
    container: ContainerDep,
    user: CurrentUserDep,
) -> KnowledgeItemResponse:
    """Retires an item without deleting it: the audit trail still references it."""
    return _decide(session, container, user, item_id, KnowledgeStatus.ARCHIVED, body.reason)


@router.post("/{item_id}/restore", response_model=KnowledgeItemResponse)
def restore(
    item_id: str,
    body: KnowledgeDecisionRequest,
    session: SessionDep,
    container: ContainerDep,
    user: User = require(Permission.KNOWLEDGE_ARCHIVE),
) -> KnowledgeItemResponse:
    """Back to review — never straight back into use."""
    return _decide(session, container, user, item_id, KnowledgeStatus.PENDING, body.reason)
