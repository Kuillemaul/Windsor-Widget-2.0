"""Operational manufacture-order and bring-in planning models."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Unicode,
    UnicodeText,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from windsor_widget.db.base import Base
from windsor_widget.db.models.audit import AppUser, new_uuid, utc_now
from windsor_widget.db.models.master_data import CustomerAccount, Item, Supplier
from windsor_widget.db.models.transactions import PurchaseDocument, PurchaseLine


class ManufactureOrder(Base):
    """A supplier instruction to manufacture or hold product for future shipment."""

    __tablename__ = "manufacture_orders"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'sent', 'in_production', 'ready', 'closed', 'cancelled')",
            name="manufacture_order_status_valid",
        ),
        CheckConstraint("version >= 1", name="manufacture_order_version_positive"),
        UniqueConstraint("supplier_id", "order_number", name="manufacture_supplier_number"),
        Index(
            "ix_manufacture_orders_supplier_status",
            "supplier_id",
            "status",
            "expected_ready_date",
        ),
        Index("ix_manufacture_orders_number", "order_number"),
        Index(
            "ux_manufacture_orders_source_purchase_document",
            "source_purchase_document_id",
            unique=True,
            mssql_where=text("[source_purchase_document_id] IS NOT NULL"),
            sqlite_where=text("source_purchase_document_id IS NOT NULL"),
        ),
    )

    manufacture_order_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("suppliers.supplier_id"), nullable=False
    )
    source_purchase_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("purchase_documents.purchase_document_id", ondelete="SET NULL")
    )
    order_number: Mapped[str] = mapped_column(Unicode(100), nullable=False)
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")
    expected_ready_date: Mapped[date | None] = mapped_column(Date)
    supplier_reference: Mapped[str | None] = mapped_column(Unicode(150))
    notes: Mapped[str | None] = mapped_column(UnicodeText)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_users.user_id"), nullable=False
    )
    updated_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_users.user_id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, default=utc_now, onupdate=utc_now
    )

    supplier: Mapped[Supplier] = relationship(lazy="joined")
    source_purchase_document: Mapped[PurchaseDocument | None] = relationship(lazy="joined")
    lines: Mapped[list[ManufactureOrderLine]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="ManufactureOrderLine.line_sequence",
    )
    created_by: Mapped[AppUser] = relationship(
        foreign_keys=[created_by_user_id], lazy="joined"
    )
    updated_by: Mapped[AppUser] = relationship(
        foreign_keys=[updated_by_user_id], lazy="joined"
    )


class ManufactureOrderLine(Base):
    """One item quantity on a manufacture order."""

    __tablename__ = "manufacture_order_lines"
    __table_args__ = (
        CheckConstraint("ordered_quantity > 0", name="manufacture_line_ordered_positive"),
        CheckConstraint("cancelled_quantity >= 0", name="manufacture_line_cancelled_nonnegative"),
        CheckConstraint(
            "cancelled_quantity <= ordered_quantity",
            name="manufacture_line_cancelled_not_over_ordered",
        ),
        CheckConstraint(
            "supplier_ready_quantity IS NULL OR supplier_ready_quantity >= 0",
            name="manufacture_line_ready_nonnegative",
        ),
        CheckConstraint(
            "supplier_ready_quantity IS NULL OR supplier_ready_quantity "
            "<= ordered_quantity - cancelled_quantity",
            name="manufacture_line_ready_not_over_remaining",
        ),
        CheckConstraint(
            "readiness_override IN ('auto', 'delayed', 'partially_ready', "
            "'confirmed_ready', 'cancelled')",
            name="manufacture_line_readiness_override_valid",
        ),
        UniqueConstraint(
            "manufacture_order_id", "line_sequence", name="manufacture_order_line_sequence"
        ),
        Index("ix_manufacture_order_lines_item", "item_id", "expected_ready_date"),
        Index("ix_manufacture_order_lines_order", "manufacture_order_id", "line_sequence"),
        Index(
            "ux_manufacture_order_lines_source_purchase_line",
            "source_purchase_line_id",
            unique=True,
            mssql_where=text("[source_purchase_line_id] IS NOT NULL"),
            sqlite_where=text("source_purchase_line_id IS NOT NULL"),
        ),
    )

    manufacture_order_line_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    manufacture_order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("manufacture_orders.manufacture_order_id", ondelete="CASCADE"),
        nullable=False,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("items.item_id"), nullable=False
    )
    source_purchase_line_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("purchase_lines.purchase_line_id", ondelete="SET NULL")
    )
    line_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    ordered_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    cancelled_quantity: Mapped[Decimal] = mapped_column(
        Numeric(18, 6), nullable=False, default=Decimal("0")
    )
    supplier_ready_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    expected_ready_date: Mapped[date | None] = mapped_column(Date)
    readiness_override: Mapped[str] = mapped_column(
        String(30), nullable=False, default="auto"
    )
    supplier_status_note: Mapped[str | None] = mapped_column(UnicodeText)
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    currency_code: Mapped[str | None] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, default=utc_now, onupdate=utc_now
    )

    order: Mapped[ManufactureOrder] = relationship(back_populates="lines")
    item: Mapped[Item] = relationship(lazy="joined")
    source_purchase_line: Mapped[PurchaseLine | None] = relationship(lazy="joined")
    allocations: Mapped[list[ManufactureLineAllocation]] = relationship(
        back_populates="line", cascade="all, delete-orphan"
    )
    bring_in_requests: Mapped[list[BringInRequest]] = relationship(back_populates="source_line")


class ManufactureLineAllocation(Base):
    """The business purpose/customer split attached to a manufacture-order line."""

    __tablename__ = "manufacture_line_allocations"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="manufacture_allocation_quantity_positive"),
        CheckConstraint(
            "allocation_type IN ('general_stock', 'customer_cover', 'mto')",
            name="manufacture_allocation_type_valid",
        ),
        Index(
            "ix_manufacture_line_allocations_line",
            "manufacture_order_line_id",
            "allocation_type",
        ),
        Index(
            "ix_manufacture_line_allocations_customer",
            "customer_account_id",
            "allocation_type",
        ),
    )

    manufacture_line_allocation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    manufacture_order_line_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("manufacture_order_lines.manufacture_order_line_id", ondelete="CASCADE"),
        nullable=False,
    )
    allocation_type: Mapped[str] = mapped_column(String(30), nullable=False)
    customer_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customer_accounts.customer_account_id", ondelete="SET NULL")
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    customer_reference: Mapped[str | None] = mapped_column(Unicode(250))
    notes: Mapped[str | None] = mapped_column(UnicodeText)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, default=utc_now
    )

    line: Mapped[ManufactureOrderLine] = relationship(back_populates="allocations")
    customer: Mapped[CustomerAccount | None] = relationship(lazy="joined")


class BringInRequest(Base):
    """An item-level request to consider product for a future shipment.

    The request deliberately does not reserve its source manufacture-order line. Stage 2
    can allocate the requested quantity FIFO across any suitable open supplier order.
    """

    __tablename__ = "bring_in_requests"
    __table_args__ = (
        CheckConstraint("requested_quantity > 0", name="bring_in_requested_positive"),
        CheckConstraint(
            "status IN ('active', 'allocated', 'completed', 'cancelled')",
            name="bring_in_status_valid",
        ),
        CheckConstraint(
            "priority IN ('manual', 'amber', 'red')",
            name="bring_in_priority_valid",
        ),
        Index(
            "ix_bring_in_requests_status_supplier",
            "status",
            "supplier_id",
            "target_shipment_date",
        ),
        Index("ix_bring_in_requests_item", "item_id", "status"),
    )

    bring_in_request_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("suppliers.supplier_id"), nullable=False
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("items.item_id"), nullable=False
    )
    source_manufacture_order_line_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "manufacture_order_lines.manufacture_order_line_id", ondelete="SET NULL"
        )
    )
    requested_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    target_shipment_date: Mapped[date | None] = mapped_column(Date)
    reason: Mapped[str | None] = mapped_column(UnicodeText)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_users.user_id"), nullable=False
    )
    cancelled_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_users.user_id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, default=utc_now, onupdate=utc_now
    )

    supplier: Mapped[Supplier] = relationship(lazy="joined")
    item: Mapped[Item] = relationship(lazy="joined")
    source_line: Mapped[ManufactureOrderLine | None] = relationship(
        back_populates="bring_in_requests"
    )
    created_by: Mapped[AppUser] = relationship(
        foreign_keys=[created_by_user_id], lazy="joined"
    )
    cancelled_by: Mapped[AppUser | None] = relationship(
        foreign_keys=[cancelled_by_user_id], lazy="joined"
    )
