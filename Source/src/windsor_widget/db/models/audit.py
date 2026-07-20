"""Application identity and append-only audit event models."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Unicode, UnicodeText, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from windsor_widget.db.base import Base


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


def utc_now() -> datetime:
    """Return a naive UTC timestamp for the database's timezone-free columns."""

    return datetime.now(UTC).replace(tzinfo=None)


class AppUser(Base):
    __tablename__ = "app_users"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(Unicode(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, default=utc_now
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_entity", "entity_type", "entity_id", "occurred_at"),
        Index("ix_audit_events_correlation_id", "correlation_id"),
    )

    audit_event_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, default=utc_now
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_users.user_id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(100), nullable=False)
    correlation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, default=new_uuid)
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="application")
    summary: Mapped[str | None] = mapped_column(Unicode(500))
    before_json: Mapped[str | None] = mapped_column(UnicodeText)
    after_json: Mapped[str | None] = mapped_column(UnicodeText)

    actor: Mapped[AppUser | None] = relationship(lazy="joined")
