"""Add supplier order-form template settings.

Revision ID: 0008_supplier_order_templates
Revises: 0007_manufacture_orders
Create Date: 2026-07-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_supplier_order_templates"
down_revision: str | None = "0007_manufacture_orders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "supplier_order_templates",
        sa.Column("supplier_order_template_id", sa.Uuid(), nullable=False),
        sa.Column("supplier_id", sa.Uuid(), nullable=False),
        sa.Column("template_kind", sa.String(length=50), nullable=False),
        sa.Column("folder_path", sa.Unicode(length=1000), nullable=False),
        sa.Column("file_name", sa.Unicode(length=500), nullable=False),
        sa.Column("worksheet_name", sa.Unicode(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("verified_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "template_kind IN ('yuchang_compact_xlsx')",
            name=op.f(
                "ck_supplier_order_templates_supplier_order_template_kind_valid"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["supplier_id"],
            ["suppliers.supplier_id"],
            name=op.f("fk_supplier_order_templates_supplier_id_suppliers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["verified_by_user_id"],
            ["app_users.user_id"],
            name=op.f(
                "fk_supplier_order_templates_verified_by_user_id_app_users"
            ),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "supplier_order_template_id",
            name=op.f("pk_supplier_order_templates"),
        ),
        sa.UniqueConstraint(
            "supplier_id",
            "template_kind",
            name="supplier_template_kind",
        ),
    )
    op.create_index(
        "ix_supplier_order_templates_active",
        "supplier_order_templates",
        ["supplier_id", "is_active"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_supplier_order_templates_active",
        table_name="supplier_order_templates",
    )
    op.drop_table("supplier_order_templates")
