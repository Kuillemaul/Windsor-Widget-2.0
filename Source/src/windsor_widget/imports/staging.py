"""Persist parsed MYOB files into review-first import staging.

The functions in this module only add and flush SQLAlchemy objects.  They never
commit a transaction, approve rows, or promote data into operational tables.
Those decisions remain with the calling workflow and its reviewer.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from windsor_widget.db.models import ImportBatch, ImportIssue, ImportRow
from windsor_widget.imports.contracts import SourceContract
from windsor_widget.imports.myob_text import (
    MyobFileInspection,
    ParsedRow,
    ParseIssue,
    inspect_myob_text,
    iter_myob_rows,
)


class DuplicateImportBatchError(ValueError):
    """Raised when the same source type and exact file have already been staged."""

    def __init__(self, existing_batch_id: uuid.UUID) -> None:
        self.existing_batch_id = existing_batch_id
        super().__init__(
            f"This exact source file is already staged as import batch {existing_batch_id}."
        )


@dataclass(frozen=True, slots=True)
class StagingSummary:
    """Counts returned after a file has been flushed to staging."""

    import_batch_id: uuid.UUID
    source_type: str
    file_sha256: str
    row_count: int
    parsed_row_count: int
    review_row_count: int
    issue_count: int
    status: str


def _issue_model(
    issue: ParseIssue,
    *,
    import_batch_id: uuid.UUID,
    row: ImportRow | None = None,
) -> ImportIssue:
    return ImportIssue(
        import_batch_id=import_batch_id,
        row=row,
        severity=issue.severity,
        issue_code=issue.issue_code,
        field_name=issue.field_name,
        supplied_value=issue.supplied_value,
        message=issue.message,
        resolution_status="open",
    )


def _raw_json(row: ParsedRow) -> str:
    """Keep both positional and named values so empty/extra fields remain inspectable."""

    return json.dumps(
        {
            "raw_values": row.raw_values,
            "values": row.values,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _row_model(row: ParsedRow, *, import_batch_id: uuid.UUID) -> ImportRow:
    staged_row = ImportRow(
        import_batch_id=import_batch_id,
        row_number=row.row_number,
        # Unit Separator is also used by the parser's stable semantic row hash.
        # raw_json retains the unambiguous positional and named source values.
        raw_text="\x1f".join(row.raw_values),
        raw_json=_raw_json(row),
        natural_key=row.natural_key,
        row_sha256=row.row_sha256,
        status="review_required" if row.review_required else "parsed",
        issue_count=len(row.issues),
    )
    staged_row.issues.extend(
        _issue_model(issue, import_batch_id=import_batch_id, row=staged_row)
        for issue in row.issues
    )
    return staged_row


def _find_duplicate(
    session: Session, inspection: MyobFileInspection
) -> uuid.UUID | None:
    statement = (
        select(ImportBatch.import_batch_id)
        .where(
            ImportBatch.source_type == inspection.source_type,
            ImportBatch.file_sha256 == inspection.file_sha256,
        )
        .limit(1)
    )
    return session.execute(statement).scalar_one_or_none()


def stage_myob_file(
    session: Session,
    path: str | Path,
    contract: SourceContract,
    *,
    received_by_user_id: uuid.UUID | None = None,
    source_period_start: date | None = None,
    source_period_end: date | None = None,
    notes: str | None = None,
    chunk_size: int = 1_000,
) -> StagingSummary:
    """Stream a source file into import staging without committing or approving it.

    The caller owns the surrounding transaction.  An identical file/source pair
    is rejected explicitly instead of being silently staged twice.
    """

    if chunk_size < 1:
        raise ValueError("chunk_size must be at least 1")

    inspection = inspect_myob_text(path, contract)
    existing_batch_id = _find_duplicate(session, inspection)
    if existing_batch_id is not None:
        raise DuplicateImportBatchError(existing_batch_id)

    batch = ImportBatch(
        source_type=inspection.source_type,
        source_file_name=inspection.source_path.name,
        file_sha256=inspection.file_sha256,
        status="review_required" if inspection.review_required else "staged",
        received_by_user_id=received_by_user_id,
        source_period_start=source_period_start,
        source_period_end=source_period_end,
        notes=notes,
        row_count=0,
        accepted_row_count=0,
        rejected_row_count=0,
    )
    session.add(batch)
    session.flush()

    batch_id = batch.import_batch_id
    batch_issue_count = len(inspection.issues)
    session.add_all(
        _issue_model(issue, import_batch_id=batch_id) for issue in inspection.issues
    )

    row_count = 0
    review_row_count = 0
    row_issue_count = 0
    buffer: list[ImportRow] = []

    for parsed_row in iter_myob_rows(path, contract, inspection=inspection):
        row_count += 1
        review_row_count += int(parsed_row.review_required)
        row_issue_count += len(parsed_row.issues)
        buffer.append(_row_model(parsed_row, import_batch_id=batch_id))

        if len(buffer) >= chunk_size:
            session.add_all(buffer)
            session.flush()
            buffer.clear()

    if buffer:
        session.add_all(buffer)
        session.flush()

    issue_count = batch_issue_count + row_issue_count
    batch.row_count = row_count
    if issue_count:
        batch.status = "review_required"
    session.flush()

    return StagingSummary(
        import_batch_id=batch_id,
        source_type=inspection.source_type,
        file_sha256=inspection.file_sha256,
        row_count=row_count,
        parsed_row_count=row_count - review_row_count,
        review_row_count=review_row_count,
        issue_count=issue_count,
        status=batch.status,
    )
