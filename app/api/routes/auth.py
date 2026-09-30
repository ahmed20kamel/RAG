"""Login, logout, identity and password change."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response

from app.api.deps import AuthServiceDep, CurrentUserDep, SessionDep, SettingsDep
from app.core.permissions import permissions_for
from app.exceptions import AuthenticationError, ValidationError
from app.models.auth import Team
from app.schemas.auth import (
    CurrentUserResponse,
    LoginRequest,
    PasswordChangeRequest,
)
from app.services.auth_service import verify_password

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_session_cookie(response: Response, token: str, settings) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,                       # unreadable from JavaScript, so XSS cannot lift it
        secure=settings.session_cookie_secure,
        samesite="lax",                      # blocks the cross-site POST that CSRF needs
        path="/",
    )


def _describe(user, session, settings) -> CurrentUserResponse:
    team_name = ""
    if user.team_id:
        team = session.get(Team, user.team_id)
        team_name = team.name if team else ""
    payload = CurrentUserResponse.model_validate(user)
    payload.permissions = sorted(p.value for p in permissions_for(user.role))
    payload.team_name = team_name
    return payload


@router.post("/login", response_model=CurrentUserResponse)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    auth: AuthServiceDep,
    settings: SettingsDep,
) -> CurrentUserResponse:
    user = auth.authenticate(session, body.email, body.password)
    if user is None:
        # One message for every failure mode: a wrong password and an unknown account
        # must not be distinguishable, or the endpoint becomes an account enumerator.
        logger.info("Failed login attempt for %s", body.email[:64])
        raise AuthenticationError("البريد الإلكتروني أو كلمة المرور غير صحيحة.")

    token, _row = auth.issue_session(
        session,
        user,
        user_agent=request.headers.get("user-agent", ""),
        ip=request.client.host if request.client else "",
    )
    # FastAPI closes the session dependency *after* the response is sent, so a client
    # that immediately makes its next request can arrive before the row exists.
    session.commit()

    _set_session_cookie(response, token, settings)
    logger.info("Login: %s (%s)", user.email, user.role)
    return _describe(user, session, settings)


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    session: SessionDep,
    auth: AuthServiceDep,
    settings: SettingsDep,
) -> None:
    token = request.cookies.get(settings.session_cookie_name, "")
    if token:
        auth.revoke(session, token)
        session.commit()
    response.delete_cookie(settings.session_cookie_name, path="/")


@router.get("/me", response_model=CurrentUserResponse)
def me(user: CurrentUserDep, session: SessionDep, settings: SettingsDep) -> CurrentUserResponse:
    return _describe(user, session, settings)


@router.post("/password", status_code=204)
def change_password(
    body: PasswordChangeRequest,
    user: CurrentUserDep,
    session: SessionDep,
    auth: AuthServiceDep,
) -> None:
    if not verify_password(user.password_hash, body.current_password):
        raise ValidationError("كلمة المرور الحالية غير صحيحة.")
    if body.current_password == body.new_password:
        raise ValidationError("كلمة المرور الجديدة مطابقة للحالية.")

    auth.set_password(user, body.new_password)
    # Every other session was authenticated with the old password; a password change is
    # usually a response to a suspected leak, so none of them may survive it.
    revoked = auth.revoke_all_for_user(session, user.id)
    session.commit()
    logger.info("Password changed for %s, revoked %s session(s)", user.email, revoked)
