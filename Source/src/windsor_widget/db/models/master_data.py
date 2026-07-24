"""Durable customer, supplier and item master-data models."""

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
    text,
    UnicodeText,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from windsor_widget.db.base import Base
from windsor_widget.db.models.audit import AppUser, new_uuid, utc_now


class CustomerGroup(Base):
    """A commercial customer group which may contain several MYOB accounts."""

    __tablename__ = "customer_groups"
    __table_args__ = (
        UniqueConstraint("normalized_name", name="normalized_name"),
        Index("ix_customer_groups_active_name", "is_active", "display_name"),
    )

    customer_group_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    display_name: Mapped[str] = mapped_column(Unicode(250), nullable=False)
    normalized_name: Mapped[str] = mapped_column(Unicode(250), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(UnicodeText)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, default=utc_now
    )

    accounts: Mapped[list[CustomerAccount]] = relationship(back_populates="group")
    price_files: Mapped[list[CustomerPriceFile]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )


class CustomerAccount(Base):
    """An individual MYOB customer card/account within a customer group."""

    __tablename__ = "customer_accounts"
    __table_args__ = (
        CheckConstraint(
            "payment_basis IN ('unknown', 'prepay', 'account')",
            name="payment_basis_valid",
        ),
        CheckConstraint(
            "freight_payer IN ('unknown', 'customer', 'windsor')",
            name="freight_payer_valid",
        ),
        CheckConstraint(
            "group_match_status IN ('unmatched', 'proposed', 'approved')",
            name="group_match_status_valid",
        ),
        Index("ix_customer_accounts_name", "normalized_name", "is_active"),
        Index("ix_customer_accounts_group", "customer_group_id", "is_active"),
        Index(
            "ux_customer_accounts_myob_record_id_not_null",
            "myob_record_id",
            unique=True,
            mssql_where=text("[myob_record_id] IS NOT NULL"),
        ),
        Index(
            "ux_customer_accounts_myob_card_id_not_null",
            "myob_card_id",
            unique=True,
            mssql_where=text("[myob_card_id] IS NOT NULL"),
        ),
    )

    customer_account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    customer_group_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customer_groups.customer_group_id", ondelete="SET NULL")
    )
    myob_record_id: Mapped[str | None] = mapped_column(String(100))
    myob_card_id: Mapped[str | None] = mapped_column(String(100))
    display_name: Mapped[str] = mapped_column(Unicode(250), nullable=False)
    normalized_name: Mapped[str] = mapped_column(Unicode(250), nullable=False)
    card_status: Mapped[str | None] = mapped_column(Unicode(50))
    address_line_1: Mapped[str | None] = mapped_column(Unicode(250))
    city: Mapped[str | None] = mapped_column(Unicode(150))
    state: Mapped[str | None] = mapped_column(Unicode(100))
    postcode: Mapped[str | None] = mapped_column(Unicode(30))
    contact_name: Mapped[str | None] = mapped_column(Unicode(200))
    email: Mapped[str | None] = mapped_column(Unicode(320))
    phone: Mapped[str | None] = mapped_column(Unicode(100))
    terms_description: Mapped[str | None] = mapped_column(Unicode(250))
    price_level: Mapped[str | None] = mapped_column(Unicode(100))
    shipping_method: Mapped[str | None] = mapped_column(Unicode(200))
    payment_basis: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unknown"
    )
    freight_payer: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unknown"
    )
    group_match_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unmatched"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))

    group: Mapped[CustomerGroup | None] = relationship(back_populates="accounts")


