"""Add supplier-specific roll/spool packing fields.

Revision ID: 0009_yuchang_packing_fields
Revises: 0008_supplier_order_templates
Create Date: 2026-07-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_yuchang_packing_fields"
down_revision: str | None = "0008_supplier_order_templates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "item_suppliers",
        sa.Column("supplier_description_raw", sa.UnicodeText(), nullable=True),
    )
    op.add_column(
        "item_suppliers",
        sa.Column("supplier_size_raw", sa.Unicode(length=250), nullable=True),
    )
    op.add_column(
        "item_suppliers",
        sa.Column("supplier_colour_raw", sa.Unicode(length=250), nullable=True),
    )
    op.add_column(
        "item_suppliers",
        sa.Column("supplier_unit_type", sa.Unicode(length=100), nullable=True),
    )
    op.add_column(
        "item_suppliers",
        sa.Column(
            "packing_quantity_per_unit_raw",
            sa.Unicode(length=100),
            nullable=True,
        ),
    )
    op.add_column(
        "item_suppliers",
        sa.Column(
            "roll_spool_length_metres",
            sa.Numeric(precision=18, scale=6),
            nullable=True,
        ),
    )
    op.add_column(
        "item_suppliers",
        sa.Column(
            "packing_quantity_per_carton_raw",
            sa.Unicode(length=100),
            nullable=True,
        ),
    )
    op.add_column(
        "item_suppliers",
        sa.Column(
            "metres_per_carton",
            sa.Numeric(precision=18, scale=6),
            nullable=True,
        ),
    )
    op.add_column(
        "item_suppliers",
        sa.Column(
            "supplier_units_per_carton",
            sa.Numeric(precision=18, scale=6),
            nullable=True,
        ),
    )
    op.add_column(
        "item_suppliers",
        sa.Column(
            "supplier_label_description_raw",
            sa.Unicode(length=500),
            nullable=True,
        ),
    )
    op.add_column(
        "item_suppliers",
        sa.Column(
            "packing_source",
            sa.String(length=30),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.add_column(
        "item_suppliers",
        sa.Column(
            "packing_source_workbook",
            sa.Unicode(length=1000),
            nullable=True,
        ),
    )
    op.add_column(
        "item_suppliers",
        sa.Column(
            "packing_source_worksheet",
            sa.Unicode(length=100),
            nullable=True,
        ),
    )
    op.add_column(
        "item_suppliers",
        sa.Column("packing_source_row", sa.Integer(), nullable=True),
    )
    op.add_column(
        "item_suppliers",
        sa.Column("packing_verified_at", sa.DateTime(), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_item_suppliers_packing_source_valid"),
        "item_suppliers",
        "packing_source IN ('unknown', 'supplier_workbook', 'user')",
    )
    op.alter_column(
        "item_suppliers",
        "packing_source",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_item_suppliers_packing_source_valid"),
        "item_suppliers",
        type_="check",
    )
    for column_name in (
        "packing_verified_at",
        "packing_source_row",
        "packing_source_worksheet",
        "packing_source_workbook",
        "packing_source",
        "supplier_label_description_raw",
        "supplier_units_per_carton",
        "metres_per_carton",
        "packing_quantity_per_carton_raw",
        "roll_spool_length_metres",
        "packing_quantity_per_unit_raw",
        "supplier_unit_type",
        "supplier_colour_raw",
        "supplier_size_raw",
        "supplier_description_raw",
    ):
        op.drop_column("item_suppliers", column_name)
