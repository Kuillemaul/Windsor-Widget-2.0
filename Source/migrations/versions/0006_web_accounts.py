"""Add web user authentication accounts.

Revision ID: 0006_web_accounts
Revises: 0005_inventory_snapshot
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_web_accounts"
down_revision: str | None = "0005_inventory_snapshot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "web_user_accounts",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("password_hash", sa.Unicode(length=500), nullable=False),
        sa.Column("role", sa.String(length=30), nullable=False),
        sa.Column("must_change_password", sa.Boolean(), nullable=False),
        sa.Column("failed_login_count", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "role IN ('admin', 'procurement', 'read_only')",
            name="ck_web_user_accounts_web_user_role_valid",
        ),
        sa.CheckConstraint(
            "failed_login_count >= 0",
            name="ck_web_user_accounts_failed_login_count_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_users.user_id"],
            name="fk_web_user_accounts_user_id_app_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name="pk_web_user_accounts"),
    )
    op.create_index(
        "ix_web_user_accounts_role",
        "web_user_accounts",
        ["role"],
        unique=False,
    )
    op.create_index(
        "ix_web_user_accounts_locked_until",
        "web_user_accounts",
        ["locked_until"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_web_user_accounts_locked_until", table_name="web_user_accounts")
    op.drop_index("ix_web_user_accounts_role", table_name="web_user_accounts")
    op.drop_table("web_user_accounts")
