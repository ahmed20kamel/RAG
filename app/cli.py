"""Administrative commands.

Bootstrap accounts are created here rather than at import time, so a running server
never creates a privileged account on its own and no default password is ever baked
into the image.

    python -m app.cli bootstrap
    python -m app.cli create-user <email> <role>
    python -m app.cli reset-password <email>
    python -m app.cli list-users

    python -m app.cli integration-master-key
    python -m app.cli integration-create <name> <tenant_id> [service-email]
    python -m app.cli integration-list
    python -m app.cli integration-rotate <key_id>
    python -m app.cli integration-revoke <key_id>
"""

from __future__ import annotations

import getpass
import sys

from sqlalchemy import select

from app.config import get_settings
from app.core.permissions import Role
from app.models.auth import User
from app.models.database import session_scope
from app.services.auth_service import AuthService

auth = AuthService()


def _prompt_password(label: str) -> str:
    first = getpass.getpass(f"{label}: ")
    second = getpass.getpass("Confirm: ")
    if first != second:
        print("Passwords do not match.", file=sys.stderr)
        raise SystemExit(1)
    return first


def _ensure(db, email: str, password: str, role: str, display_name: str) -> str:
    existing = db.scalar(select(User).where(User.email == auth.normalise_email(email)))
    if existing is not None:
        return f"  exists   {existing.email:32} {existing.role}"
    auth.create_user(db, email=email, password=password, role=role, display_name=display_name)
    return f"  created  {email:32} {role}"


