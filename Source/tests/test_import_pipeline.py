from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from windsor_widget.db.base import Base
from windsor_widget.db.models import CustomerAccount, ImportBatch, Item, Supplier
from windsor_widget.imports import (
    SourceFileRequest,
    SourceManifestError,
    load_source_manifest,
    run_import_pipeline,
    write_pipeline_report,
)


def _session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _write_item_export(path: Path) -> None:
    path.write_text(
        "Item Number,Item Name,Buy,Sell,Inventory\n"
        "ABC,Example item,Yes,Yes,Yes\n",
        encoding="utf-8",
    )


def test_manifest_resolves_relative_paths_and_periods(tmp_path: Path) -> None:
    source = tmp_path / "items.txt"
    _write_item_export(source)
    manifest = tmp_path / "sources.json"
    manifest.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "source_type": "item_master",
                        "path": "items.txt",
                        "source_period_start": "2026-07-01",
                        "source_period_end": "2026-07-31",
                        "notes": "  July snapshot  ",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    requests = load_source_manifest(manifest)

    assert requests[0].path == source.resolve()
    assert requests[0].source_period_start is not None
    assert requests[0].source_period_start.isoformat() == "2026-07-01"
    assert requests[0].notes == "July snapshot"


@pytest.mark.parametrize(
    ("sources", "message"),
    [
        (
            [
                {"source_type": "item_master", "path": "items.txt"},
                {"source_type": "item_master", "path": "items.txt"},
            ],
            "appears more than once",
        ),
        ([{"source_type": "unknown", "path": "items.txt"}], "Unknown source_type"),
        ([{"source_type": "item_master", "path": "missing.txt"}], "does not exist"),
    ],
)
def test_manifest_rejects_ambiguous_or_missing_sources(
    tmp_path: Path, sources: list[dict[str, str]], message: str
) -> None:
    _write_item_export(tmp_path / "items.txt")
    manifest = tmp_path / "sources.json"
    manifest.write_text(json.dumps({"sources": sources}), encoding="utf-8")

    with pytest.raises(SourceManifestError, match=message):
        load_source_manifest(manifest)


def test_dry_run_counts_review_rows_and_writes_a_sanitised_report(
    tmp_path: Path,
) -> None:
    source = tmp_path / "cover_orders.txt"
    source.write_text(
        "Co./Last Name,Invoice No.,Date,Item Number,Quantity,Record ID,Journal Memo\n"
        "Comfort Sleep,BO1,20/07/2026,ABC,600,55,"
        "Sale; Comfort Sleep - COVER ORDER\n"
        "Needs Review,BO2,20/07/2026,XYZ,40,,Sale; Needs Review\n",
        encoding="utf-8",
    )

    summary = run_import_pipeline(
        (SourceFileRequest("cover_order_snapshot", source),),
        commit=False,
    )
    report_path = write_pipeline_report(summary, tmp_path / "reports" / "result.json")
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert summary.mode == "dry_run"
    assert summary.file_count == 1
    assert summary.row_count == 2
    assert summary.issue_count == 1
    assert summary.review_file_count == 1
    assert summary.results[0].review_row_count == 1
    assert report["files"][0]["source_type"] == "cover_order_snapshot"
    assert "Comfort Sleep" not in report_path.read_text(encoding="utf-8")


def test_commit_stages_only_and_duplicate_rerun_is_safe(tmp_path: Path) -> None:
    source = tmp_path / "items.txt"
    _write_item_export(source)
    factory = _session_factory()
    request = SourceFileRequest("item_master", source)

    first = run_import_pipeline(
        (request,), commit=True, session_factory=factory, chunk_size=1
    )
    second = run_import_pipeline(
        (request,), commit=True, session_factory=factory, chunk_size=1
    )

    assert first.mode == "staging"
    assert first.staged_count == 1
    assert first.results[0].status == "staged"
    assert second.duplicate_count == 1
    assert second.results[0].existing_batch_id == first.results[0].import_batch_id

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ImportBatch)) == 1
        assert session.scalar(select(func.count()).select_from(CustomerAccount)) == 0
        assert session.scalar(select(func.count()).select_from(Item)) == 0
        assert session.scalar(select(func.count()).select_from(Supplier)) == 0


def test_pipeline_rejects_invalid_chunk_size_even_for_a_dry_run(tmp_path: Path) -> None:
    source = tmp_path / "items.txt"
    _write_item_export(source)

    with pytest.raises(ValueError, match="chunk_size"):
        run_import_pipeline(
            (SourceFileRequest("item_master", source),),
            commit=False,
            chunk_size=0,
        )
