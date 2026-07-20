"""Customer, supplier and item master-data foundation.

Revision ID: 0002_master_data
Revises: 0001_stage1_foundation
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_master_data"
down_revision: str | None = "0001_stage1_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "customer_groups",
        sa.Column("customer_group_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.Unicode(length=250), nullable=False),
        sa.Column("normalized_name", sa.Unicode(length=250), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.UnicodeText(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("customer_group_id", name=op.f("pk_customer_groups")),
        sa.UniqueConstraint("normalized_name", name="normalized_name"),
    )
    op.create_index(
        "ix_customer_groups_active_name",
        "customer_groups",
        ["is_active", "display_name"],
        unique=False,
    )

    op.create_table(
        "suppliers",
        sa.Column("supplier_id", sa.Uuid(), nullable=False),
        sa.Column("myob_record_id", sa.String(length=100), nullable=True),
        sa.Column("myob_card_id", sa.String(length=100), nullable=True),
        sa.Column("display_name", sa.Unicode(length=250), nullable=False),
        sa.Column("normalized_name", sa.Unicode(length=250), nullable=False),
        sa.Column("card_status", sa.Unicode(length=50), nullable=True),
        sa.Column("contact_name", sa.Unicode(length=200), nullable=True),
        sa.Column("email", sa.Unicode(length=320), nullable=True),
        sa.Column("phone", sa.Unicode(length=100), nullable=True),
        sa.Column("default_manufacturing_lead_days", sa.Integer(), nullable=True),
        sa.Column("default_transit_lead_days", sa.Integer(), nullable=True),
        sa.Column("default_buffer_days", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "default_manufacturing_lead_days IS NULL OR "
            "default_manufacturing_lead_days >= 0",
            name=op.f("ck_suppliers_manufacturing_lead_days_nonnegative"),
        ),
        sa.CheckConstraint(
            "default_transit_lead_days IS NULL OR default_transit_lead_days >= 0",
            name=op.f("ck_suppliers_transit_lead_days_nonnegative"),
        ),
        sa.CheckConstraint(
            "default_buffer_days IS NULL OR default_buffer_days >= 0",
            name=op.f("ck_suppliers_buffer_days_nonnegative"),
        ),
        sa.PrimaryKeyConstraint("supplier_id", name=op.f("pk_suppliers")),
        sa.UniqueConstraint("myob_card_id", name=op.f("uq_suppliers_myob_card_id")),
        sa.UniqueConstraint(
            "myob_record_id", name=op.f("uq_suppliers_myob_record_id")
        ),
    )
    op.create_index(
        "ix_suppliers_name",
        "suppliers",
        ["normalized_name", "is_active"],
        unique=False,
    )

    op.create_table(
        "items",
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("item_number", sa.Unicode(length=100), nullable=False),
        sa.Column("item_name", sa.Unicode(length=500), nullable=False),
        sa.Column("normalized_name", sa.Unicode(length=500), nullable=False),
        sa.Column("description", sa.UnicodeText(), nullable=True),
        sa.Column("is_bought", sa.Boolean(), nullable=False),
        sa.Column("is_sold", sa.Boolean(), nullable=False),
        sa.Column("is_inventoried", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("excluded_from_item_view", sa.Boolean(), nullable=False),
        sa.Column("buy_unit_measure", sa.Unicode(length=100), nullable=True),
        sa.Column("sell_unit_measure", sa.Unicode(length=100), nullable=True),
        sa.Column("reorder_quantity", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("minimum_level", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("standard_cost", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("replenishment_policy", sa.String(length=30), nullable=False),
        sa.Column("policy_source", sa.String(length=20), nullable=False),
        sa.Column("policy_reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("policy_reviewed_by_user_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "policy_source IN ('unknown', 'myob', 'inferred', 'user')",
            name=op.f("ck_items_policy_source_valid"),
        ),
        sa.CheckConstraint(
            "replenishment_policy IN "
            "('unknown', 'stocked', 'make_to_order', 'manual')",
            name=op.f("ck_items_replenishment_policy_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["policy_reviewed_by_user_id"],
            ["app_users.user_id"],
            name=op.f("fk_items_policy_reviewed_by_user_id_app_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("item_id", name=op.f("pk_items")),
        sa.UniqueConstraint("item_number", name=op.f("uq_items_item_number")),
    )
    op.create_index(
        "ix_items_name", "items", ["normalized_name", "is_active"], unique=False
    )
    op.create_index(
        "ix_items_planning_view",
        "items",
        ["excluded_from_item_view", "is_active", "item_number"],
        unique=False,
    )

    op.create_table(
        "customer_accounts",
        sa.Column("customer_account_id", sa.Uuid(), nullable=False),
        sa.Column("customer_group_id", sa.Uuid(), nullable=True),
        sa.Column("myob_record_id", sa.String(length=100), nullable=True),
        sa.Column("myob_card_id", sa.String(length=100), nullable=True),
        sa.Column("display_name", sa.Unicode(length=250), nullable=False),
        sa.Column("normalized_name", sa.Unicode(length=250), nullable=False),
        sa.Column("card_status", sa.Unicode(length=50), nullable=True),
        sa.Column("address_line_1", sa.Unicode(length=250), nullable=True),
        sa.Column("city", sa.Unicode(length=150), nullable=True),
        sa.Column("state", sa.Unicode(length=100), nullable=True),
        sa.Column("postcode", sa.Unicode(length=30), nullable=True),
        sa.Column("contact_name", sa.Unicode(length=200), nullable=True),
        sa.Column("email", sa.Unicode(length=320), nullable=True),
        sa.Column("phone", sa.Unicode(length=100), nullable=True),
        sa.Column("terms_description", sa.Unicode(length=250), nullable=True),
        sa.Column("price_level", sa.Unicode(length=100), nullable=True),
        sa.Column("shipping_method", sa.Unicode(length=200), nullable=True),
        sa.Column("payment_basis", sa.String(length=20), nullable=False),
        sa.Column("freight_payer", sa.String(length=20), nullable=False),
        sa.Column("group_match_status", sa.String(length=20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "freight_payer IN ('unknown', 'customer', 'windsor')",
            name=op.f("ck_customer_accounts_freight_payer_valid"),
        ),
        sa.CheckConstraint(
            "group_match_status IN ('unmatched', 'proposed', 'approved')",
            name=op.f("ck_customer_accounts_group_match_status_valid"),
        ),
        sa.CheckConstraint(
            "payment_basis IN ('unknown', 'prepay', 'account')",
            name=op.f("ck_customer_accounts_payment_basis_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["customer_group_id"],
            ["customer_groups.customer_group_id"],
            name=op.f(
                "fk_customer_accounts_customer_group_id_customer_groups"
            ),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "customer_account_id", name=op.f("pk_customer_accounts")
        ),
        sa.UniqueConstraint(
            "myob_card_id", name=op.f("uq_customer_accounts_myob_card_id")
        ),
        sa.UniqueConstraint(
            "myob_record_id", name=op.f("uq_customer_accounts_myob_record_id")
        ),
    )
    op.create_index(
        "ix_customer_accounts_group",
        "customer_accounts",
        ["customer_group_id", "is_active"],
        unique=False,
    )
    op.create_index(
        "ix_customer_accounts_name",
        "customer_accounts",
        ["normalized_name", "is_active"],
        unique=False,
    )

    op.create_table(
        "customer_price_files",
        sa.Column("customer_price_file_id", sa.Uuid(), nullable=False),
        sa.Column("customer_group_id", sa.Uuid(), nullable=False),
        sa.Column("file_path", sa.Unicode(length=1000), nullable=False),
        sa.Column("file_name", sa.Unicode(length=500), nullable=False),
        sa.Column("match_status", sa.String(length=20), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("verified_by_user_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 100)",
            name=op.f("ck_customer_price_files_confidence_range"),
        ),
        sa.CheckConstraint(
            "match_status IN ('proposed', 'approved', 'rejected')",
            name=op.f("ck_customer_price_files_match_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["customer_group_id"],
            ["customer_groups.customer_group_id"],
            name=op.f(
                "fk_customer_price_files_customer_group_id_customer_groups"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["verified_by_user_id"],
            ["app_users.user_id"],
            name=op.f(
                "fk_customer_price_files_verified_by_user_id_app_users"
            ),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "customer_price_file_id", name=op.f("pk_customer_price_files")
        ),
        sa.UniqueConstraint(
            "customer_group_id", "file_path", name="group_file_path"
        ),
    )
    op.create_index(
        "ix_customer_price_files_group_active",
        "customer_price_files",
        ["customer_group_id", "is_active"],
        unique=False,
    )

    op.create_table(
        "item_suppliers",
        sa.Column("item_supplier_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("supplier_id", sa.Uuid(), nullable=False),
        sa.Column("supplier_item_number", sa.Unicode(length=150), nullable=True),
        sa.Column("is_preferred", sa.Boolean(), nullable=False),
        sa.Column(
            "minimum_order_quantity",
            sa.Numeric(precision=18, scale=6),
            nullable=True,
        ),
        sa.Column("manufacturing_lead_days_override", sa.Integer(), nullable=True),
        sa.Column("transit_lead_days_override", sa.Integer(), nullable=True),
        sa.Column("buffer_days_override", sa.Integer(), nullable=True),
        sa.Column("last_purchase_date", sa.Date(), nullable=True),
        sa.Column(
            "last_purchase_price", sa.Numeric(precision=18, scale=6), nullable=True
        ),
        sa.Column("last_purchase_currency", sa.String(length=10), nullable=True),
        sa.Column("match_status", sa.String(length=20), nullable=False),
        sa.Column("match_method", sa.String(length=30), nullable=False),
        sa.CheckConstraint(
            "buffer_days_override IS NULL OR buffer_days_override >= 0",
            name=op.f("ck_item_suppliers_buffer_days_nonnegative"),
        ),
        sa.CheckConstraint(
            "manufacturing_lead_days_override IS NULL OR "
            "manufacturing_lead_days_override >= 0",
            name=op.f("ck_item_suppliers_manufacturing_lead_days_nonnegative"),
        ),
        sa.CheckConstraint(
            "match_method IN ('myob_primary', 'recent_purchase', 'user')",
            name=op.f("ck_item_suppliers_match_method_valid"),
        ),
        sa.CheckConstraint(
            "match_status IN ('proposed', 'approved', 'rejected')",
            name=op.f("ck_item_suppliers_match_status_valid"),
        ),
        sa.CheckConstraint(
            "minimum_order_quantity IS NULL OR minimum_order_quantity >= 0",
            name=op.f("ck_item_suppliers_minimum_order_quantity_nonnegative"),
        ),
        sa.CheckConstraint(
            "transit_lead_days_override IS NULL OR transit_lead_days_override >= 0",
            name=op.f("ck_item_suppliers_transit_lead_days_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["items.item_id"],
            name=op.f("fk_item_suppliers_item_id_items"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["supplier_id"],
            ["suppliers.supplier_id"],
            name=op.f("fk_item_suppliers_supplier_id_suppliers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "item_supplier_id", name=op.f("pk_item_suppliers")
        ),
        sa.UniqueConstraint("item_id", "supplier_id", name="item_supplier"),
    )
    op.create_index(
        "ix_item_suppliers_preferred",
        "item_suppliers",
        ["item_id", "is_preferred"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_item_suppliers_preferred", table_name="item_suppliers")
    op.drop_table("item_suppliers")
    op.drop_index(
        "ix_customer_price_files_group_active", table_name="customer_price_files"
    )
    op.drop_table("customer_price_files")
    op.drop_index("ix_customer_accounts_name", table_name="customer_accounts")
    op.drop_index("ix_customer_accounts_group", table_name="customer_accounts")
    op.drop_table("customer_accounts")
    op.drop_index("ix_items_planning_view", table_name="items")
    op.drop_index("ix_items_name", table_name="items")
    op.drop_table("items")
    op.drop_index("ix_suppliers_name", table_name="suppliers")
    op.drop_table("suppliers")
    op.drop_index("ix_customer_groups_active_name", table_name="customer_groups")
    op.drop_table("customer_groups")
