"""Immutable inventory snapshots and planning foundation.

Revision ID: 0005_inventory_snapshot
Revises: 0004_transaction_foundation
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_inventory_snapshot"
down_revision: str | None = "0004_transaction_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "inventory_snapshots",
        sa.Column("inventory_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("source_file_name", sa.Unicode(length=500), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("committed_at", sa.DateTime(), nullable=False),
        sa.Column("committed_by_user_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["committed_by_user_id"],
            ["app_users.user_id"],
            name=op.f("fk_inventory_snapshots_committed_by_user_id_app_users"),
        ),
        sa.PrimaryKeyConstraint(
            "inventory_snapshot_id", name=op.f("pk_inventory_snapshots")
        ),
        sa.UniqueConstraint(
            "source_sha256", name="inventory_snapshot_source_sha256"
        ),
    )
    op.create_index(
        "ix_inventory_snapshots_current",
        "inventory_snapshots",
        ["is_current", "captured_at"],
        unique=False,
    )
    op.create_index(
        "ux_inventory_snapshots_one_current",
        "inventory_snapshots",
        ["is_current"],
        unique=True,
        mssql_where=sa.text("[is_current] = 1"),
        sqlite_where=sa.text("is_current = 1"),
    )

    op.create_table(
        "inventory_snapshot_lines",
        sa.Column("inventory_snapshot_line_id", sa.Uuid(), nullable=False),
        sa.Column("inventory_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.Column("item_number_snapshot", sa.Unicode(length=100), nullable=False),
        sa.Column("item_name_snapshot", sa.Unicode(length=500), nullable=False),
        sa.Column("on_hand", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("committed", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("on_order", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("available", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["inventory_snapshot_id"],
            ["inventory_snapshots.inventory_snapshot_id"],
            name=op.f(
                "fk_inventory_snapshot_lines_inventory_snapshot_id_inventory_snapshots"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["items.item_id"],
            name=op.f("fk_inventory_snapshot_lines_item_id_items"),
        ),
        sa.PrimaryKeyConstraint(
            "inventory_snapshot_line_id",
            name=op.f("pk_inventory_snapshot_lines"),
        ),
        sa.UniqueConstraint(
            "inventory_snapshot_id", "item_id", name="inventory_snapshot_item"
        ),
        sa.UniqueConstraint(
            "inventory_snapshot_id",
            "source_row_number",
            name="inventory_snapshot_source_row",
        ),
    )
    op.create_index(
        "ix_inventory_snapshot_lines_item",
        "inventory_snapshot_lines",
        ["item_id", "inventory_snapshot_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_inventory_snapshot_lines_item", table_name="inventory_snapshot_lines"
    )
    op.drop_table("inventory_snapshot_lines")
    op.drop_index(
        "ux_inventory_snapshots_one_current", table_name="inventory_snapshots"
    )
    op.drop_index(
        "ix_inventory_snapshots_current", table_name="inventory_snapshots"
    )
    op.drop_table("inventory_snapshots")
