"""Review-first import staging, issue and match-decision models."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Unicode,
    UnicodeText,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from windsor_widget.db.base import Base
from windsor_widget.db.models.audit import AppUser, new_uuid, utc_now


class ImportBatch(Base):
    __tablename__ = "import_batches"
    __table_args__ = (
        CheckConstraint(
            "status IN ('staged', 'review_required', 'approved', 'committed', 'rejected')",
            name="status_valid",
        ),
        Index("ix_import_batches_source_hash", "source_type", "file_sha256"),
        Index("ix_import_batches_status_received", "status", "received_at"),
    )

    import_batch_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    source_type: Mapped[str] = mapped_column(String(100), nullable=False)
    source_file_name: Mapped[str] = mapped_column(Unicode(500), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="staged")
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, default=utc_now
    )
    received_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_users.user_id", ondelete="SET NULL")
    )
    source_period_start: Mapped[date | None] = mapped_column(Date)
    source_period_end: Mapped[date | None] = mapped_column(Date)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accepted_row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected_row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    notes: Mapped[str | None] = mapped_column(UnicodeText)

    received_by: Mapped[AppUser | None] = relationship(lazy="joined")
    rows: Mapped[list[ImportRow]] = relationship(
        back_populates="batch", cascade="all, delete-orphan", passive_deletes=True
    )
    issues: Mapped[list[ImportIssue]] = relationship(
        back_populates="batch", cascade="all, delete-orphan", passive_deletes=True
    )


class ImportRow(Base):
    __tablename__ = "import_rows"
    __table_args__ = (
        CheckConstraint(
            "status IN ('raw', 'parsed', 'review_required', 'accepted', 'rejected', 'committed')",
            name="status_valid",
        ),
        Index("ix_import_rows_batch_status", "import_batch_id", "status"),
        Index("ix_import_rows_natural_key", "natural_key"),
    )

    import_row_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    import_batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("import_batches.import_batch_id", ondelete="CASCADE"), nullable=False
    )
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_text: Mapped[str | None] = mapped_column(UnicodeText)
    raw_json: Mapped[str | None] = mapped_column(UnicodeText)
    natural_key: Mapped[str | None] = mapped_column(Unicode(500))
    row_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="raw")
    issue_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    batch: Mapped[ImportBatch] = relationship(back_populates="rows")
    issues: Mapped[list[ImportIssue]] = relationship(
        back_populates="row", cascade="all, delete-orphan", passive_deletes=True
    )
    match_candidates: Mapped[list[MatchCandidate]] = relationship(
        back_populates="row", cascade="all, delete-orphan", passive_deletes=True
    )


class ImportIssue(Base):
    __tablename__ = "import_issues"
    __table_args__ = (
        CheckConstraint("severity IN ('info', 'warning', 'error')", name="severity_valid"),
        CheckConstraint(
            "resolution_status IN ('open', 'resolved', 'accepted_risk')",
            name="resolution_status_valid",
        ),
        Index("ix_import_issues_batch_resolution", "import_batch_id", "resolution_status"),
    )

    import_issue_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    import_batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("import_batches.import_batch_id", ondelete="CASCADE"), nullable=False
    )
    import_row_id: Mapped[int | None] = mapped_column(
        ForeignKey("import_rows.import_row_id", ondelete="CASCADE")
    )
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    issue_code: Mapped[str] = mapped_column(String(100), nullable=False)
    field_name: Mapped[str | None] = mapped_column(String(100))
    supplied_value: Mapped[str | None] = mapped_column(Unicode(1000))
    message: Mapped[str] = mapped_column(Unicode(1000), nullable=False)
    resolution_status: Mapped[str] = mapped_column(String(30), nullable=False, default="open")
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_users.user_id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    resolution_notes: Mapped[str | None] = mapped_column(UnicodeText)

    batch: Mapped[ImportBatch] = relationship(back_populates="issues")
    row: Mapped[ImportRow | None] = relationship(back_populates="issues")
    resolved_by: Mapped[AppUser | None] = relationship(lazy="joined")


class MatchCandidate(Base):
    __tablename__ = "match_candidates"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('pending', 'approved', 'rejected')", name="decision_valid"
        ),
        CheckConstraint(
            "proposed_by IN ('exact_rule', 'heuristic', 'user')", name="proposed_by_valid"
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 100", name="confidence_range"),
        Index("ix_match_candidates_pending", "decision", "match_type"),
    )

    match_candidate_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    import_row_id: Mapped[int] = mapped_column(
        ForeignKey("import_rows.import_row_id", ondelete="CASCADE"), nullable=False
    )
    match_type: Mapped[str] = mapped_column(String(100), nullable=False)
    source_value: Mapped[str] = mapped_column(Unicode(1000), nullable=False)
    candidate_entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    candidate_entity_id: Mapped[str] = mapped_column(String(100), nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    proposed_by: Mapped[str] = mapped_column(String(30), nullable=False)
    evidence_json: Mapped[str | None] = mapped_column(UnicodeText)
    decision: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_users.user_id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    decision_notes: Mapped[str | None] = mapped_column(UnicodeText)

    row: Mapped[ImportRow] = relationship(back_populates="match_candidates")
    decided_by: Mapped[AppUser | None] = relationship(lazy="joined")
