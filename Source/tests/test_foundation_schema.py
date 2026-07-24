from __future__ import annotations

from sqlalchemy import create_engine, inspect

from windsor_widget.db.base import Base
from windsor_widget.db.models import (  # noqa: F401
    AppUser,
    AuditEvent,
    ImportBatch,
    ImportIssue,
    ImportRow,
    MatchCandidate,
)

EXPECTED_TABLES = {
    "app_users",
    "web_user_accounts",
    "audit_events",
    "import_batches",
    "import_issues",
    "import_rows",
    "match_candidates",
    "customer_accounts",
    "customer_groups",
    "customer_price_files",
    "items",
    "item_suppliers",
    "suppliers",
    "sales_documents",
    "sales_lines",
    "purchase_documents",
    "purchase_lines",
    "cover_order_snapshots",
    "cover_order_documents",
    "cover_order_lines",
    "transaction_line_observations",
    "inventory_snapshots",
    "inventory_snapshot_lines",
    "manufacture_orders",
    "manufacture_order_lines",
    "manufacture_line_allocations",
    "bring_in_requests",
    "supplier_order_templates",
}


def test_current_metadata_contains_expected_tables() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_current_schema_can_be_created_in_memory() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES


def test_match_candidates_require_explicit_resolution_fields() -> None:
    table = Base.metadata.tables["match_candidates"]

    assert table.c.decision.nullable is False
    assert table.c.candidate_entity_id.nullable is False
    assert table.c.decided_by_user_id.nullable is True
    assert table.c.decided_at.nullable is True


def test_import_issue_foreign_keys_have_one_sql_server_cascade_path() -> None:
    """A batch may delete issues directly, never through a second row cascade path."""

    table = Base.metadata.tables["import_issues"]
    foreign_keys = {foreign_key.parent.name: foreign_key for foreign_key in table.foreign_keys}

    assert foreign_keys["import_batch_id"].ondelete == "CASCADE"
    assert foreign_keys["import_row_id"].ondelete is None
