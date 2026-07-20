from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from windsor_widget.db.base import Base
from windsor_widget.db.models import ImportBatch, ImportIssue, ImportRow
from windsor_widget.imports import (
    SOURCE_CONTRACTS,
    DuplicateImportBatchError,
    stage_myob_file,
)


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def test_staging_persists_clean_and_review_rows_without_committing(tmp_path: Path) -> None:
    source = tmp_path / "sales.txt"
    source.write_text(
        "Co./Last Name,Invoice No.,Date,Item Number,Quantity,Record ID\n"
        "First,INV1,20/07/2026,ABC,12,55\n"
        "Second,INV2,21/07/2026,XYZ,4,\n",
        encoding="utf-8",
    )

    with Session(_engine()) as session:
        summary = stage_myob_file(
            session,
            source,
            SOURCE_CONTRACTS["sales_transactions"],
            chunk_size=1,
        )

        assert summary.row_count == 2
        assert summary.parsed_row_count == 1
        assert summary.review_row_count == 1
        assert summary.issue_count == 1
        assert summary.status == "review_required"

        batch = session.get(ImportBatch, summary.import_batch_id)
        rows = list(
            session.scalars(
                select(ImportRow)
                .where(ImportRow.import_batch_id == summary.import_batch_id)
                .order_by(ImportRow.row_number)
            )
        )
        issues = list(
            session.scalars(
                select(ImportIssue).where(
                    ImportIssue.import_batch_id == summary.import_batch_id
                )
            )
        )

        assert batch is not None
        assert batch.status == "review_required"
        assert [row.status for row in rows] == ["parsed", "review_required"]
        assert rows[0].raw_json is not None and '"Item Number":"ABC"' in rows[0].raw_json
        assert issues[0].issue_code == "natural_key_incomplete"
        assert issues[0].import_row_id == rows[1].import_row_id


def test_staging_rejects_an_identical_file_source_pair(tmp_path: Path) -> None:
    source = tmp_path / "items.txt"
    source.write_text(
        "Item Number,Item Name,Buy,Sell,Inventory\nABC,Example,Yes,Yes,Yes\n",
        encoding="utf-8",
    )

    with Session(_engine()) as session:
        first = stage_myob_file(session, source, SOURCE_CONTRACTS["item_master"])

        with pytest.raises(DuplicateImportBatchError) as raised:
            stage_myob_file(session, source, SOURCE_CONTRACTS["item_master"])

        assert raised.value.existing_batch_id == first.import_batch_id


def test_header_failure_is_a_batch_level_review_issue(tmp_path: Path) -> None:
    source = tmp_path / "unknown.txt"
    source.write_text("Not,A,MYOB,Header\n1,2,3,4\n", encoding="utf-8")

    with Session(_engine()) as session:
        summary = stage_myob_file(session, source, SOURCE_CONTRACTS["item_master"])
        issue = session.scalar(
            select(ImportIssue).where(
                ImportIssue.import_batch_id == summary.import_batch_id
            )
        )

        assert summary.row_count == 0
        assert summary.review_row_count == 0
        assert summary.issue_count == 1
        assert summary.status == "review_required"
        assert issue is not None
        assert issue.issue_code == "header_not_found"
        assert issue.import_row_id is None


def test_staging_does_not_accept_invalid_chunk_size(tmp_path: Path) -> None:
    source = tmp_path / "unused.txt"

    with Session(_engine()) as session, pytest.raises(ValueError, match="chunk_size"):
        stage_myob_file(
            session,
            source,
            SOURCE_CONTRACTS["item_master"],
            chunk_size=0,
        )
