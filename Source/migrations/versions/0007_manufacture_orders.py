"""Add manufacture orders, allocation purpose and bring-in requests.

Revision ID: 0007_manufacture_orders
Revises: 0006_web_accounts
Create Date: 2026-07-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_manufacture_orders"
down_revision: str | None = "0006_web_accounts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "manufacture_orders",
        sa.Column("manufacture_order_id", sa.Uuid(), nullable=False),
        sa.Column("supplier_id", sa.Uuid(), nullable=False),
        sa.Column("source_purchase_document_id", sa.Uuid(), nullable=True),
        sa.Column("order_number", sa.Unicode(length=100), nullable=False),
        sa.Column("order_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("expected_ready_date", sa.Date(), nullable=True),
        sa.Column("supplier_reference", sa.Unicode(length=150), nullable=True),
        sa.Column("notes", sa.UnicodeText(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'sent', 'in_production', 'ready', 'closed', 'cancelled')",
            name=op.f("ck_manufacture_orders_manufacture_order_status_valid"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_manufacture_orders_manufacture_order_version_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["supplier_id"],
            ["suppliers.supplier_id"],
            name=op.f("fk_manufacture_orders_supplier_id_suppliers"),
        ),
        sa.ForeignKeyConstraint(
            ["source_purchase_document_id"],
            ["purchase_documents.purchase_document_id"],
            name=op.f(
                "fk_manufacture_orders_source_purchase_document_id_purchase_documents"
            ),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["app_users.user_id"],
            name=op.f("fk_manufacture_orders_created_by_user_id_app_users"),
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["app_users.user_id"],
            name=op.f("fk_manufacture_orders_updated_by_user_id_app_users"),
        ),
        sa.PrimaryKeyConstraint(
            "manufacture_order_id", name=op.f("pk_manufacture_orders")
        ),
        sa.UniqueConstraint(
            "supplier_id", "order_number", name="manufacture_supplier_number"
        ),
    )
    op.create_index(
        "ix_manufacture_orders_supplier_status",
        "manufacture_orders",
        ["supplier_id", "status", "expected_ready_date"],
        unique=False,
    )
    op.create_index(
        "ix_manufacture_orders_number",
        "manufacture_orders",
        ["order_number"],
        unique=False,
    )
    op.create_index(
        "ux_manufacture_orders_source_purchase_document",
        "manufacture_orders",
        ["source_purchase_document_id"],
        unique=True,
        mssql_where=sa.text("[source_purchase_document_id] IS NOT NULL"),
        sqlite_where=sa.text("source_purchase_document_id IS NOT NULL"),
    )

    op.create_table(
        "manufacture_order_lines",
        sa.Column("manufacture_order_line_id", sa.Uuid(), nullable=False),
        sa.Column("manufacture_order_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("source_purchase_line_id", sa.Uuid(), nullable=True),
        sa.Column("line_sequence", sa.Integer(), nullable=False),
        sa.Column("ordered_quantity", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("cancelled_quantity", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column(
            "supplier_ready_quantity", sa.Numeric(precision=18, scale=6), nullable=True
        ),
        sa.Column("expected_ready_date", sa.Date(), nullable=True),
        sa.Column("readiness_override", sa.String(length=30), nullable=False),
        sa.Column("supplier_status_note", sa.UnicodeText(), nullable=True),
        sa.Column("unit_cost", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("currency_code", sa.String(length=10), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "ordered_quantity > 0",
            name=op.f("ck_manufacture_order_lines_manufacture_line_ordered_positive"),
        ),
        sa.CheckConstraint(
            "cancelled_quantity >= 0",
            name=op.f("ck_manufacture_order_lines_manufacture_line_cancelled_nonnegative"),
        ),
        sa.CheckConstraint(
            "cancelled_quantity <= ordered_quantity",
            name=op.f(
                "ck_manufacture_order_lines_manufacture_line_cancelled_not_over_ordered"
            ),
        ),
        sa.CheckConstraint(
            "supplier_ready_quantity IS NULL OR supplier_ready_quantity >= 0",
            name=op.f("ck_manufacture_order_lines_manufacture_line_ready_nonnegative"),
        ),
        sa.CheckConstraint(
            "supplier_ready_quantity IS NULL OR supplier_ready_quantity "
            "<= ordered_quantity - cancelled_quantity",
            name=op.f(
                "ck_manufacture_order_lines_manufacture_line_ready_not_over_remaining"
            ),
        ),
        sa.CheckConstraint(
            "readiness_override IN ('auto', 'delayed', 'partially_ready', "
            "'confirmed_ready', 'cancelled')",
            name=op.f(
                "ck_manufacture_order_lines_manufacture_line_readiness_override_valid"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["manufacture_order_id"],
            ["manufacture_orders.manufacture_order_id"],
            name=op.f(
                "fk_manufacture_order_lines_manufacture_order_id_manufacture_orders"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["items.item_id"],
            name=op.f("fk_manufacture_order_lines_item_id_items"),
        ),
        sa.ForeignKeyConstraint(
            ["source_purchase_line_id"],
            ["purchase_lines.purchase_line_id"],
            name=op.f(
                "fk_manufacture_order_lines_source_purchase_line_id_purchase_lines"
            ),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "manufacture_order_line_id", name=op.f("pk_manufacture_order_lines")
        ),
        sa.UniqueConstraint(
            "manufacture_order_id",
            "line_sequence",
            name="manufacture_order_line_sequence",
        ),
    )
    op.create_index(
        "ix_manufacture_order_lines_item",
        "manufacture_order_lines",
        ["item_id", "expected_ready_date"],
        unique=False,
    )
    op.create_index(
        "ix_manufacture_order_lines_order",
        "manufacture_order_lines",
        ["manufacture_order_id", "line_sequence"],
        unique=False,
    )
    op.create_index(
        "ux_manufacture_order_lines_source_purchase_line",
        "manufacture_order_lines",
        ["source_purchase_line_id"],
        unique=True,
        mssql_where=sa.text("[source_purchase_line_id] IS NOT NULL"),
        sqlite_where=sa.text("source_purchase_line_id IS NOT NULL"),
    )

    op.create_table(
        "manufacture_line_allocations",
        sa.Column("manufacture_line_allocation_id", sa.Uuid(), nullable=False),
        sa.Column("manufacture_order_line_id", sa.Uuid(), nullable=False),
        sa.Column("allocation_type", sa.String(length=30), nullable=False),
        sa.Column("customer_account_id", sa.Uuid(), nullable=True),
        sa.Column("quantity", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("customer_reference", sa.Unicode(length=250), nullable=True),
        sa.Column("notes", sa.UnicodeText(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "quantity > 0",
            name=op.f(
                "ck_manufacture_line_allocations_manufacture_allocation_quantity_positive"
            ),
        ),
        sa.CheckConstraint(
            "allocation_type IN ('general_stock', 'customer_cover', 'mto')",
            name=op.f(
                "ck_manufacture_line_allocations_manufacture_allocation_type_valid"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["manufacture_order_line_id"],
            ["manufacture_order_lines.manufacture_order_line_id"],
            name=op.f(
                "fk_manufacture_line_allocations_manufacture_order_line_id_manufacture_order_lines"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["customer_account_id"],
            ["customer_accounts.customer_account_id"],
            name=op.f(
                "fk_manufacture_line_allocations_customer_account_id_customer_accounts"
            ),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "manufacture_line_allocation_id",
            name=op.f("pk_manufacture_line_allocations"),
        ),
    )
    op.create_index(
        "ix_manufacture_line_allocations_line",
        "manufacture_line_allocations",
        ["manufacture_order_line_id", "allocation_type"],
        unique=False,
    )
    op.create_index(
        "ix_manufacture_line_allocations_customer",
        "manufacture_line_allocations",
        ["customer_account_id", "allocation_type"],
        unique=False,
    )

    op.create_table(
        "bring_in_requests",
        sa.Column("bring_in_request_id", sa.Uuid(), nullable=False),
        sa.Column("supplier_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("source_manufacture_order_line_id", sa.Uuid(), nullable=True),
        sa.Column("requested_quantity", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("priority", sa.String(length=20), nullable=False),
        sa.Column("target_shipment_date", sa.Date(), nullable=True),
        sa.Column("reason", sa.UnicodeText(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("cancelled_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "requested_quantity > 0",
            name=op.f("ck_bring_in_requests_bring_in_requested_positive"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'allocated', 'completed', 'cancelled')",
            name=op.f("ck_bring_in_requests_bring_in_status_valid"),
        ),
        sa.CheckConstraint(
            "priority IN ('manual', 'amber', 'red')",
            name=op.f("ck_bring_in_requests_bring_in_priority_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["supplier_id"],
            ["suppliers.supplier_id"],
            name=op.f("fk_bring_in_requests_supplier_id_suppliers"),
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["items.item_id"],
            name=op.f("fk_bring_in_requests_item_id_items"),
        ),
        sa.ForeignKeyConstraint(
            ["source_manufacture_order_line_id"],
            ["manufacture_order_lines.manufacture_order_line_id"],
            name=op.f(
                "fk_bring_in_requests_source_manufacture_order_line_id_manufacture_order_lines"
            ),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["app_users.user_id"],
            name=op.f("fk_bring_in_requests_created_by_user_id_app_users"),
        ),
        sa.ForeignKeyConstraint(
            ["cancelled_by_user_id"],
            ["app_users.user_id"],
            name=op.f("fk_bring_in_requests_cancelled_by_user_id_app_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "bring_in_request_id", name=op.f("pk_bring_in_requests")
        ),
    )
    op.create_index(
        "ix_bring_in_requests_status_supplier",
        "bring_in_requests",
        ["status", "supplier_id", "target_shipment_date"],
        unique=False,
    )
    op.create_index(
        "ix_bring_in_requests_item",
        "bring_in_requests",
        ["item_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_bring_in_requests_item", table_name="bring_in_requests")
    op.drop_index("ix_bring_in_requests_status_supplier", table_name="bring_in_requests")
    op.drop_table("bring_in_requests")

    op.drop_index(
        "ix_manufacture_line_allocations_customer",
        table_name="manufacture_line_allocations",
    )
    op.drop_index(
        "ix_manufacture_line_allocations_line",
        table_name="manufacture_line_allocations",
    )
    op.drop_table("manufacture_line_allocations")

    op.drop_index(
        "ux_manufacture_order_lines_source_purchase_line",
        table_name="manufacture_order_lines",
    )
    op.drop_index("ix_manufacture_order_lines_order", table_name="manufacture_order_lines")
    op.drop_index("ix_manufacture_order_lines_item", table_name="manufacture_order_lines")
    op.drop_table("manufacture_order_lines")

    op.drop_index(
        "ux_manufacture_orders_source_purchase_document",
        table_name="manufacture_orders",
    )
    op.drop_index("ix_manufacture_orders_number", table_name="manufacture_orders")
    op.drop_index(
        "ix_manufacture_orders_supplier_status", table_name="manufacture_orders"
    )
    op.drop_table("manufacture_orders")