class CustomerPriceFile(Base):
    """A group-level link to a semi-structured customer pricing workbook."""

    __tablename__ = "customer_price_files"
    __table_args__ = (
        CheckConstraint(
            "match_status IN ('proposed', 'approved', 'rejected')",
            name="match_status_valid",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 100)",
            name="confidence_range",
        ),
        UniqueConstraint("customer_group_id", "file_path", name="group_file_path"),
        Index("ix_customer_price_files_group_active", "customer_group_id", "is_active"),
    )

    customer_price_file_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    customer_group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customer_groups.customer_group_id", ondelete="CASCADE"),
        nullable=False,
    )
    file_path: Mapped[str] = mapped_column(Unicode(1000), nullable=False)
    file_name: Mapped[str] = mapped_column(Unicode(500), nullable=False)
    match_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="proposed"
    )
    confidence: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    verified_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_users.user_id", ondelete="SET NULL")
    )

    group: Mapped[CustomerGroup] = relationship(back_populates="price_files")
    verified_by: Mapped[AppUser | None] = relationship(lazy="joined")


class Supplier(Base):
    """A MYOB supplier card with default operational lead-time settings."""

    __tablename__ = "suppliers"
    __table_args__ = (
        CheckConstraint(
            "default_manufacturing_lead_days IS NULL OR default_manufacturing_lead_days >= 0",
            name="manufacturing_lead_days_nonnegative",
        ),
        CheckConstraint(
            "default_transit_lead_days IS NULL OR default_transit_lead_days >= 0",
            name="transit_lead_days_nonnegative",
        ),
        CheckConstraint(
            "default_buffer_days IS NULL OR default_buffer_days >= 0",
            name="buffer_days_nonnegative",
        ),
        Index("ix_suppliers_name", "normalized_name", "is_active"),
        Index(
            "ux_suppliers_myob_record_id_not_null",
            "myob_record_id",
            unique=True,
            mssql_where=text("[myob_record_id] IS NOT NULL"),
        ),
        Index(
            "ux_suppliers_myob_card_id_not_null",
            "myob_card_id",
            unique=True,
            mssql_where=text("[myob_card_id] IS NOT NULL"),
        ),
    )

    supplier_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    myob_record_id: Mapped[str | None] = mapped_column(String(100))
    myob_card_id: Mapped[str | None] = mapped_column(String(100))
    display_name: Mapped[str] = mapped_column(Unicode(250), nullable=False)
    normalized_name: Mapped[str] = mapped_column(Unicode(250), nullable=False)
    card_status: Mapped[str | None] = mapped_column(Unicode(50))
    contact_name: Mapped[str | None] = mapped_column(Unicode(200))
    email: Mapped[str | None] = mapped_column(Unicode(320))
    phone: Mapped[str | None] = mapped_column(Unicode(100))
    default_manufacturing_lead_days: Mapped[int | None] = mapped_column(Integer)
    default_transit_lead_days: Mapped[int | None] = mapped_column(Integer)
    default_buffer_days: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    item_links: Mapped[list[ItemSupplier]] = relationship(back_populates="supplier")


class Item(Base):
    """A durable item master, keyed externally by the MYOB item number."""

    __tablename__ = "items"
    __table_args__ = (
        CheckConstraint(
            "replenishment_policy IN ('unknown', 'stocked', 'make_to_order', 'manual')",
            name="replenishment_policy_valid",
        ),
        CheckConstraint(
            "policy_source IN ('unknown', 'myob', 'inferred', 'user')",
            name="policy_source_valid",
        ),
        Index("ix_items_name", "normalized_name", "is_active"),
        Index(
            "ix_items_planning_view",
            "excluded_from_item_view",
            "is_active",
            "item_number",
        ),
    )

    item_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    item_number: Mapped[str] = mapped_column(Unicode(100), nullable=False, unique=True)
    item_name: Mapped[str] = mapped_column(Unicode(500), nullable=False)
    normalized_name: Mapped[str] = mapped_column(Unicode(500), nullable=False)
    description: Mapped[str | None] = mapped_column(UnicodeText)
    is_bought: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_sold: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_inventoried: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    excluded_from_item_view: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    buy_unit_measure: Mapped[str | None] = mapped_column(Unicode(100))
    sell_unit_measure: Mapped[str | None] = mapped_column(Unicode(100))
    reorder_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    minimum_level: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    standard_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    replenishment_policy: Mapped[str] = mapped_column(
        String(30), nullable=False, default="unknown"
    )
    policy_source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unknown"
    )
    policy_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    policy_reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_users.user_id", ondelete="SET NULL")
    )

    supplier_links: Mapped[list[ItemSupplier]] = relationship(back_populates="item")
    policy_reviewed_by: Mapped[AppUser | None] = relationship(lazy="joined")


