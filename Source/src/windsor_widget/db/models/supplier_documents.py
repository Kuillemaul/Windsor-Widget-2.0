"""Supplier-facing operational document settings."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Unicode,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from windsor_widget.db.base import Base
from windsor_widget.db.models.audit import AppUser, new_uuid, utc_now
from windsor_widget.db.models.master_data import Supplier


class SupplierOrderTemplate(Base):
    """One active supplier order-form workbook configuration."""

    __tablename__ = "supplier_order_templates"
    __table_args__ = (
        CheckConstraint(
            "template_kind IN ('yuchang_compact_xlsx')",
            name="supplier_order_template_kind_valid",
        ),
        UniqueConstraint("supplier_id", "template_kind", name="supplier_template_kind"),
        Index("ix_supplier_order_templates_active", "supplier_id", "is_active"),
    )

    supplier_order_template_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("suppliers.supplier_id", ondelete="CASCADE"), nullable=False
    )
    template_kind: Mapped[str] = mapped_column(
        String(50), nullable=False, default="yuchang_compact_xlsx"
    )
    folder_path: Mapped[str] = mapped_column(Unicode(1000), nullable=False)
    file_name: Mapped[str] = mapped_column(Unicode(500), nullable=False)
    worksheet_name: Mapped[str] = mapped_column(
        Unicode(100), nullable=False, default="Sheet1"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    verified_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_users.user_id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, default=utc_now, onupdate=utc_now
    )

    supplier: Mapped[Supplier] = relationship(lazy="joined")
    verified_by: Mapped[AppUser | None] = relationship(lazy="joined")
