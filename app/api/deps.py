"""FastAPI dependencies resolving services from the application container."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.container import Container
from app.core.permissions import Permission, permissions_for
from app.exceptions import AuthenticationError, AuthorizationError
from app.models.auth import User
from app.models.database import get_session
from app.services.auth_service import AuthService
from app.services.document_service import DocumentService
from app.services.rag_service import RagService


def get_container(request: Request) -> Container:
    return request.app.state.container


def get_document_service(container: Annotated[Container, Depends(get_container)]) -> DocumentService:
    return container.document_service


def get_rag_service(container: Annotated[Container, Depends(get_container)]) -> RagService:
    return container.rag_service


SessionDep = Annotated[Session, Depends(get_session)]
ContainerDep = Annotated[Container, Depends(get_container)]
DocumentServiceDep = Annotated[DocumentService, Depends(get_document_service)]
RagServiceDep = Annotated[RagService, Depends(get_rag_service)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_auth_service(container: Annotated[Container, Depends(get_container)]) -> AuthService:
    return container.auth_service


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


def get_current_user(
    request: Request,
    session: SessionDep,
    auth: AuthServiceDep,
    settings: SettingsDep,
) -> User:
    """The signed-in user, or 401.

    The cookie is the only accepted credential for a browser. `Authorization: Bearer`
    is accepted too so a non-browser client — the evaluation harness, the test suites —
    can present the same session token without needing a cookie jar.
    """
    token = request.cookies.get(settings.session_cookie_name, "")
    if not token:
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            token = header[7:].strip()

    resolved = auth.resolve(session, token)
    if resolved is None:
        raise AuthenticationError("يلزم تسجيل الدخول.")

    user, _row = resolved
    return user


CurrentUserDep = Annotated[User, Depends(get_current_user)]


def require(permission: Permission):
    """Dependency factory: the handler states the capability it needs, not the role.

    Roles change; capabilities are what the code actually depends on. Expressing the
    requirement this way means a new role is a row in the permission table and nothing
    else, and a handler can never be protected by a role check someone forgot to update.
    """

    def guard(user: CurrentUserDep) -> User:
        if permission not in permissions_for(user.role):
            raise AuthorizationError(f"لا تملك صلاحية '{permission.value}'.")
        return user

    return Depends(guard)
