"""User and team administration. Every route here needs `user.manage`."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query
from sqlalchemy import func, or_, select

from app.api.deps import AuthServiceDep, CurrentUserDep, SessionDep
from app.core.permissions import Permission
from app.api.deps import require
from app.exceptions import DocumentNotFoundError, ValidationError
from app.models.auth import Team, User
from app.schemas.auth import (
    TeamCreateRequest,
    TeamResponse,
    UserCreateRequest,
    UserListResponse,
    UserResponse,
    UserUpdateRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["users"])


@router.get("/users", response_model=UserListResponse)
def list_users(
    session: SessionDep,
    _admin: User = require(Permission.USER_MANAGE),
    search: str | None = Query(default=None),
    role: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> UserListResponse:
    filters = []
    if search:
        pattern = f"%{search.lower()}%"
        filters.append(or_(User.email.ilike(pattern), User.display_name.ilike(pattern)))
    if role:
        filters.append(User.role == role)

    total = session.scalar(select(func.count()).select_from(User).where(*filters)) or 0
    rows = session.scalars(
        select(User).where(*filters).order_by(User.created_at.desc()).limit(limit).offset(offset)
    )
    return UserListResponse(total=total, items=[UserResponse.model_validate(r) for r in rows])


@router.post("/users", response_model=UserResponse, status_code=201)
def create_user(
    body: UserCreateRequest,
    session: SessionDep,
    auth: AuthServiceDep,
    admin: User = require(Permission.USER_MANAGE),
) -> UserResponse:
    if body.team_id and session.get(Team, body.team_id) is None:
        raise ValidationError(f"لا يوجد فريق بالمعرّف '{body.team_id}'.")
    user = auth.create_user(
        session,
        email=body.email,
        password=body.password,
        display_name=body.display_name,
        role=body.role,
        team_id=body.team_id,
    )
    session.commit()
    logger.info("User %s created %s (%s)", admin.email, user.email, user.role)
    return UserResponse.model_validate(user)


@router.patch("/users/{user_id}", response_model=UserResponse)
def update_user(
    user_id: str,
    body: UserUpdateRequest,
    session: SessionDep,
    auth: AuthServiceDep,
    admin: User = require(Permission.USER_MANAGE),
) -> UserResponse:
    user = session.get(User, user_id)
    if user is None:
        raise DocumentNotFoundError(f"لا يوجد مستخدم بالمعرّف '{user_id}'.")

    # An administrator who removes their own last privilege locks everyone out of user
    # management, and there is no way back in without editing the database by hand.
    if user.id == admin.id:
        if body.role is not None and body.role != user.role:
            raise ValidationError("لا يمكنك تغيير دورك بنفسك.")
        if body.is_active is False:
            raise ValidationError("لا يمكنك تعطيل حسابك بنفسك.")

    changed_authority = False
    if body.display_name is not None:
        user.display_name = body.display_name
    if body.team_id is not None:
        if body.team_id and session.get(Team, body.team_id) is None:
            raise ValidationError(f"لا يوجد فريق بالمعرّف '{body.team_id}'.")
        user.team_id = body.team_id or None
        changed_authority = True
    if body.role is not None and body.role != user.role:
        user.role = body.role
        changed_authority = True
    if body.is_active is not None and body.is_active != user.is_active:
        user.is_active = body.is_active
        changed_authority = True
    if body.new_password:
        auth.set_password(user, body.new_password)
        changed_authority = True

    # Live sessions were issued under the old role or the old account state, so they
    # would keep carrying permissions this change just took away.
    if changed_authority:
        revoked = auth.revoke_all_for_user(session, user.id)
        logger.info("Authority changed for %s; revoked %s session(s)", user.email, revoked)

    session.commit()
    return UserResponse.model_validate(user)


@router.delete("/users/{user_id}", status_code=204)
def deactivate_user(
    user_id: str,
    session: SessionDep,
    auth: AuthServiceDep,
    admin: User = require(Permission.USER_MANAGE),
) -> None:
    """Deactivates rather than deletes: the audit trail references this row."""
    user = session.get(User, user_id)
    if user is None:
        raise DocumentNotFoundError(f"لا يوجد مستخدم بالمعرّف '{user_id}'.")
    if user.id == admin.id:
        raise ValidationError("لا يمكنك تعطيل حسابك بنفسك.")
    user.is_active = False
    auth.revoke_all_for_user(session, user.id)
    session.commit()
    logger.info("User %s deactivated %s", admin.email, user.email)


@router.get("/teams", response_model=list[TeamResponse])
def list_teams(session: SessionDep, _user: CurrentUserDep) -> list[TeamResponse]:
    rows = session.scalars(select(Team).order_by(Team.name))
    return [TeamResponse.model_validate(r) for r in rows]


@router.post("/teams", response_model=TeamResponse, status_code=201)
def create_team(
    body: TeamCreateRequest,
    session: SessionDep,
    auth: AuthServiceDep,
    _admin: User = require(Permission.USER_MANAGE),
) -> TeamResponse:
    team = auth.create_team(session, body.name, body.department)
    session.commit()
    return TeamResponse.model_validate(team)
