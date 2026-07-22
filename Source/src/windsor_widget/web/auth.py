"""Password hashing, sign-in throttling and web principals."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from windsor_widget.db.models import AppUser, AuditEvent, WebUserAccount
from windsor_widget.db.models.audit import utc_now

_PASSWORD_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
_MAX_FAILED_LOGINS = 5
_LOCK_DURATION = timedelta(minutes=15)
_ALLOWED_ROLES = frozenset({"admin", "procurement", "read_only"})


class AuthenticationError(RuntimeError):
    """Raised when an authentication operation cannot be completed safely."""


@dataclass(frozen=True, slots=True)
class WebPrincipal:
    user_id: uuid.UUID
    username: str
    display_name: str
    email: str | None
    role: str
    must_change_password: bool

    @property
    def can_manage_users(self) -> bool:
        return self.role == "admin"

    @property
    def can_change_operations(self) -> bool:
        return self.role in {"admin", "procurement"}


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise AuthenticationError("Passwords must contain at least 12 characters.")
    return _PASSWORD_HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> tuple[bool, str | None]:
    try:
        verified = _PASSWORD_HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False, None
    if not verified:
        return False, None
    replacement = _PASSWORD_HASHER.hash(password) if _PASSWORD_HASHER.check_needs_rehash(password_hash) else None
    return True, replacement


def _principal(user: AppUser, account: WebUserAccount) -> WebPrincipal:
    return WebPrincipal(
        user_id=user.user_id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        role=account.role,
        must_change_password=account.must_change_password,
    )


def get_principal(session: Session, user_id: str | uuid.UUID | None) -> WebPrincipal | None:
    if not user_id:
        return None
    try:
        resolved = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    except (TypeError, ValueError):
        return None
    row = session.execute(
        select(AppUser, WebUserAccount)
        .join(WebUserAccount, WebUserAccount.user_id == AppUser.user_id)
        .where(AppUser.user_id == resolved, AppUser.is_active.is_(True))
    ).one_or_none()
    if row is None:
        return None
    return _principal(*row)


def authenticate_user(
    session: Session,
    *,
    username: str,
    password: str,
    now: datetime | None = None,
) -> WebPrincipal | None:
    current_time = now or utc_now()
    normalized = username.strip().casefold()
    row = session.execute(
        select(AppUser, WebUserAccount)
        .join(WebUserAccount, WebUserAccount.user_id == AppUser.user_id)
        .where(func.lower(AppUser.username) == normalized)
    ).one_or_none()
    if row is None:
        return None

    user, account = row
    if not user.is_active:
        return None
    if account.locked_until is not None and account.locked_until > current_time:
        return None

    verified, replacement_hash = verify_password(account.password_hash, password)
    if not verified:
        account.failed_login_count += 1
        if account.failed_login_count >= _MAX_FAILED_LOGINS:
            account.locked_until = current_time + _LOCK_DURATION
            account.failed_login_count = 0
        account.updated_at = current_time
        session.flush()
        return None

    account.failed_login_count = 0
    account.locked_until = None
    account.last_login_at = current_time
    account.updated_at = current_time
    if replacement_hash is not None:
        account.password_hash = replacement_hash
    session.add(
        AuditEvent(
            actor_user_id=user.user_id,
            action="web.login",
            entity_type="app_user",
            entity_id=str(user.user_id),
            source="web",
            summary=f"{user.display_name} signed in to Windsor Widget.",
        )
    )
    session.flush()
    return _principal(user, account)


def upsert_web_user(
    session: Session,
    *,
    username: str,
    display_name: str,
    password: str,
    role: str,
    email: str | None = None,
    actor_user_id: uuid.UUID | None = None,
) -> WebPrincipal:
    normalized_username = username.strip()
    normalized_role = role.strip().lower()
    if not normalized_username:
        raise AuthenticationError("Username is required.")
    if not display_name.strip():
        raise AuthenticationError("Display name is required.")
    if normalized_role not in _ALLOWED_ROLES:
        raise AuthenticationError("Role must be admin, procurement or read_only.")

    user = session.scalar(
        select(AppUser).where(func.lower(AppUser.username) == normalized_username.casefold())
    )
    created = user is None
    if user is None:
        user = AppUser(
            username=normalized_username,
            display_name=display_name.strip(),
            email=email.strip() if email else None,
            is_active=True,
        )
        session.add(user)
        session.flush()
    else:
        user.display_name = display_name.strip()
        user.email = email.strip() if email else user.email
        user.is_active = True

    account = session.get(WebUserAccount, user.user_id)
    password_hash = hash_password(password)
    if account is None:
        account = WebUserAccount(
            user_id=user.user_id,
            password_hash=password_hash,
            role=normalized_role,
            must_change_password=False,
            failed_login_count=0,
        )
        session.add(account)
    else:
        account.password_hash = password_hash
        account.role = normalized_role
        account.must_change_password = False
        account.failed_login_count = 0
        account.locked_until = None
        account.updated_at = utc_now()

    session.add(
        AuditEvent(
            actor_user_id=actor_user_id or user.user_id,
            action="web_user.created" if created else "web_user.updated",
            entity_type="web_user_account",
            entity_id=str(user.user_id),
            source="administration",
            summary=f"Web access configured for {user.display_name} as {normalized_role}.",
        )
    )
    session.flush()
    return _principal(user, account)
