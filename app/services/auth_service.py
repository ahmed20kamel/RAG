"""Authentication: password hashing, session issue, lookup and revocation.

Two properties this file exists to guarantee. A password is never stored or compared in
plaintext, and the value handed to the browser is never the value kept in the database —
the cookie carries a random secret, the table keeps only its digest.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.core.permissions import Role
from app.exceptions import ValidationError
from app.models.auth import Session, Team, User

logger = logging.getLogger(__name__)

_hasher = PasswordHasher()

TOKEN_BYTES = 32
MIN_PASSWORD_LENGTH = 10
# `last_seen_at` is for showing someone their own sessions, not for accounting, so it
# is refreshed at this interval rather than on every request. Writing on each call
# would put a row update — and on SQLite a write lock — in front of every single
# authenticated read.
LAST_SEEN_RESOLUTION = timedelta(minutes=5)


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationError(f"كلمة المرور يجب أن تكون {MIN_PASSWORD_LENGTH} أحرف على الأقل.")
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        _hasher.verify(password_hash, password)
        return True
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when the stored hash used weaker parameters than the current policy."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def token_digest(token: str) -> str:
    """What goes in the database. A plain digest is right here: the token is already
    32 bytes of entropy, so there is nothing for an attacker to brute-force."""
    return hashlib.sha256(token.encode()).hexdigest()


class AuthService:
    def __init__(self, session_ttl_hours: int = 12) -> None:
        self.session_ttl = timedelta(hours=session_ttl_hours)

    # -- users ------------------------------------------------------------
    @staticmethod
    def normalise_email(email: str) -> str:
        return email.strip().lower()

    def create_user(
        self,
        db: DbSession,
        *,
        email: str,
        password: str,
        display_name: str = "",
        role: str = Role.VIEWER,
        team_id: str | None = None,
    ) -> User:
        email = self.normalise_email(email)
        if not email or "@" not in email:
            raise ValidationError("البريد الإلكتروني غير صالح.")
        if db.scalar(select(User).where(User.email == email)) is not None:
            raise ValidationError(f"يوجد حساب بالبريد '{email}' بالفعل.")
        try:
            role = Role(role)
        except ValueError as exc:
            raise ValidationError(f"دور غير معروف: '{role}'.") from exc

        user = User(
            id=str(uuid.uuid4()),
            email=email,
            display_name=display_name or email.split("@")[0],
            password_hash=hash_password(password),
            role=role,
            team_id=team_id,
        )
        db.add(user)
        db.flush()
        logger.info("Created user %s with role %s", email, role)
        return user

    def authenticate(self, db: DbSession, email: str, password: str) -> User | None:
        user = db.scalar(select(User).where(User.email == self.normalise_email(email)))
        if user is None:
            # Spend the same time as a real verification would, so a missing account
            # and a wrong password are not distinguishable by how long the reply takes.
            _hasher.hash(password)
            return None
        if not verify_password(user.password_hash, password):
            return None
        if not user.is_active:
            logger.info("Rejected login for deactivated account %s", user.email)
            return None
        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
        user.last_login_at = datetime.now(UTC)
        return user

    def set_password(self, user: User, password: str) -> None:
        user.password_hash = hash_password(password)

    # -- sessions ---------------------------------------------------------
    def issue_session(
        self, db: DbSession, user: User, *, user_agent: str = "", ip: str = ""
    ) -> tuple[str, Session]:
        """Returns the token for the cookie and the row that records it."""
        token = secrets.token_urlsafe(TOKEN_BYTES)
        now = datetime.now(UTC)
        row = Session(
            id=token_digest(token),
            user_id=user.id,
            created_at=now,
            last_seen_at=now,
            expires_at=now + self.session_ttl,
            user_agent=user_agent[:512],
            ip=ip[:64],
        )
        db.add(row)
        db.flush()
        return token, row

    def resolve(self, db: DbSession, token: str) -> tuple[User, Session] | None:
        """The user behind a cookie, or None when it is unknown, expired or revoked."""
        if not token:
            return None
        row = db.get(Session, token_digest(token))
        if row is None or not row.is_live:
            return None
        user = db.get(User, row.user_id)
        if user is None or not user.is_active:
            return None
        now = datetime.now(UTC)
        last_seen = row.last_seen_at
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=UTC)
        if now - last_seen > LAST_SEEN_RESOLUTION:
            row.last_seen_at = now
        return user, row

    @staticmethod
    def revoke(db: DbSession, token: str) -> None:
        row = db.get(Session, token_digest(token))
        if row is not None and row.revoked_at is None:
            row.revoked_at = datetime.now(UTC)

    @staticmethod
    def revoke_all_for_user(db: DbSession, user_id: str) -> int:
        """Used when a role changes or an account is disabled: the old sessions were
        issued under permissions that no longer apply."""
        now = datetime.now(UTC)
        rows = db.scalars(
            select(Session).where(Session.user_id == user_id, Session.revoked_at.is_(None))
        ).all()
        for row in rows:
            row.revoked_at = now
        return len(rows)

    @staticmethod
    def purge_expired(db: DbSession) -> int:
        rows = db.scalars(select(Session).where(Session.expires_at < datetime.now(UTC))).all()
        for row in rows:
            db.delete(row)
        return len(rows)

    # -- teams ------------------------------------------------------------
    @staticmethod
    def create_team(db: DbSession, name: str, department: str = "") -> Team:
        name = name.strip()
        if not name:
            raise ValidationError("اسم الفريق مطلوب.")
        if db.scalar(select(Team).where(Team.name == name)) is not None:
            raise ValidationError(f"يوجد فريق باسم '{name}' بالفعل.")
        team = Team(id=str(uuid.uuid4()), name=name, department=department.strip())
        db.add(team)
        db.flush()
        return team
