"""Stage 1 identity, audit and review-first import foundation.

Revision ID: 0001_stage1_foundation
Revises:
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_stage1_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_users",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.Unicode(length=200), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_app_users")),
        sa.UniqueConstraint("username", name=op.f("uq_app_users_username")),
    )

    op.create_table(
        "audit_events",
        sa.Column("audit_event_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("entity_type", sa.String(length=100), nullable=False),
        sa.Column("entity_id", sa.String(length=100), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("summary", sa.Unicode(length=500), nullable=True),
        sa.Column("before_json", sa.UnicodeText(), nullable=True),
        sa.Column("after_json", sa.UnicodeText(), nullable=True),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["app_users.user_id"],
            name=op.f("fk_audit_events_actor_user_id_app_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("audit_event_id", name=op.f("pk_audit_events")),
    )
    op.create_index(
        "ix_audit_events_correlation_id", "audit_events", ["correlation_id"], unique=False
    )
    op.create_index(
        "ix_audit_events_entity",
        "audit_events",
        ["entity_type", "entity_id", "occurred_at"],
        unique=False,
    )

    op.create_table(
        "import_batches",
        sa.Column("import_batch_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(length=100), nullable=False),
        sa.Column("source_file_name", sa.Unicode(length=500), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("received_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("source_period_start", sa.Date(), nullable=True),
        sa.Column("source_period_end", sa.Date(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("accepted_row_count", sa.Integer(), nullable=False),
        sa.Column("rejected_row_count", sa.Integer(), nullable=False),
        sa.Column("committed_at", sa.DateTime(), nullable=True),
        sa.Column("notes", sa.UnicodeText(), nullable=True),
        sa.CheckConstraint(
            "status IN ('staged', 'review_required', 'approved', 'committed', 'rejected')",
            name=op.f("ck_import_batches_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["received_by_user_id"],
            ["app_users.user_id"],
            name=op.f("fk_import_batches_received_by_user_id_app_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("import_batch_id", name=op.f("pk_import_batches")),
    )
    op.create_index(
        "ix_import_batches_source_hash",
        "import_batches",
        ["source_type", "file_sha256"],
        unique=False,
    )
    op.create_index(
        "ix_import_batches_status_received",
        "import_batches",
        ["status", "received_at"],
        unique=False,
    )

    op.create_table(
        "import_rows",
        sa.Column("import_row_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("import_batch_id", sa.Uuid(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("raw_text", sa.UnicodeText(), nullable=True),
        sa.Column("raw_json", sa.UnicodeText(), nullable=True),
        sa.Column("natural_key", sa.Unicode(length=500), nullable=True),
        sa.Column("row_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("issue_count", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "status IN ('raw', 'parsed', 'review_required', 'accepted', 'rejected', 'committed')",
            name=op.f("ck_import_rows_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["import_batch_id"],
            ["import_batches.import_batch_id"],
            name=op.f("fk_import_rows_import_batch_id_import_batches"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("import_row_id", name=op.f("pk_import_rows")),
    )
    op.create_index(
        "ix_import_rows_batch_status",
        "import_rows",
        ["import_batch_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_import_rows_natural_key", "import_rows", ["natural_key"], unique=False
    )

    op.create_table(
        "import_issues",
        sa.Column("import_issue_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("import_batch_id", sa.Uuid(), nullable=False),
        sa.Column("import_row_id", sa.Integer(), nullable=True),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("issue_code", sa.String(length=100), nullable=False),
        sa.Column("field_name", sa.String(length=100), nullable=True),
        sa.Column("supplied_value", sa.Unicode(length=1000), nullable=True),
        sa.Column("message", sa.Unicode(length=1000), nullable=False),
        sa.Column("resolution_status", sa.String(length=30), nullable=False),
        sa.Column("resolved_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("resolution_notes", sa.UnicodeText(), nullable=True),
        sa.CheckConstraint(
            "resolution_status IN ('open', 'resolved', 'accepted_risk')",
            name=op.f("ck_import_issues_resolution_status_valid"),
        ),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'error')",
            name=op.f("ck_import_issues_severity_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["import_batch_id"],
            ["import_batches.import_batch_id"],
            name=op.f("fk_import_issues_import_batch_id_import_batches"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["import_row_id"],
            ["import_rows.import_row_id"],
            name=op.f("fk_import_issues_import_row_id_import_rows"),
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_user_id"],
            ["app_users.user_id"],
            name=op.f("fk_import_issues_resolved_by_user_id_app_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("import_issue_id", name=op.f("pk_import_issues")),
    )
    op.create_index(
        "ix_import_issues_batch_resolution",
        "import_issues",
        ["import_batch_id", "resolution_status"],
        unique=False,
    )

    op.create_table(
        "match_candidates",
        sa.Column("match_candidate_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("import_row_id", sa.Integer(), nullable=False),
        sa.Column("match_type", sa.String(length=100), nullable=False),
        sa.Column("source_value", sa.Unicode(length=1000), nullable=False),
        sa.Column("candidate_entity_type", sa.String(length=100), nullable=False),
        sa.Column("candidate_entity_id", sa.String(length=100), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("proposed_by", sa.String(length=30), nullable=False),
        sa.Column("evidence_json", sa.UnicodeText(), nullable=True),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("decision_notes", sa.UnicodeText(), nullable=True),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 100",
            name=op.f("ck_match_candidates_confidence_range"),
        ),
        sa.CheckConstraint(
            "decision IN ('pending', 'approved', 'rejected')",
            name=op.f("ck_match_candidates_decision_valid"),
        ),
        sa.CheckConstraint(
            "proposed_by IN ('exact_rule', 'heuristic', 'user')",
            name=op.f("ck_match_candidates_proposed_by_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"],
            ["app_users.user_id"],
            name=op.f("fk_match_candidates_decided_by_user_id_app_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["import_row_id"],
            ["import_rows.import_row_id"],
            name=op.f("fk_match_candidates_import_row_id_import_rows"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("match_candidate_id", name=op.f("pk_match_candidates")),
    )
    op.create_index(
        "ix_match_candidates_pending",
        "match_candidates",
        ["decision", "match_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_match_candidates_pending", table_name="match_candidates")
    op.drop_table("match_candidates")
    op.drop_index("ix_import_issues_batch_resolution", table_name="import_issues")
    op.drop_table("import_issues")
    op.drop_index("ix_import_rows_natural_key", table_name="import_rows")
    op.drop_index("ix_import_rows_batch_status", table_name="import_rows")
    op.drop_table("import_rows")
    op.drop_index("ix_import_batches_status_received", table_name="import_batches")
    op.drop_index("ix_import_batches_source_hash", table_name="import_batches")
    op.drop_table("import_batches")
    op.drop_index("ix_audit_events_entity", table_name="audit_events")
    op.drop_index("ix_audit_events_correlation_id", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("app_users")
