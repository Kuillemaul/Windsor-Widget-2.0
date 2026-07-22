"""Web sign-in credentials and application roles."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Unicode, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from windsor_widget.db.base import Base
from windsor_widget.db.models.audit import AppUser, utc_now


class WebUserAccount(Base):
    """Authentication state attached to the durable application user identity."""

    __tablename__ = "web_user_accounts"
    __table_args__ = (
        CheckConstraint(
            "role IN ('admin', 'procurement', 'read_only')",
            name="web_user_role_valid",
        ),
        CheckConstraint(
            "failed_login_count >= 0",
            name="failed_login_count_nonnegative",
        ),
        Index("ix_web_user_accounts_role", "role"),
        Index("ix_web_user_accounts_locked_until", "locked_until"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("app_users.user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    password_hash: Mapped[str] = mapped_column(Unicode(500), nullable=False)
    role: Mapped[str] = mapped_column(String(30), nullable=False, default="read_only")
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, default=utc_now, onupdate=utc_now
    )

    user: Mapped[AppUser] = relationship(lazy="joined")
