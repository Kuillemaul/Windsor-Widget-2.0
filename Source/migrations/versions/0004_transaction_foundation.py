"""Durable sales, purchase and cover-order transaction foundation.

Revision ID: 0004_transaction_foundation
Revises: 0003_nullable_external_ids
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_transaction_foundation"
down_revision: str | None = "0003_nullable_external_ids"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sales_documents",
        sa.Column("sales_document_id", sa.Uuid(), nullable=False),
        sa.Column("customer_account_id", sa.Uuid(), nullable=False),
        sa.Column("myob_customer_record_id", sa.String(length=100), nullable=False),
        sa.Column("invoice_no", sa.Unicode(length=100), nullable=False),
        sa.Column("first_transaction_date", sa.Date(), nullable=False),
        sa.Column("last_transaction_date", sa.Date(), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column("first_import_batch_id", sa.Uuid(), nullable=False),
        sa.Column("last_import_batch_id", sa.Uuid(), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["customer_account_id"],
            ["customer_accounts.customer_account_id"],
            name=op.f("fk_sales_documents_customer_account_id_customer_accounts"),
        ),
        sa.ForeignKeyConstraint(
            ["first_import_batch_id"],
            ["import_batches.import_batch_id"],
            name=op.f("fk_sales_documents_first_import_batch_id_import_batches"),
        ),
        sa.ForeignKeyConstraint(
            ["last_import_batch_id"],
            ["import_batches.import_batch_id"],
            name=op.f("fk_sales_documents_last_import_batch_id_import_batches"),
        ),
        sa.PrimaryKeyConstraint("sales_document_id", name=op.f("pk_sales_documents")),
        sa.UniqueConstraint(
            "myob_customer_record_id", "invoice_no", name="sales_customer_invoice"
        ),
    )
    op.create_index(
        "ix_sales_documents_customer_date",
        "sales_documents",
        ["customer_account_id", "last_transaction_date"],
        unique=False,
    )
    op.create_index(
        "ix_sales_documents_invoice", "sales_documents", ["invoice_no"], unique=False
    )

    op.create_table(
        "purchase_documents",
        sa.Column("purchase_document_id", sa.Uuid(), nullable=False),
        sa.Column("supplier_id", sa.Uuid(), nullable=False),
        sa.Column("myob_supplier_record_id", sa.String(length=100), nullable=False),
        sa.Column("purchase_no", sa.Unicode(length=100), nullable=False),
        sa.Column("first_transaction_date", sa.Date(), nullable=False),
        sa.Column("last_transaction_date", sa.Date(), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column("first_import_batch_id", sa.Uuid(), nullable=False),
        sa.Column("last_import_batch_id", sa.Uuid(), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["supplier_id"],
            ["suppliers.supplier_id"],
            name=op.f("fk_purchase_documents_supplier_id_suppliers"),
        ),
        sa.ForeignKeyConstraint(
            ["first_import_batch_id"],
            ["import_batches.import_batch_id"],
            name=op.f("fk_purchase_documents_first_import_batch_id_import_batches"),
        ),
        sa.ForeignKeyConstraint(
            ["last_import_batch_id"],
            ["import_batches.import_batch_id"],
            name=op.f("fk_purchase_documents_last_import_batch_id_import_batches"),
        ),
        sa.PrimaryKeyConstraint(
            "purchase_document_id", name=op.f("pk_purchase_documents")
        ),
        sa.UniqueConstraint(
            "myob_supplier_record_id", "purchase_no", name="purchase_supplier_number"
        ),
    )
    op.create_index(
        "ix_purchase_documents_supplier_date",
        "purchase_documents",
        ["supplier_id", "last_transaction_date"],
        unique=False,
    )
    op.create_index(
        "ix_purchase_documents_number",
        "purchase_documents",
        ["purchase_no"],
        unique=False,
    )

    op.create_table(
        "cover_order_snapshots",
        sa.Column("cover_order_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("import_batch_id", sa.Uuid(), nullable=False),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("source_file_name", sa.Unicode(length=500), nullable=False),
        sa.Column("document_count", sa.Integer(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("committed_at", sa.DateTime(), nullable=False),
        sa.Column("committed_by_user_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["committed_by_user_id"],
            ["app_users.user_id"],
            name=op.f("fk_cover_order_snapshots_committed_by_user_id_app_users"),
        ),
        sa.ForeignKeyConstraint(
            ["import_batch_id"],
            ["import_batches.import_batch_id"],
            name=op.f("fk_cover_order_snapshots_import_batch_id_import_batches"),
        ),
        sa.PrimaryKeyConstraint(
            "cover_order_snapshot_id", name=op.f("pk_cover_order_snapshots")
        ),
        sa.UniqueConstraint("import_batch_id", name="cover_snapshot_import_batch"),
    )
    op.create_index(
        "ix_cover_order_snapshots_current",
        "cover_order_snapshots",
        ["is_current", "captured_at"],
        unique=False,
    )
    op.create_index(
        "ux_cover_order_snapshots_one_current",
        "cover_order_snapshots",
        ["is_current"],
        unique=True,
        mssql_where=sa.text("[is_current] = 1"),
        sqlite_where=sa.text("is_current = 1"),
    )

    op.create_table(
        "sales_lines",
        sa.Column("sales_line_id", sa.Uuid(), nullable=False),
        sa.Column("sales_document_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=True),
        sa.Column("line_sequence", sa.Integer(), nullable=False),
        sa.Column("source_import_row_id", sa.Integer(), nullable=False),
        sa.Column("source_row_sha256", sa.String(length=64), nullable=False),
        sa.Column("last_import_batch_id", sa.Uuid(), nullable=False),
        sa.Column("myob_item_number", sa.Unicode(length=100), nullable=True),
        sa.Column("customer_name_snapshot", sa.Unicode(length=250), nullable=False),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("customer_po", sa.Unicode(length=250), nullable=True),
        sa.Column("ship_via", sa.Unicode(length=200), nullable=True),
        sa.Column("delivery_status", sa.String(length=20), nullable=True),
        sa.Column("description", sa.UnicodeText(), nullable=True),
        sa.Column("quantity", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("discount_percent", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("line_total", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("inclusive", sa.Boolean(), nullable=False),
        sa.Column("job", sa.Unicode(length=200), nullable=True),
        sa.Column("comment", sa.UnicodeText(), nullable=True),
        sa.Column("journal_memo", sa.Unicode(length=500), nullable=True),
        sa.Column("shipping_date", sa.Date(), nullable=True),
        sa.Column("tax_code", sa.String(length=30), nullable=True),
        sa.Column("tax_amount", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("freight_amount", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("freight_tax_code", sa.String(length=30), nullable=True),
        sa.Column("freight_tax_amount", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("sale_status", sa.String(length=20), nullable=True),
        sa.Column("currency_code", sa.String(length=10), nullable=True),
        sa.Column("exchange_rate", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("amount_paid", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("payment_method", sa.Unicode(length=100), nullable=True),
        sa.Column("category", sa.Unicode(length=100), nullable=True),
        sa.Column("location_id", sa.Unicode(length=100), nullable=True),
        sa.Column("card_id_snapshot", sa.String(length=100), nullable=True),
        sa.Column("is_cover_order", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["items.item_id"],
            name=op.f("fk_sales_lines_item_id_items"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["last_import_batch_id"],
            ["import_batches.import_batch_id"],
            name=op.f("fk_sales_lines_last_import_batch_id_import_batches"),
        ),
        sa.ForeignKeyConstraint(
            ["sales_document_id"],
            ["sales_documents.sales_document_id"],
            name=op.f("fk_sales_lines_sales_document_id_sales_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_import_row_id"],
            ["import_rows.import_row_id"],
            name=op.f("fk_sales_lines_source_import_row_id_import_rows"),
        ),
        sa.PrimaryKeyConstraint("sales_line_id", name=op.f("pk_sales_lines")),
        sa.UniqueConstraint(
            "sales_document_id", "line_sequence", name="sales_document_sequence"
        ),
    )
    op.create_index(
        "ix_sales_lines_item_date", "sales_lines", ["item_id", "transaction_date"], unique=False
    )
    op.create_index(
        "ix_sales_lines_document_active",
        "sales_lines",
        ["sales_document_id", "is_active"],
        unique=False,
    )
    op.create_index(
        "ix_sales_lines_cover", "sales_lines", ["is_cover_order", "transaction_date"], unique=False
    )

    op.create_table(
        "purchase_lines",
        sa.Column("purchase_line_id", sa.Uuid(), nullable=False),
        sa.Column("purchase_document_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=True),
        sa.Column("line_sequence", sa.Integer(), nullable=False),
        sa.Column("source_import_row_id", sa.Integer(), nullable=False),
        sa.Column("source_row_sha256", sa.String(length=64), nullable=False),
        sa.Column("last_import_batch_id", sa.Uuid(), nullable=False),
        sa.Column("myob_item_number", sa.Unicode(length=100), nullable=True),
        sa.Column("supplier_name_snapshot", sa.Unicode(length=250), nullable=False),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("supplier_invoice_no", sa.Unicode(length=150), nullable=True),
        sa.Column("ship_via", sa.Unicode(length=200), nullable=True),
        sa.Column("delivery_status", sa.String(length=20), nullable=True),
        sa.Column("description", sa.UnicodeText(), nullable=True),
        sa.Column("quantity", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("discount_percent", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("line_total", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("inclusive", sa.Boolean(), nullable=False),
        sa.Column("job", sa.Unicode(length=200), nullable=True),
        sa.Column("comment", sa.UnicodeText(), nullable=True),
        sa.Column("journal_memo", sa.Unicode(length=500), nullable=True),
        sa.Column("shipping_date", sa.Date(), nullable=True),
        sa.Column("tax_code", sa.String(length=30), nullable=True),
        sa.Column("tax_amount", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("freight_amount", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("freight_tax_code", sa.String(length=30), nullable=True),
        sa.Column("freight_tax_amount", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("purchase_status", sa.String(length=20), nullable=True),
        sa.Column("currency_code", sa.String(length=10), nullable=True),
        sa.Column("exchange_rate", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("amount_paid", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("order_quantity", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("received_quantity", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("billed_quantity", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("category", sa.Unicode(length=100), nullable=True),
        sa.Column("location_id", sa.Unicode(length=100), nullable=True),
        sa.Column("card_id_snapshot", sa.String(length=100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["items.item_id"],
            name=op.f("fk_purchase_lines_item_id_items"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["last_import_batch_id"],
            ["import_batches.import_batch_id"],
            name=op.f("fk_purchase_lines_last_import_batch_id_import_batches"),
        ),
        sa.ForeignKeyConstraint(
            ["purchase_document_id"],
            ["purchase_documents.purchase_document_id"],
            name=op.f("fk_purchase_lines_purchase_document_id_purchase_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_import_row_id"],
            ["import_rows.import_row_id"],
            name=op.f("fk_purchase_lines_source_import_row_id_import_rows"),
        ),
        sa.PrimaryKeyConstraint("purchase_line_id", name=op.f("pk_purchase_lines")),
        sa.UniqueConstraint(
            "purchase_document_id", "line_sequence", name="purchase_document_sequence"
        ),
    )
    op.create_index(
        "ix_purchase_lines_item_date",
        "purchase_lines",
        ["item_id", "transaction_date"],
        unique=False,
    )
    op.create_index(
        "ix_purchase_lines_document_active",
        "purchase_lines",
        ["purchase_document_id", "is_active"],
        unique=False,
    )
    op.create_index(
        "ix_purchase_lines_delivery",
        "purchase_lines",
        ["delivery_status", "transaction_date"],
        unique=False,
    )

    op.create_table(
        "cover_order_documents",
        sa.Column("cover_order_document_id", sa.Uuid(), nullable=False),
        sa.Column("cover_order_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("customer_account_id", sa.Uuid(), nullable=False),
        sa.Column("myob_customer_record_id", sa.String(length=100), nullable=False),
        sa.Column("invoice_no", sa.Unicode(length=100), nullable=False),
        sa.Column("first_transaction_date", sa.Date(), nullable=False),
        sa.Column("last_transaction_date", sa.Date(), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["cover_order_snapshot_id"],
            ["cover_order_snapshots.cover_order_snapshot_id"],
            name=op.f("fk_cover_order_documents_cover_order_snapshot_id_cover_order_snapshots"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["customer_account_id"],
            ["customer_accounts.customer_account_id"],
            name=op.f("fk_cover_order_documents_customer_account_id_customer_accounts"),
        ),
        sa.PrimaryKeyConstraint(
            "cover_order_document_id", name=op.f("pk_cover_order_documents")
        ),
        sa.UniqueConstraint(
            "cover_order_snapshot_id",
            "myob_customer_record_id",
            "invoice_no",
            name="cover_snapshot_customer_invoice",
        ),
    )
    op.create_index(
        "ix_cover_order_documents_customer",
        "cover_order_documents",
        ["customer_account_id", "last_transaction_date"],
        unique=False,
    )

    op.create_table(
        "cover_order_lines",
        sa.Column("cover_order_line_id", sa.Uuid(), nullable=False),
        sa.Column("cover_order_document_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=True),
        sa.Column("line_sequence", sa.Integer(), nullable=False),
        sa.Column("source_import_row_id", sa.Integer(), nullable=False),
        sa.Column("source_row_sha256", sa.String(length=64), nullable=False),
        sa.Column("myob_item_number", sa.Unicode(length=100), nullable=True),
        sa.Column("customer_name_snapshot", sa.Unicode(length=250), nullable=False),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("customer_po", sa.Unicode(length=250), nullable=True),
        sa.Column("ship_via", sa.Unicode(length=200), nullable=True),
        sa.Column("delivery_status", sa.String(length=20), nullable=True),
        sa.Column("description", sa.UnicodeText(), nullable=True),
        sa.Column("quantity", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("discount_percent", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("line_total", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("inclusive", sa.Boolean(), nullable=False),
        sa.Column("job", sa.Unicode(length=200), nullable=True),
        sa.Column("comment", sa.UnicodeText(), nullable=True),
        sa.Column("journal_memo", sa.Unicode(length=500), nullable=True),
        sa.Column("shipping_date", sa.Date(), nullable=True),
        sa.Column("tax_code", sa.String(length=30), nullable=True),
        sa.Column("tax_amount", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("freight_amount", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("freight_tax_code", sa.String(length=30), nullable=True),
        sa.Column("freight_tax_amount", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("sale_status", sa.String(length=20), nullable=True),
        sa.Column("amount_paid", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("payment_method", sa.Unicode(length=100), nullable=True),
        sa.Column("category", sa.Unicode(length=100), nullable=True),
        sa.Column("location_id", sa.Unicode(length=100), nullable=True),
        sa.Column("card_id_snapshot", sa.String(length=100), nullable=True),
        sa.Column("is_cover_order", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["cover_order_document_id"],
            ["cover_order_documents.cover_order_document_id"],
            name=op.f("fk_cover_order_lines_cover_order_document_id_cover_order_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["items.item_id"],
            name=op.f("fk_cover_order_lines_item_id_items"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_import_row_id"],
            ["import_rows.import_row_id"],
            name=op.f("fk_cover_order_lines_source_import_row_id_import_rows"),
        ),
        sa.PrimaryKeyConstraint(
            "cover_order_line_id", name=op.f("pk_cover_order_lines")
        ),
        sa.UniqueConstraint(
            "cover_order_document_id", "line_sequence", name="cover_document_sequence"
        ),
    )
    op.create_index(
        "ix_cover_order_lines_item_date",
        "cover_order_lines",
        ["item_id", "transaction_date"],
        unique=False,
    )
    op.create_index(
        "ix_cover_order_lines_delivery",
        "cover_order_lines",
        ["delivery_status", "transaction_date"],
        unique=False,
    )

    op.create_table(
        "transaction_line_observations",
        sa.Column("transaction_line_observation_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("import_batch_id", sa.Uuid(), nullable=False),
        sa.Column("import_row_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "action IN ('created', 'updated', 'unchanged', 'reactivated')",
            name=op.f("ck_transaction_line_observations_action_valid"),
        ),
        sa.CheckConstraint(
            "entity_type IN ('sales_line', 'cover_order_line', 'purchase_line')",
            name=op.f("ck_transaction_line_observations_entity_type_valid"),
        ),
        sa.CheckConstraint(
            "source_type IN ('sales_transactions', 'cover_order_snapshot', 'purchase_transactions')",
            name=op.f("ck_transaction_line_observations_source_type_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["import_batch_id"],
            ["import_batches.import_batch_id"],
            name=op.f("fk_transaction_line_observations_import_batch_id_import_batches"),
        ),
        sa.ForeignKeyConstraint(
            ["import_row_id"],
            ["import_rows.import_row_id"],
            name=op.f("fk_transaction_line_observations_import_row_id_import_rows"),
        ),
        sa.PrimaryKeyConstraint(
            "transaction_line_observation_id",
            name=op.f("pk_transaction_line_observations"),
        ),
        sa.UniqueConstraint(
            "import_row_id", name="transaction_observation_import_row"
        ),
    )
    op.create_index(
        "ix_transaction_line_observations_entity",
        "transaction_line_observations",
        ["entity_type", "entity_id"],
        unique=False,
    )
    op.create_index(
        "ix_transaction_line_observations_batch",
        "transaction_line_observations",
        ["import_batch_id", "source_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_transaction_line_observations_batch",
        table_name="transaction_line_observations",
    )
    op.drop_index(
        "ix_transaction_line_observations_entity",
        table_name="transaction_line_observations",
    )
    op.drop_table("transaction_line_observations")
    op.drop_index("ix_cover_order_lines_delivery", table_name="cover_order_lines")
    op.drop_index("ix_cover_order_lines_item_date", table_name="cover_order_lines")
    op.drop_table("cover_order_lines")
    op.drop_index(
        "ix_cover_order_documents_customer", table_name="cover_order_documents"
    )
    op.drop_table("cover_order_documents")
    op.drop_index("ix_purchase_lines_delivery", table_name="purchase_lines")
    op.drop_index("ix_purchase_lines_document_active", table_name="purchase_lines")
    op.drop_index("ix_purchase_lines_item_date", table_name="purchase_lines")
    op.drop_table("purchase_lines")
    op.drop_index("ix_sales_lines_cover", table_name="sales_lines")
    op.drop_index("ix_sales_lines_document_active", table_name="sales_lines")
    op.drop_index("ix_sales_lines_item_date", table_name="sales_lines")
    op.drop_table("sales_lines")
    op.drop_index(
        "ux_cover_order_snapshots_one_current", table_name="cover_order_snapshots"
    )
    op.drop_index(
        "ix_cover_order_snapshots_current", table_name="cover_order_snapshots"
    )
    op.drop_table("cover_order_snapshots")
    op.drop_index("ix_purchase_documents_number", table_name="purchase_documents")
    op.drop_index(
        "ix_purchase_documents_supplier_date", table_name="purchase_documents"
    )
    op.drop_table("purchase_documents")
    op.drop_index("ix_sales_documents_invoice", table_name="sales_documents")
    op.drop_index("ix_sales_documents_customer_date", table_name="sales_documents")
    op.drop_table("sales_documents")
