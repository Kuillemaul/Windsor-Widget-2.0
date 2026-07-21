"""Allow multiple missing MYOB external IDs while preserving uniqueness.

Revision ID: 0003_nullable_external_ids
Revises: 0002_master_data
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_nullable_external_ids"
down_revision: str | None = "0002_master_data"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_EXTERNAL_ID_INDEXES = (
    (
        "customer_accounts",
        "myob_record_id",
        "uq_customer_accounts_myob_record_id",
        "ux_customer_accounts_myob_record_id_not_null",
    ),
    (
        "customer_accounts",
        "myob_card_id",
        "uq_customer_accounts_myob_card_id",
        "ux_customer_accounts_myob_card_id_not_null",
    ),
    (
        "suppliers",
        "myob_record_id",
        "uq_suppliers_myob_record_id",
        "ux_suppliers_myob_record_id_not_null",
    ),
    (
        "suppliers",
        "myob_card_id",
        "uq_suppliers_myob_card_id",
        "ux_suppliers_myob_card_id_not_null",
    ),
)


def upgrade() -> None:
    for table_name, column_name, constraint_name, index_name in _EXTERNAL_ID_INDEXES:
        op.drop_constraint(constraint_name, table_name, type_="unique")
        predicate = sa.text(f"[{column_name}] IS NOT NULL")
        op.create_index(
            index_name,
            table_name,
            [column_name],
            unique=True,
            mssql_where=predicate,
            postgresql_where=sa.text(f'"{column_name}" IS NOT NULL'),
            sqlite_where=sa.text(f'"{column_name}" IS NOT NULL'),
        )


def downgrade() -> None:
    for table_name, column_name, constraint_name, index_name in reversed(
        _EXTERNAL_ID_INDEXES
    ):
        op.drop_index(index_name, table_name=table_name)
        op.create_unique_constraint(constraint_name, table_name, [column_name])
