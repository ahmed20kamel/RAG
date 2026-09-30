"""Machine authentication: who is calling, and is this exact request genuine.

Two different questions, and the reason both are answered here rather than by a bearer
token alone. A token proves *who*. On a network where traffic is readable — which is
what plain HTTP inside a building means — a captured request can be replayed by anyone
who saw it, and a proxy in the path can change its body without either end noticing. A
signature over the body, the time and a one-use nonce answers *is this request genuine*,
which is the question that actually protects the data.

**Where the secret lives.** The approved contract said the secret would be sent as a
bearer credential and stored as a SHA-256 digest, the way session tokens are. Those two
cannot both hold. Verifying an HMAC needs the shared key itself, and a digest is by
construction not the key; and a secret that travels in a header can be read and re-used
by anything in the path, which would leave the signature proving only that its holder
could copy a header. Either way the signature becomes decoration.

So the secret does not travel and is not stored. It is derived when needed:

    secret = base64url( HMAC-SHA256(master_key, "<key_id>:<version>") )

The master key lives in the environment, never in the database. A dump of the database
yields key ids and fingerprints and no way to sign anything. The request carries only
the key id; possession of the secret is proved by the signature and by nothing else.

The operational consequence is stated rather than hidden: the master key is the root of
every credential. Losing it invalidates all of them, and holding it is equivalent to
holding all of them. That is the same bargain any signing scheme makes, and it is worth
making here because the alternative was a signature that checked nothing.

None of this replaces transport encryption. HMAC signs; it does not conceal. Supplier
prices in a request body travel in the clear over HTTP however well they are signed, so
`integration_require_https` defaults to on and this module refuses plaintext.

Nothing here logs a secret, a signature or a nonce. `key_id` is the only part of a
credential that may appear in a log line.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from app.core.permissions import Role
from app.exceptions import IntegrationAuthError, TransportError
from app.models.auth import User
from app.models.database import session_scope
from app.models.integration import IntegrationClient, IntegrationNonce

logger = logging.getLogger(__name__)

KEY_PREFIX = "rag_sk_"
KEY_ID_BYTES = 16
#: Shorter than this and a master key is worth guessing.
MIN_MASTER_KEY_CHARS = 32
#: How long a rotated secret keeps working, so a caller can deploy the new one without
#: a restart coordinated across two systems.
ROTATION_OVERLAP = timedelta(days=7)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class MasterKeyMissing(RuntimeError):
    """No master key is configured, so no credential can be minted or verified."""


def derive_secret(master_key: str, key_id: str, version: int) -> str:
    """The shared secret for one credential at one version.

    Deterministic on purpose: it is what lets the server verify a signature without
    keeping anything that could be stolen from the database.
    """
    if not master_key or len(master_key) < MIN_MASTER_KEY_CHARS:
        raise MasterKeyMissing(
            "INTEGRATION_MASTER_KEY غير مضبوط أو أقصر من الحد الأدنى."
        )
    material = f"{key_id}:{version}".encode()
    digest = hmac.new(master_key.encode("utf-8"), material, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def new_master_key() -> str:
    """A fresh master key, for `python -m app.cli integration-master-key`."""
    return secrets.token_urlsafe(48)


def secret_digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def body_digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def presentable_key(key_id: str) -> str:
    """What a caller puts in `Authorization`. Public, and safe to log."""
    return f"{KEY_PREFIX}{key_id}"


def parse_key_id(raw: str) -> str:
    """The key id out of an Authorization header.

    Returns an empty string rather than raising: an unparseable credential and a wrong
    one both end in the same 401, because distinguishing them tells an attacker which
    half they got right.
    """
    raw = (raw or "").strip()
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    if not raw.startswith(KEY_PREFIX):
        return ""
    key_id = raw[len(KEY_PREFIX):].strip()
    # A key id is hex. Anything else is not one, and accepting it would let a caller
    # smuggle a separator into a field the signature material is built from.
    if not key_id or len(key_id) > 32 or any(c not in "0123456789abcdef" for c in key_id):
        return ""
    return key_id


def string_to_sign(method: str, path: str, body_sha256: str, timestamp: str, nonce: str) -> str:
    """The canonical material, in the one order both sides agreed on.

    Newline-separated and fixed. Any scheme in which a field could contain the separator,
    or the order could be inferred, lets two different requests produce the same string
    to sign — and a signature covering two requests covers neither.
    """
    return "\n".join([method.upper(), path, body_sha256, timestamp, nonce])


def sign(secret: str, material: str) -> str:
    return hmac.new(secret.encode("utf-8"), material.encode("utf-8"), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class PresentedCredential:
    """The pieces a signed request must carry. The secret is not among them."""

    authorization: str
    key_id_header: str
    timestamp: str
    nonce: str
    signature: str
    body: bytes

    @property
    def complete(self) -> bool:
        return bool(self.authorization and self.timestamp and self.nonce and self.signature)


@dataclass(frozen=True)
class AuthenticatedClient:
    """A verified caller, resolved to the identity retrieval will actually see."""

    client_id: str
    name: str
    tenant_id: str
    key_id: str
    user: User
    allowed_scopes: tuple[str, ...]
    web_search_allowed: bool
    rate_limit_per_minute: int
    rate_limit_burst: int
    max_concurrency: int
    daily_quota: int


class IntegrationAuthenticator:
    """Verifies a signed request and resolves it to a service identity.

    The order of checks is deliberate: cheap and non-committal first, and the nonce
    recorded last — *after* the signature holds — so nobody can burn a legitimate
    caller's nonce space by replaying garbage at it.
    """

    def __init__(
        self,
        *,
        master_key: str = "",
        require_https: bool = True,
        skew_seconds: int = 300,
        nonce_ttl_seconds: int = 600,
    ) -> None:
        self.master_key = master_key
        self.require_https = require_https
        self.skew_seconds = skew_seconds
        self.nonce_ttl_seconds = nonce_ttl_seconds

    # -- transport --------------------------------------------------------
    def check_transport(self, scheme: str, forwarded_proto: str = "") -> None:
        if not self.require_https:
            return
        effective = (forwarded_proto or scheme or "").split(",")[0].strip().lower()
        if effective != "https":
            raise TransportError("هذه الواجهة لا تقبل الطلبات إلا عبر HTTPS.")

    # -- credential -------------------------------------------------------
    def authenticate(
        self, db: DbSession, presented: PresentedCredential, *, method: str, path: str
    ) -> AuthenticatedClient:
        if not presented.complete:
            raise IntegrationAuthError("بيانات المصادقة ناقصة.", error_code="AUTH_MISSING")

        key_id = parse_key_id(presented.authorization)
        if not key_id:
            raise IntegrationAuthError("بيانات المصادقة غير صالحة.")

        # A mismatching X-RAG-Key-Id is a caller bug rather than an attack, but letting
        # it pass would mean the logs attribute a call to a key that did not sign it.
        if presented.key_id_header and presented.key_id_header != key_id:
            raise IntegrationAuthError("بيانات المصادقة غير صالحة.")

        client = db.scalar(select(IntegrationClient).where(IntegrationClient.key_id == key_id))
        if client is None:
            raise IntegrationAuthError("بيانات المصادقة غير صالحة.")
        if not client.is_usable:
            raise IntegrationAuthError("هذا المفتاح مُبطَل أو غير نشط.", error_code="KEY_REVOKED")

        self._check_timestamp(presented.timestamp)

        material = string_to_sign(
            method, path, body_digest(presented.body), presented.timestamp, presented.nonce
        )
        if not self._signature_holds(client, material, presented.signature):
            raise IntegrationAuthError("التوقيع غير صالح.", error_code="SIGNATURE_INVALID")

        # Recorded only once everything else held, and recorded by inserting rather than
        # by looking up: the uniqueness constraint closes the window in which two copies
        # of one captured request could both pass.
        self._claim_nonce(db, key_id, presented.nonce)

        user = db.scalar(select(User).where(User.id == client.service_user_id))
        if user is None or not user.is_active:
            raise IntegrationAuthError(
                "حساب الخدمة المرتبط بهذا المفتاح غير متاح.",
                error_code="SERVICE_ACCOUNT_UNAVAILABLE",
            )
        # Checked per request rather than trusted from creation time. A role that
        # drifted upward after the key was issued is exactly the case worth catching.
        if user.role != Role.SERVICE:
            raise IntegrationAuthError(
                "حساب الخدمة المرتبط بهذا المفتاح لا يحمل دور الخدمة.",
                error_code="SERVICE_ACCOUNT_UNAVAILABLE",
            )

        client.last_used_at = _utcnow()

        return AuthenticatedClient(
            client_id=client.id,
            name=client.name,
            tenant_id=client.tenant_id,
            key_id=client.key_id,
            user=user,
            allowed_scopes=tuple(client.allowed_scopes or ()),
            web_search_allowed=bool(client.web_search_allowed),
            rate_limit_per_minute=client.rate_limit_per_minute,
            rate_limit_burst=client.rate_limit_burst,
            max_concurrency=client.max_concurrency,
            daily_quota=client.daily_quota,
        )

    # -- pieces -----------------------------------------------------------
    def _check_timestamp(self, raw: str) -> None:
        try:
            sent = int(raw)
        except (TypeError, ValueError) as exc:
            raise IntegrationAuthError(
                "الطابع الزمني غير صالح.", error_code="CLOCK_SKEW"
            ) from exc
        drift = abs(int(_utcnow().timestamp()) - sent)
        if drift > self.skew_seconds:
            raise IntegrationAuthError(
                f"الطابع الزمني خارج النافذة المسموحة ({self.skew_seconds} ثانية).",
                error_code="CLOCK_SKEW",
            )

    def _signature_holds(
        self, client: IntegrationClient, material: str, presented: str
    ) -> bool:
        """True when the signature matches the current secret, or a rotating predecessor.

        Both comparisons are constant-time. A fingerprint mismatch on the current version
        is reported as its own failure rather than as a bad signature, because "the
        master key in this environment is not the one this credential was minted under"
        and "someone is forging requests" want completely different responses.
        """
        try:
            current = derive_secret(self.master_key, client.key_id, client.secret_version)
        except MasterKeyMissing as exc:
            raise IntegrationAuthError(
                "خدمة التكامل غير مهيأة على هذا الخادم.",
                error_code="MASTER_KEY_MISSING",
            ) from exc

        if client.secret_fingerprint and not hmac.compare_digest(
            secret_digest(current), client.secret_fingerprint
        ):
            logger.error(
                "Master key mismatch for integration key %s — the configured key is not "
                "the one this credential was issued under",
                client.key_id,
            )
            raise IntegrationAuthError(
                "مفتاح التكامل لا يطابق المفتاح الرئيسي المهيأ على هذا الخادم.",
                error_code="MASTER_KEY_MISMATCH",
            )

        if hmac.compare_digest(sign(current, material), presented or ""):
            return True

        if client.previous_version is not None and client.previous_expires_at is not None:
            expires = client.previous_expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=UTC)
            if expires > _utcnow():
                previous = derive_secret(self.master_key, client.key_id, client.previous_version)
                if hmac.compare_digest(sign(previous, material), presented or ""):
                    logger.info(
                        "Key %s authenticated with its previous secret during rotation",
                        client.key_id,
                    )
                    return True

        return False

    def _claim_nonce(self, _db: DbSession, key_id: str, nonce: str) -> None:
        """Burns the nonce in its own transaction, whatever happens afterwards.

        This deliberately does not use the caller's session. Recorded there, the claim
        would be rolled back by any later failure in the same request — a tenant
        mismatch, a spent quota, a validation error — and the captured request that
        caused it would become replayable. Replay protection that only holds when the
        request succeeds is not replay protection: an attacker replays what they
        captured, and what they captured is as likely to have failed as not.

        Found by the acceptance test: the first tenant-mismatched call rolled its nonce
        back, so the identical second call authenticated cleanly.
        """
        if len(nonce) > 64:
            raise IntegrationAuthError("قيمة nonce أطول من المسموح.")

        cutoff = _utcnow() - timedelta(seconds=self.nonce_ttl_seconds)
        try:
            with session_scope() as own:
                own.execute(delete(IntegrationNonce).where(IntegrationNonce.seen_at < cutoff))
                own.add(IntegrationNonce(key_id=key_id, nonce=nonce, seen_at=_utcnow()))
        except IntegrityError as exc:
            raise IntegrationAuthError(
                "تم استخدام هذا الطلب من قبل.", error_code="REPLAY_DETECTED"
            ) from exc


# ---------------------------------------------------------------------------
# Administration — used by the CLI, never by a request handler.
# ---------------------------------------------------------------------------


def create_client(
    db: DbSession,
    *,
    master_key: str,
    name: str,
    tenant_id: str,
    service_user_id: str,
    allowed_scopes: list[str] | None = None,
    web_search_allowed: bool = False,
    rate_limit_per_minute: int = 6,
    rate_limit_burst: int = 10,
    max_concurrency: int = 2,
    daily_quota: int = 500,
) -> tuple[IntegrationClient, str, str]:
    """Registers a caller. Returns the row, its public key id, and the secret.

    The secret is shown once, here, and is never reconstructible from the database
    alone afterwards — deriving it again needs the master key.
    """
    key_id = secrets.token_hex(KEY_ID_BYTES)
    secret = derive_secret(master_key, key_id, 1)
    client = IntegrationClient(
        id=str(uuid.uuid4()),
        name=name,
        tenant_id=tenant_id,
        key_id=key_id,
        secret_version=1,
        secret_fingerprint=secret_digest(secret),
        service_user_id=service_user_id,
        allowed_scopes=allowed_scopes or ["global"],
        web_search_allowed=web_search_allowed,
        rate_limit_per_minute=rate_limit_per_minute,
        rate_limit_burst=rate_limit_burst,
        max_concurrency=max_concurrency,
        daily_quota=daily_quota,
    )
    db.add(client)
    db.flush()
    logger.info("Registered integration client %s for tenant %s", key_id, tenant_id)
    return client, presentable_key(key_id), secret


def rotate_client(db: DbSession, client: IntegrationClient, *, master_key: str) -> str:
    """Issues the next secret, keeping the previous one valid for the overlap window."""
    client.previous_version = client.secret_version
    client.previous_expires_at = _utcnow() + ROTATION_OVERLAP
    client.secret_version += 1
    secret = derive_secret(master_key, client.key_id, client.secret_version)
    client.secret_fingerprint = secret_digest(secret)
    client.rotated_at = _utcnow()
    db.flush()
    logger.info("Rotated secret for integration client %s", client.key_id)
    return secret


def revoke_client(db: DbSession, client: IntegrationClient) -> None:
    """Stops the credential working, now. One write, with no waiting for an expiry."""
    client.is_active = False
    client.revoked_at = _utcnow()
    client.previous_version = None
    client.previous_expires_at = None
    db.flush()
    logger.warning("Revoked integration client %s (%s)", client.key_id, client.name)