class ItemSupplier(Base):
    """Supplier-specific item settings, history-derived facts and manual overrides."""

    __tablename__ = "item_suppliers"
    __table_args__ = (
        CheckConstraint(
            "match_status IN ('proposed', 'approved', 'rejected')",
            name="match_status_valid",
        ),
        CheckConstraint(
            "match_method IN ('myob_primary', 'recent_purchase', 'user')",
            name="match_method_valid",
        ),
        CheckConstraint(
            "minimum_order_quantity IS NULL OR minimum_order_quantity >= 0",
            name="minimum_order_quantity_nonnegative",
        ),
        CheckConstraint(
            "manufacturing_lead_days_override IS NULL OR manufacturing_lead_days_override >= 0",
            name="manufacturing_lead_days_nonnegative",
        ),
        CheckConstraint(
            "transit_lead_days_override IS NULL OR transit_lead_days_override >= 0",
            name="transit_lead_days_nonnegative",
        ),
        CheckConstraint(
            "buffer_days_override IS NULL OR buffer_days_override >= 0",
            name="buffer_days_nonnegative",
        ),
        CheckConstraint(
            "packing_source IN (\'unknown\', \'supplier_workbook\', \'user\')",
            name="packing_source_valid",
        ),
        UniqueConstraint("item_id", "supplier_id", name="item_supplier"),
        Index("ix_item_suppliers_preferred", "item_id", "is_preferred"),
    )

    item_supplier_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("items.item_id", ondelete="CASCADE"), nullable=False
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("suppliers.supplier_id", ondelete="CASCADE"), nullable=False
    )
    supplier_item_number: Mapped[str | None] = mapped_column(Unicode(150))
    is_preferred: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    minimum_order_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    manufacturing_lead_days_override: Mapped[int | None] = mapped_column(Integer)
    transit_lead_days_override: Mapped[int | None] = mapped_column(Integer)
    buffer_days_override: Mapped[int | None] = mapped_column(Integer)
    last_purchase_date: Mapped[date | None] = mapped_column(Date)
    last_purchase_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    last_purchase_currency: Mapped[str | None] = mapped_column(String(10))
    supplier_description_raw: Mapped[str | None] = mapped_column(UnicodeText)
    supplier_size_raw: Mapped[str | None] = mapped_column(Unicode(250))
    supplier_colour_raw: Mapped[str | None] = mapped_column(Unicode(250))
    supplier_unit_type: Mapped[str | None] = mapped_column(Unicode(100))
    packing_quantity_per_unit_raw: Mapped[str | None] = mapped_column(Unicode(100))
    roll_spool_length_metres: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    packing_quantity_per_carton_raw: Mapped[str | None] = mapped_column(Unicode(100))
    metres_per_carton: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    supplier_units_per_carton: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    supplier_label_description_raw: Mapped[str | None] = mapped_column(Unicode(500))
    packing_source: Mapped[str] = mapped_column(
        String(30), nullable=False, default="unknown"
    )
    packing_source_workbook: Mapped[str | None] = mapped_column(Unicode(1000))
    packing_source_worksheet: Mapped[str | None] = mapped_column(Unicode(100))
    packing_source_row: Mapped[int | None] = mapped_column(Integer)
    packing_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    match_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="proposed"
    )
    match_method: Mapped[str] = mapped_column(
        String(30), nullable=False, default="recent_purchase"
    )

    item: Mapped[Item] = relationship(back_populates="supplier_links")
    supplier: Mapped[Supplier] = relationship(back_populates="item_links")
