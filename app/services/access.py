"""Who may see which document.

A person sees the documents they uploaded. Whoever holds `document.read_all` — an
administrator, an integration — sees every document, including those uploaded before
documents had owners. The same rule decides the library, a document's pages and the
evidence an answer is built from, so a file that cannot be opened cannot be quoted either.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import Permission, has_permission
from app.models.database import session_scope
from app.models.document import Document

#: Matches no document. Passed as a filter when a person has no documents: an empty
#: filter would read as "no filter" and search everyone's.
NO_DOCUMENT = "__no-document__"


def sees_all(user) -> bool:
    return user is None or has_permission(getattr(user, "role", ""), Permission.DOCUMENT_READ_ALL)


def owner_filter(user) -> str | None:
    """The owner to filter by, or None when this reader sees every document."""
    return None if sees_all(user) else user.id


def can_see(user, document: Document) -> bool:
    return sees_all(user) or (document.owner_id is not None and document.owner_id == user.id)


def visible_ids(user, session: Session | None = None) -> list[str] | None:
    """Every document this reader may be answered from, or None for all of them."""
    if sees_all(user):
        return None

    def read(db: Session) -> list[str]:
        return list(db.scalars(select(Document.id).where(Document.owner_id == user.id)))

    if session is not None:
        return read(session)
    with session_scope() as db:
        return read(db)


def restrict(requested: list[str] | None, allowed: list[str] | None) -> list[str] | None:
    """The documents a question may search: what it asked for, within what is allowed.

    None means unrestricted. A reader with nothing allowed gets a filter that matches
    nothing, never an empty one.
    """
    if allowed is None:
        return requested
    allowed_set = set(allowed)
    chosen = [d for d in requested if d in allowed_set] if requested else list(allowed)
    return chosen or [NO_DOCUMENT]