def bootstrap() -> None:
    """Creates the first administrator and the service account from the environment."""
    settings = get_settings()
    if not settings.bootstrap_admin_email or not settings.bootstrap_admin_password:
        print(
            "Set BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD before bootstrapping.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if not settings.service_account_password:
        print("Set SERVICE_ACCOUNT_PASSWORD before bootstrapping.", file=sys.stderr)
        raise SystemExit(1)

    with session_scope() as db:
        print(_ensure(db, settings.bootstrap_admin_email, settings.bootstrap_admin_password,
                      Role.ADMIN, "Administrator"))
        print(_ensure(db, settings.service_account_email, settings.service_account_password,
                      Role.SERVICE, "Evaluation harness"))


def create_user(email: str, role: str = Role.VIEWER) -> None:
    password = _prompt_password("Password")
    with session_scope() as db:
        auth.create_user(db, email=email, password=password, role=role)
    print(f"created {email} ({role})")


def reset_password(email: str) -> None:
    password = _prompt_password("New password")
    with session_scope() as db:
        user = db.scalar(select(User).where(User.email == auth.normalise_email(email)))
        if user is None:
            print(f"No user '{email}'.", file=sys.stderr)
            raise SystemExit(1)
        auth.set_password(user, password)
        revoked = auth.revoke_all_for_user(db, user.id)
    print(f"password reset for {email}; revoked {revoked} session(s)")


def list_users() -> None:
    with session_scope() as db:
        rows = db.scalars(select(User).order_by(User.created_at)).all()
        if not rows:
            print("no users — run `python -m app.cli bootstrap`")
            return
        for u in rows:
            state = "active" if u.is_active else "disabled"
            print(f"  {u.email:36} {u.role:20} {state}")


# ---------------------------------------------------------------------------
# ERP integration credentials
#
# Kept here rather than behind an HTTP endpoint on purpose. Minting a credential that
# can question the whole corpus is an administrative act performed by a person with
# access to the machine, and an API that issues its own credentials is one compromised
# admin session away from issuing them to somebody else.
# ---------------------------------------------------------------------------


def _service_user(db, email: str):
    from app.models.auth import User

    user = db.scalar(select(User).where(User.email == auth.normalise_email(email)))
    if user is None:
        print(f"No user '{email}' — run `python -m app.cli bootstrap` first.", file=sys.stderr)
        raise SystemExit(1)
    if user.role != Role.SERVICE:
        print(
            f"'{email}' carries the role '{user.role}', not '{Role.SERVICE}'. "
            "An integration credential must resolve to a service account, so a leaked "
            "key cannot teach the system, delete a document, or read who else exists.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return user


def integration_master_key() -> None:
    """Prints a fresh master key. Not stored anywhere by this command."""
    from app.services.integration_auth import new_master_key

    print(new_master_key())
    print()
    print("Put this in .env as INTEGRATION_MASTER_KEY and keep a copy in a password", file=sys.stderr)
    print("manager. It is the root of every integration credential: losing it", file=sys.stderr)
    print("invalidates all of them, and holding it is equivalent to holding all of them.", file=sys.stderr)


def integration_create(name: str, tenant_id: str, service_email: str = "") -> None:
    from app.services.integration_auth import create_client

    settings = get_settings()
    if not settings.integration_master_key:
        print("Set INTEGRATION_MASTER_KEY first.", file=sys.stderr)
        raise SystemExit(1)

    email = service_email or settings.service_account_email
    with session_scope() as db:
        user = _service_user(db, email)
        client, key, secret = create_client(
            db,
            master_key=settings.integration_master_key,
            name=name,
            tenant_id=tenant_id,
            service_user_id=user.id,
            rate_limit_per_minute=settings.integration_rate_limit_per_minute,
            rate_limit_burst=settings.integration_rate_limit_burst,
            max_concurrency=settings.integration_max_concurrency,
            daily_quota=settings.integration_daily_quota,
        )
        key_id = client.key_id

    print(f"client      {name}")
    print(f"tenant      {tenant_id}")
    print(f"key_id      {key_id}")
    print(f"key         {key}          <- Authorization: Bearer <this>")
    print(f"secret      {secret}")
    print()
    print("The secret signs requests and is never sent with one. It is shown here once;", file=sys.stderr)
    print("the database holds no copy, only a fingerprint.", file=sys.stderr)


def integration_list() -> None:
    from app.models.integration import IntegrationClient

    with session_scope() as db:
        rows = db.scalars(
            select(IntegrationClient).order_by(IntegrationClient.created_at)
        ).all()
        if not rows:
            print("no integration clients")
            return
        for row in rows:
            state = "active" if row.is_usable else "revoked"
            used = row.last_used_at.strftime("%Y-%m-%d %H:%M") if row.last_used_at else "never"
            print(
                f"  {row.key_id}  {row.name:24} tenant={row.tenant_id:16} "
                f"v{row.secret_version} {state:8} last_used={used}"
            )


def _client_or_exit(db, key_id: str):
    from app.models.integration import IntegrationClient

    client = db.scalar(select(IntegrationClient).where(IntegrationClient.key_id == key_id))
    if client is None:
        print(f"No integration client with key_id '{key_id}'.", file=sys.stderr)
        raise SystemExit(1)
    return client


def integration_rotate(key_id: str) -> None:
    from app.services.integration_auth import ROTATION_OVERLAP, rotate_client

    settings = get_settings()
    if not settings.integration_master_key:
        print("Set INTEGRATION_MASTER_KEY first.", file=sys.stderr)
        raise SystemExit(1)

    with session_scope() as db:
        client = _client_or_exit(db, key_id)
        secret = rotate_client(db, client, master_key=settings.integration_master_key)
        version = client.secret_version

    print(f"key_id      {key_id}")
    print(f"version     {version}")
    print(f"secret      {secret}")
    print()
    print(
        f"The previous secret keeps working for {ROTATION_OVERLAP.days} days, so the "
        "caller can deploy this one without a restart coordinated across both systems.",
        file=sys.stderr,
    )


def integration_revoke(key_id: str) -> None:
    from app.services.integration_auth import revoke_client

    with session_scope() as db:
        client = _client_or_exit(db, key_id)
        revoke_client(db, client)
    print(f"revoked {key_id}")


COMMANDS = {
    "bootstrap": bootstrap,
    "create-user": create_user,
    "reset-password": reset_password,
    "list-users": list_users,
    "integration-master-key": integration_master_key,
    "integration-create": integration_create,
    "integration-list": integration_list,
    "integration-rotate": integration_rotate,
    "integration-revoke": integration_revoke,
}


def main(argv: list[str]) -> None:
    if not argv or argv[0] not in COMMANDS:
        print(__doc__)
        raise SystemExit(2 if argv else 0)
    COMMANDS[argv[0]](*argv[1:])


if __name__ == "__main__":
    main(sys.argv[1:])
