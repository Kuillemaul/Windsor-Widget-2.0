"""Immutable inventory-position snapshots imported from MYOB Analyse Inventory."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Unicode,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from windsor_widget.db.base import Base
from windsor_widget.db.models.audit import AppUser, new_uuid, utc_now

if TYPE_CHECKING:
    from windsor_widget.db.models.master_data import Item


class InventorySnapshot(Base):
    """An immutable point-in-time copy of MYOB inventory availability."""

    __tablename__ = "inventory_snapshots"
    __table_args__ = (
        UniqueConstraint("source_sha256", name="inventory_snapshot_source_sha256"),
        Index("ix_inventory_snapshots_current", "is_current", "captured_at"),
        Index(
            "ux_inventory_snapshots_one_current",
            "is_current",
            unique=True,
            mssql_where=text("[is_current] = 1"),
            sqlite_where=text("is_current = 1"),
        ),
    )

    inventory_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False
    )
    source_file_name: Mapped[str] = mapped_column(Unicode(500), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    committed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, default=utc_now
    )
    committed_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_users.user_id"), nullable=False
    )

    lines: Mapped[list[InventorySnapshotLine]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )
    committed_by: Mapped[AppUser] = relationship(lazy="joined")


class InventorySnapshotLine(Base):
    """One item position within an immutable inventory snapshot."""

    __tablename__ = "inventory_snapshot_lines"
    __table_args__ = (
        UniqueConstraint(
            "inventory_snapshot_id", "item_id", name="inventory_snapshot_item"
        ),
        UniqueConstraint(
            "inventory_snapshot_id",
            "source_row_number",
            name="inventory_snapshot_source_row",
        ),
        Index("ix_inventory_snapshot_lines_item", "item_id", "inventory_snapshot_id"),
    )

    inventory_snapshot_line_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    inventory_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("inventory_snapshots.inventory_snapshot_id", ondelete="CASCADE"),
        nullable=False,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("items.item_id"), nullable=False
    )
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    item_number_snapshot: Mapped[str] = mapped_column(Unicode(100), nullable=False)
    item_name_snapshot: Mapped[str] = mapped_column(Unicode(500), nullable=False)
    on_hand: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    committed: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    on_order: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    available: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)

    snapshot: Mapped[InventorySnapshot] = relationship(back_populates="lines")
    item: Mapped[Item] = relationship(lazy="joined")
