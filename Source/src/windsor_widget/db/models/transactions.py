"""Durable MYOB sales, purchase and cover-order transaction models."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
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


class SalesDocument(Base):
    """A stable MYOB sales document identity, independent of export batches."""

    __tablename__ = "sales_documents"
    __table_args__ = (
        UniqueConstraint(
            "myob_customer_record_id", "invoice_no", name="sales_customer_invoice"
        ),
        Index("ix_sales_documents_customer_date", "customer_account_id", "last_transaction_date"),
        Index("ix_sales_documents_invoice", "invoice_no"),
    )

    sales_document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    customer_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customer_accounts.customer_account_id"), nullable=False
    )
    myob_customer_record_id: Mapped[str] = mapped_column(String(100), nullable=False)
    invoice_no: Mapped[str] = mapped_column(Unicode(100), nullable=False)
    first_transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    last_transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_import_batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("import_batches.import_batch_id"), nullable=False
    )
    last_import_batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("import_batches.import_batch_id"), nullable=False
    )
    source_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, default=utc_now
    )

    lines: Mapped[list[SalesLine]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class SalesLine(Base):
    """One ordered line within a MYOB sales document."""

    __tablename__ = "sales_lines"
    __table_args__ = (
        UniqueConstraint("sales_document_id", "line_sequence", name="sales_document_sequence"),
        Index("ix_sales_lines_item_date", "item_id", "transaction_date"),
        Index("ix_sales_lines_document_active", "sales_document_id", "is_active"),
        Index("ix_sales_lines_cover", "is_cover_order", "transaction_date"),
    )

    sales_line_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    sales_document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sales_documents.sales_document_id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("items.item_id", ondelete="SET NULL")
    )
    line_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    source_import_row_id: Mapped[int] = mapped_column(
        ForeignKey("import_rows.import_row_id"), nullable=False
    )
    source_row_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    last_import_batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("import_batches.import_batch_id"), nullable=False
    )
    myob_item_number: Mapped[str | None] = mapped_column(Unicode(100))
    customer_name_snapshot: Mapped[str] = mapped_column(Unicode(250), nullable=False)
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    customer_po: Mapped[str | None] = mapped_column(Unicode(250))
    ship_via: Mapped[str | None] = mapped_column(Unicode(200))
    delivery_status: Mapped[str | None] = mapped_column(String(20))
    description: Mapped[str | None] = mapped_column(UnicodeText)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    discount_percent: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    line_total: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    inclusive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    job: Mapped[str | None] = mapped_column(Unicode(200))
    comment: Mapped[str | None] = mapped_column(UnicodeText)
    journal_memo: Mapped[str | None] = mapped_column(Unicode(500))
    shipping_date: Mapped[date | None] = mapped_column(Date)
    tax_code: Mapped[str | None] = mapped_column(String(30))
    tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    freight_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    freight_tax_code: Mapped[str | None] = mapped_column(String(30))
    freight_tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    sale_status: Mapped[str | None] = mapped_column(String(20))
    currency_code: Mapped[str | None] = mapped_column(String(10))
    exchange_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    amount_paid: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    payment_method: Mapped[str | None] = mapped_column(Unicode(100))
    category: Mapped[str | None] = mapped_column(Unicode(100))
    location_id: Mapped[str | None] = mapped_column(Unicode(100))
    card_id_snapshot: Mapped[str | None] = mapped_column(String(100))
    is_cover_order: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    document: Mapped[SalesDocument] = relationship(back_populates="lines")


class PurchaseDocument(Base):
    """A stable MYOB purchase document identity."""

    __tablename__ = "purchase_documents"
    __table_args__ = (
        UniqueConstraint(
            "myob_supplier_record_id", "purchase_no", name="purchase_supplier_number"
        ),
        Index("ix_purchase_documents_supplier_date", "supplier_id", "last_transaction_date"),
        Index("ix_purchase_documents_number", "purchase_no"),
    )

    purchase_document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("suppliers.supplier_id"), nullable=False
    )
    myob_supplier_record_id: Mapped[str] = mapped_column(String(100), nullable=False)
    purchase_no: Mapped[str] = mapped_column(Unicode(100), nullable=False)
    first_transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    last_transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_import_batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("import_batches.import_batch_id"), nullable=False
    )
    last_import_batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("import_batches.import_batch_id"), nullable=False
    )
    source_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, default=utc_now
    )

    lines: Mapped[list[PurchaseLine]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class PurchaseLine(Base):
    """One ordered line within a MYOB purchase document."""

    __tablename__ = "purchase_lines"
    __table_args__ = (
        UniqueConstraint(
            "purchase_document_id", "line_sequence", name="purchase_document_sequence"
        ),
        Index("ix_purchase_lines_item_date", "item_id", "transaction_date"),
        Index("ix_purchase_lines_document_active", "purchase_document_id", "is_active"),
        Index("ix_purchase_lines_delivery", "delivery_status", "transaction_date"),
    )

    purchase_line_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    purchase_document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("purchase_documents.purchase_document_id", ondelete="CASCADE"),
        nullable=False,
    )
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("items.item_id", ondelete="SET NULL")
    )
    line_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    source_import_row_id: Mapped[int] = mapped_column(
        ForeignKey("import_rows.import_row_id"), nullable=False
    )
    source_row_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    last_import_batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("import_batches.import_batch_id"), nullable=False
    )
    myob_item_number: Mapped[str | None] = mapped_column(Unicode(100))
    supplier_name_snapshot: Mapped[str] = mapped_column(Unicode(250), nullable=False)
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    supplier_invoice_no: Mapped[str | None] = mapped_column(Unicode(150))
    ship_via: Mapped[str | None] = mapped_column(Unicode(200))
    delivery_status: Mapped[str | None] = mapped_column(String(20))
    description: Mapped[str | None] = mapped_column(UnicodeText)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    discount_percent: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    line_total: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    inclusive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    job: Mapped[str | None] = mapped_column(Unicode(200))
    comment: Mapped[str | None] = mapped_column(UnicodeText)
    journal_memo: Mapped[str | None] = mapped_column(Unicode(500))
    shipping_date: Mapped[date | None] = mapped_column(Date)
    tax_code: Mapped[str | None] = mapped_column(String(30))
    tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    freight_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    freight_tax_code: Mapped[str | None] = mapped_column(String(30))
    freight_tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    purchase_status: Mapped[str | None] = mapped_column(String(20))
    currency_code: Mapped[str | None] = mapped_column(String(10))
    exchange_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    amount_paid: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    order_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    received_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    billed_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    category: Mapped[str | None] = mapped_column(Unicode(100))
    location_id: Mapped[str | None] = mapped_column(Unicode(100))
    card_id_snapshot: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    document: Mapped[PurchaseDocument] = relationship(back_populates="lines")


class CoverOrderSnapshot(Base):
    """An immutable point-in-time copy of MYOB open sales orders."""

    __tablename__ = "cover_order_snapshots"
    __table_args__ = (
        UniqueConstraint("import_batch_id", name="cover_snapshot_import_batch"),
        Index("ix_cover_order_snapshots_current", "is_current", "captured_at"),
        Index(
            "ux_cover_order_snapshots_one_current",
            "is_current",
            unique=True,
            mssql_where=text("[is_current] = 1"),
            sqlite_where=text("is_current = 1"),
        ),
    )

    cover_order_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    import_batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("import_batches.import_batch_id"), nullable=False
    )
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    source_file_name: Mapped[str] = mapped_column(Unicode(500), nullable=False)
    document_count: Mapped[int] = mapped_column(Integer, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    committed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, default=utc_now
    )
    committed_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_users.user_id"), nullable=False
    )

    documents: Mapped[list[CoverOrderDocument]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )
    committed_by: Mapped[AppUser] = relationship(lazy="joined")


class CoverOrderDocument(Base):
    """A customer order within one cover-order snapshot."""

    __tablename__ = "cover_order_documents"
    __table_args__ = (
        UniqueConstraint(
            "cover_order_snapshot_id",
            "myob_customer_record_id",
            "invoice_no",
            name="cover_snapshot_customer_invoice",
        ),
        Index("ix_cover_order_documents_customer", "customer_account_id", "last_transaction_date"),
    )

    cover_order_document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    cover_order_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cover_order_snapshots.cover_order_snapshot_id", ondelete="CASCADE"),
        nullable=False,
    )
    customer_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customer_accounts.customer_account_id"), nullable=False
    )
    myob_customer_record_id: Mapped[str] = mapped_column(String(100), nullable=False)
    invoice_no: Mapped[str] = mapped_column(Unicode(100), nullable=False)
    first_transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    last_transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False)

    snapshot: Mapped[CoverOrderSnapshot] = relationship(back_populates="documents")
    lines: Mapped[list[CoverOrderLine]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class CoverOrderLine(Base):
    """One open-order line within an immutable cover-order snapshot."""

    __tablename__ = "cover_order_lines"
    __table_args__ = (
        UniqueConstraint(
            "cover_order_document_id", "line_sequence", name="cover_document_sequence"
        ),
        Index("ix_cover_order_lines_item_date", "item_id", "transaction_date"),
        Index("ix_cover_order_lines_delivery", "delivery_status", "transaction_date"),
    )

    cover_order_line_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    cover_order_document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cover_order_documents.cover_order_document_id", ondelete="CASCADE"),
        nullable=False,
    )
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("items.item_id", ondelete="SET NULL")
    )
    line_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    source_import_row_id: Mapped[int] = mapped_column(
        ForeignKey("import_rows.import_row_id"), nullable=False
    )
    source_row_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    myob_item_number: Mapped[str | None] = mapped_column(Unicode(100))
    customer_name_snapshot: Mapped[str] = mapped_column(Unicode(250), nullable=False)
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    customer_po: Mapped[str | None] = mapped_column(Unicode(250))
    ship_via: Mapped[str | None] = mapped_column(Unicode(200))
    delivery_status: Mapped[str | None] = mapped_column(String(20))
    description: Mapped[str | None] = mapped_column(UnicodeText)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    discount_percent: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    line_total: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    inclusive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    job: Mapped[str | None] = mapped_column(Unicode(200))
    comment: Mapped[str | None] = mapped_column(UnicodeText)
    journal_memo: Mapped[str | None] = mapped_column(Unicode(500))
    shipping_date: Mapped[date | None] = mapped_column(Date)
    tax_code: Mapped[str | None] = mapped_column(String(30))
    tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    freight_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    freight_tax_code: Mapped[str | None] = mapped_column(String(30))
    freight_tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    sale_status: Mapped[str | None] = mapped_column(String(20))
    amount_paid: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    payment_method: Mapped[str | None] = mapped_column(Unicode(100))
    category: Mapped[str | None] = mapped_column(Unicode(100))
    location_id: Mapped[str | None] = mapped_column(Unicode(100))
    card_id_snapshot: Mapped[str | None] = mapped_column(String(100))
    is_cover_order: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    document: Mapped[CoverOrderDocument] = relationship(back_populates="lines")


class TransactionLineObservation(Base):
    """Append-only lineage from each staged row to its operational line."""

    __tablename__ = "transaction_line_observations"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('sales_transactions', 'cover_order_snapshot', 'purchase_transactions')",
            name="source_type_valid",
        ),
        CheckConstraint(
            "entity_type IN ('sales_line', 'cover_order_line', 'purchase_line')",
            name="entity_type_valid",
        ),
        CheckConstraint(
            "action IN ('created', 'updated', 'unchanged', 'reactivated')",
            name="action_valid",
        ),
        UniqueConstraint("import_row_id", name="transaction_observation_import_row"),
        Index("ix_transaction_line_observations_entity", "entity_type", "entity_id"),
        Index("ix_transaction_line_observations_batch", "import_batch_id", "source_type"),
    )

    transaction_line_observation_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    import_batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("import_batches.import_batch_id"), nullable=False
    )
    import_row_id: Mapped[int] = mapped_column(
        ForeignKey("import_rows.import_row_id"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, default=utc_now
    )
