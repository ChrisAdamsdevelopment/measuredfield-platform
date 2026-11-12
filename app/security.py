"""Authentication primitives.

Nothing here invents cryptography: passwords use Argon2id through argon2-cffi, tokens
come from ``secrets`` and are stored only as SHA-256 digests, and comparisons use
``hmac.compare_digest``.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session as DbSession

from .config import get_settings
from .models import AuthToken, RateLimit, Session, User, now

_hasher = PasswordHasher()  # Argon2id, library defaults (RFC 9106 low-memory profile)
# Verified against when the email is unknown so response time does not reveal accounts.
_DUMMY_HASH = _hasher.hash("measuredfield-dummy-password")

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,180}\.[^@\s]{2,30}$")
SESSION_COOKIE = "mf_session"
ROLES = ("owner", "admin", "staff", "readonly")

# What each role may do. Checked on the server for every state-changing route.
PERMISSIONS: dict[str, set[str]] = {
    "readonly": {"read"},
    "staff": {"read", "leads.write", "customers.write", "documents.write", "site.edit",
              "memory.write", "improvements.write"},
    "admin": {"read", "leads.write", "customers.write", "documents.write", "site.edit",
              "memory.write", "improvements.write", "site.publish", "team.manage",
              "settings.write", "learning.manage", "documents.delete"},
}
PERMISSIONS["owner"] = PERMISSIONS["admin"] | {"billing.read", "data.manage", "team.owners"}


def can(role: str | None, permission: str) -> bool:
    return bool(role) and permission in PERMISSIONS.get(role, set())


def normalize_email(value: str) -> str:
    return (value or "").strip().lower()


def valid_email(value: str) -> bool:
    return bool(EMAIL_RE.fullmatch(value or "")) and len(value) <= 254


def password_problem(password: str) -> str | None:
    if len(password) < 12:
        return "Use at least 12 characters."
    if len(password) > 200:
        return "Use at most 200 characters."
    if password.lower() in {"password1234", "123456789012", "qwertyuiopas"}:
        return "Choose a less common password."
    return None


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash or _DUMMY_HASH, password) and stored_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_ip(ip: str) -> str:
    """Keyed hash so rate-limit keys and logs never hold a raw address."""
    key = get_settings().secret_key.encode()
    return hmac.new(key, (ip or "unknown").encode(), hashlib.sha256).hexdigest()[:24]


def client_address(peer: str | None, forwarded_for: str | None, trusted_hops: int) -> str:
    """Resolve the client address for rate limiting.

    ``trusted_hops`` is the number of reverse proxies that are KNOWN to sit in front of
    this process (0 = none: use the socket peer and ignore headers). Each well-behaved
    proxy appends the address it received the connection from to X-Forwarded-For, so with
    N trusted proxies the Nth entry from the RIGHT was written by our own infrastructure
    and cannot be chosen by the client; everything to its left can be forged and is
    ignored. If the chain is shorter than expected or the entry is not an IP address the
    header is not trusted and the socket peer is used.

    The correct value for a given host must be verified on that host before it is set
    (see docs/SECURITY_ASSESSMENT.md, "Client address behind the proxy").
    """
    import ipaddress
    peer = peer or "unknown"
    if trusted_hops <= 0 or not forwarded_for:
        return peer
    parts = [part.strip() for part in forwarded_for.split(",") if part.strip()]
    if len(parts) < trusted_hops:
        return peer
    candidate = parts[-trusted_hops]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return peer


# ------------------------------------------------------------------------- sessions

def create_session(db: DbSession, user: User, active_org_id: uuid.UUID | None) -> str:
    settings = get_settings()
    token = new_token()
    db.add(Session(
        token_hash=digest(token), user_id=user.id, active_org_id=active_org_id,
        csrf_secret=secrets.token_hex(32),
        expires_at=now() + timedelta(hours=settings.session_ttl_hours),
    ))
    return token


def load_session(db: DbSession, token: str | None) -> Session | None:
    if not token or len(token) > 200:
        return None
    settings = get_settings()
    row = db.scalar(select(Session).where(Session.token_hash == digest(token)))
    if row is None or row.revoked_at is not None:
        return None
    current = now()
    if row.expires_at <= current:
        return None
    if current - row.last_seen_at > timedelta(minutes=settings.session_idle_minutes):
        return None
    if current - row.last_seen_at > timedelta(minutes=1):
        row.last_seen_at = current
    return row


def revoke_user_sessions(db: DbSession, user_id: uuid.UUID, except_id: uuid.UUID | None = None) -> None:
    for row in db.scalars(select(Session).where(Session.user_id == user_id, Session.revoked_at.is_(None))):
        if row.id != except_id:
            row.revoked_at = now()


def csrf_token(session: Session) -> str:
    return hmac.new(session.csrf_secret.encode(), b"csrf", hashlib.sha256).hexdigest()


def csrf_ok(session: Session, submitted: str | None) -> bool:
    return bool(submitted) and hmac.compare_digest(csrf_token(session), submitted)


# ---------------------------------------------------------------- single-use tokens

def issue_token(db: DbSession, *, purpose: str, email: str, ttl: timedelta,
                org_id: uuid.UUID | None = None, role: str | None = None) -> str:
    token = new_token()
    db.add(AuthToken(token_hash=digest(token), purpose=purpose, email=normalize_email(email),
                     org_id=org_id, role=role, expires_at=now() + ttl))
    return token


def peek_token(db: DbSession, token: str, purpose: str) -> AuthToken | None:
    if not token or len(token) > 200:
        return None
    row = db.scalar(select(AuthToken).where(AuthToken.token_hash == digest(token)).with_for_update())
    if row is None or row.purpose != purpose or row.used_at is not None or row.expires_at <= now():
        return None
    return row


# ---------------------------------------------------------------------- rate limits

def rate_limited(db: DbSession, key: str, *, limit: int, window_seconds: int) -> bool:
    """Fixed-window counter stored in PostgreSQL so it holds across web instances.

    Returns True when the caller is over the limit. The increment is atomic.
    """
    current = datetime.now(timezone.utc)
    epoch = int(current.timestamp())
    window = datetime.fromtimestamp(epoch - epoch % window_seconds, tz=timezone.utc)
    count = db.execute(text(
        "INSERT INTO rate_limits (key, window_start, count) VALUES (:k, :w, 1) "
        "ON CONFLICT (key, window_start) DO UPDATE SET count = rate_limits.count + 1 "
        "RETURNING count"), {"k": key[:120], "w": window}).scalar_one()
    return count > limit


def purge_old_rate_limits(db: DbSession) -> None:
    db.query(RateLimit).filter(RateLimit.window_start < now() - timedelta(days=1)).delete()
