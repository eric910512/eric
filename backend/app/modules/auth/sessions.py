"""Server-side sessions (api-design.md §1.5, Authentication Hardening).

A sign-in opens a session: one ``auth_tokens`` family (``family_id`` = session id, ``sid``) whose
first row is the session's refresh token (only its hash is stored). Every access token carries
the ``sid`` claim and is accepted only while its session is active, so signing out, revoking a
session, disabling the account or changing / resetting the password ends access at once instead
of when the 15-minute access token expires.

Session active = its family has a refresh row that is neither revoked nor expired. Revoking sets
``revoked_at`` on every row of the family. Callers write the audit entry and commit.

Refresh (``rotate``): the raw refresh token travels only in an HttpOnly cookie. Each refresh
marks the presented row ``used_at`` and issues a new row in the same family with the same
absolute expiry (a session lasts at most ``SESSION_DAYS`` from sign-in). Presenting a row that
was already used (a stolen / replayed token) revokes the whole family (api-design.md §1.5).
"""

import hashlib
import secrets
import uuid
from datetime import timedelta

from flask import request
from sqlalchemy import select

from app.core.timeutil import iso_utc
from app.extensions import db
from app.models import AuthToken
from app.models.base import utcnow
from app.models.enums import TokenType

SESSION_DAYS = 14  # refresh token lifetime (api-design.md §1.5)


def _hash(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def _client_ip():
    # request.remote_addr is the client address once ProxyFix has applied X-Forwarded-For
    return (request.remote_addr or "")[:45] or None


def _user_agent():
    return (request.headers.get("User-Agent") or "")[:255] or None


def open_session(user):
    """New session for ``user`` → (sid, raw refresh token). Only the hash is stored."""
    sid = str(uuid.uuid4())
    raw = secrets.token_urlsafe(48)
    db.session.add(AuthToken(
        user_id=user.id, token_type=TokenType.REFRESH, token_hash=_hash(raw), family_id=sid,
        expires_at=utcnow() + timedelta(days=SESSION_DAYS), ip_address=_client_ip(), user_agent=_user_agent(),
    ))
    db.session.flush()
    return sid, raw


class RefreshError(Exception):
    """Why a refresh token was refused (``code``: REFRESH_TOKEN_MISSING / _INVALID / _EXPIRED /
    _REVOKED / _REUSED, or ACCOUNT_DISABLED)."""

    def __init__(self, code, user=None):
        super().__init__(code)
        self.code = code
        self.user = user


def rotate(raw):
    """Exchange a refresh token for a new one in the same session → (user, sid, new raw token).
    Raises RefreshError. Caller commits (also on error: a detected reuse revokes the family)."""
    if not isinstance(raw, str) or not raw:
        raise RefreshError("REFRESH_TOKEN_MISSING")
    token = db.session.execute(
        select(AuthToken).where(AuthToken.token_hash == _hash(raw), AuthToken.token_type == TokenType.REFRESH)
    ).scalar_one_or_none()
    if token is None:
        raise RefreshError("REFRESH_TOKEN_INVALID")
    user = token.user
    now = utcnow()
    if token.revoked_at is not None:
        raise RefreshError("REFRESH_TOKEN_REVOKED", user)
    if token.used_at is not None:
        revoke(user, token.family_id)  # replay of a rotated token: end the whole session
        raise RefreshError("REFRESH_TOKEN_REUSED", user)
    if token.expires_at <= now:
        raise RefreshError("REFRESH_TOKEN_EXPIRED", user)
    if not user.is_active:
        revoke(user, token.family_id)
        raise RefreshError("ACCOUNT_DISABLED", user)
    token.used_at = now
    new_raw = secrets.token_urlsafe(48)
    db.session.add(AuthToken(
        user_id=user.id, token_type=TokenType.REFRESH, token_hash=_hash(new_raw), family_id=token.family_id,
        expires_at=token.expires_at, ip_address=_client_ip(), user_agent=_user_agent(),
    ))
    db.session.flush()
    return user, token.family_id, new_raw


def expiry_of(raw):
    """Expiry of a refresh token (the cookie's Expires)."""
    return db.session.execute(select(AuthToken.expires_at).where(AuthToken.token_hash == _hash(raw))).scalar_one()


def family_of(raw):
    """(user, sid) of the session a refresh token belongs to, or (None, None)."""
    if not isinstance(raw, str) or not raw:
        return None, None
    token = db.session.execute(
        select(AuthToken).where(AuthToken.token_hash == _hash(raw), AuthToken.token_type == TokenType.REFRESH)
    ).scalar_one_or_none()
    return (token.user, token.family_id) if token else (None, None)


def is_active(user_id, sid):
    if not isinstance(sid, str) or not sid:
        return False
    return db.session.execute(
        select(AuthToken.id).where(
            AuthToken.user_id == user_id, AuthToken.family_id == sid, AuthToken.token_type == TokenType.REFRESH,
            AuthToken.revoked_at.is_(None), AuthToken.expires_at > utcnow(),
        ).limit(1)
    ).first() is not None


def _active_families(user):
    rows = db.session.execute(
        select(AuthToken).where(
            AuthToken.user_id == user.id, AuthToken.token_type == TokenType.REFRESH,
            AuthToken.revoked_at.is_(None), AuthToken.expires_at > utcnow(),
        ).order_by(AuthToken.created_at, AuthToken.id)
    ).scalars().all()
    families = {}
    for t in rows:
        families.setdefault(t.family_id, []).append(t)
    return families


def revoke(user, sid):
    """End one session of ``user``. Returns True when it was active."""
    rows = db.session.execute(
        select(AuthToken).where(AuthToken.user_id == user.id, AuthToken.family_id == sid, AuthToken.revoked_at.is_(None))
    ).scalars().all()
    now = utcnow()
    for t in rows:
        t.revoked_at = now
    return bool(rows)


def revoke_all(user, keep=None):
    """End every session of ``user`` except ``keep`` (a sid). Returns the number ended."""
    ended = [sid for sid in _active_families(user) if sid != keep]
    for sid in ended:
        revoke(user, sid)
    return len(ended)


def active_count(user):
    return len(_active_families(user))


def list_sessions(user, current_sid=None):
    """Active sessions, current first then newest: {id, created_at (sign-in), expires_at, ip_address, user_agent, current}."""
    out = []
    for sid, rows in _active_families(user).items():
        first, last = rows[0], rows[-1]
        out.append({
            "id": sid, "created_at": iso_utc(first.created_at),
            "expires_at": iso_utc(max(t.expires_at for t in rows)), "ip_address": last.ip_address,
            "user_agent": last.user_agent, "current": sid == current_sid,
        })
    return sorted(out, key=lambda s: (s["current"], s["created_at"]), reverse=True)

