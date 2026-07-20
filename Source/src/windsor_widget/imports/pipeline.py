"""Manifest-driven, review-first orchestration for MYOB source exports.

Dry runs inspect every configured file without opening a database connection.
Committed runs add immutable import batches and raw rows to staging only.  They
never approve, match or promote source data into operational tables.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from windsor_widget.imports.contracts import SOURCE_CONTRACTS
from windsor_widget.imports.myob_text import inspect_myob_text, iter_myob_rows
from windsor_widget.imports.staging import DuplicateImportBatchError, stage_myob_file


class SourceManifestError(ValueError):
    """Raised when a source manifest is incomplete, ambiguous or unsafe to run."""


@dataclass(frozen=True, slots=True)
class SourceFileRequest:
    """One declared MYOB export and its optional reporting period."""

    source_type: str
    path: Path
    source_period_start: date | None = None
    source_period_end: date | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class SourceFileResult:
    """Outcome for one source file in a dry-run or staging run."""

    source_type: str
    source_path: str
    status: str
    file_sha256: str | None
    row_count: int | None
    parsed_row_count: int | None
    review_row_count: int | None
    issue_count: int | None
    import_batch_id: UUID | None = None
    existing_batch_id: UUID | None = None

    def to_mapping(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("import_batch_id", "existing_batch_id"):
            value = data[key]
            data[key] = str(value) if value is not None else None
        return data


@dataclass(frozen=True, slots=True)
class PipelineSummary:
    """Serializable summary of a complete manifest run."""

    mode: str
    started_at_utc: datetime
    completed_at_utc: datetime
    results: tuple[SourceFileResult, ...]

    @property
    def file_count(self) -> int:
        return len(self.results)

    @property
    def staged_count(self) -> int:
        if self.mode != "staging":
            return 0
        return sum(result.status in {"staged", "review_required"} for result in self.results)

    @property
    def ready_count(self) -> int:
        return sum(result.status == "ready" for result in self.results)

    @property
    def duplicate_count(self) -> int:
        return sum(result.status == "duplicate" for result in self.results)

    @property
    def review_file_count(self) -> int:
        return sum(result.status == "review_required" for result in self.results)

    @property
    def row_count(self) -> int:
        return sum(result.row_count or 0 for result in self.results)

    @property
    def issue_count(self) -> int:
        return sum(result.issue_count or 0 for result in self.results)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "started_at_utc": self.started_at_utc.isoformat(),
            "completed_at_utc": self.completed_at_utc.isoformat(),
            "totals": {
                "file_count": self.file_count,
                "ready_count": self.ready_count,
                "staged_count": self.staged_count,
                "duplicate_count": self.duplicate_count,
                "review_file_count": self.review_file_count,
                "row_count": self.row_count,
                "issue_count": self.issue_count,
            },
            "files": [result.to_mapping() for result in self.results],
        }


SessionFactory = Callable[[], Session]


def _optional_date(value: object, *, field_name: str, source_type: str) -> date | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise SourceManifestError(
            f"{source_type}.{field_name} must be an ISO date in YYYY-MM-DD format."
        )
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SourceManifestError(
            f"{source_type}.{field_name} must be an ISO date in YYYY-MM-DD format."
        ) from exc


def _request_from_mapping(
    entry: Mapping[str, object], *, manifest_parent: Path
) -> SourceFileRequest:
    source_type_value = entry.get("source_type")
    path_value = entry.get("path")
    if not isinstance(source_type_value, str) or not source_type_value.strip():
        raise SourceManifestError("Every source entry requires a non-empty source_type.")
    source_type = source_type_value.strip()
    if source_type not in SOURCE_CONTRACTS:
        valid = ", ".join(sorted(SOURCE_CONTRACTS))
        raise SourceManifestError(
            f"Unknown source_type {source_type!r}. Valid source types: {valid}."
        )
    if not isinstance(path_value, str) or not path_value.strip():
        raise SourceManifestError(f"{source_type}.path must be a non-empty file path.")

    source_path = Path(path_value).expanduser()
    if not source_path.is_absolute():
        source_path = manifest_parent / source_path
    source_path = source_path.resolve()
    if not source_path.is_file():
        raise SourceManifestError(f"Source file does not exist: {source_path}")

    period_start = _optional_date(
        entry.get("source_period_start"),
        field_name="source_period_start",
        source_type=source_type,
    )
    period_end = _optional_date(
        entry.get("source_period_end"),
        field_name="source_period_end",
        source_type=source_type,
    )
    if period_start and period_end and period_start > period_end:
        raise SourceManifestError(
            f"{source_type}.source_period_start cannot be after source_period_end."
        )

    notes_value = entry.get("notes")
    if notes_value is not None and not isinstance(notes_value, str):
        raise SourceManifestError(f"{source_type}.notes must be text when supplied.")

    return SourceFileRequest(
        source_type=source_type,
        path=source_path,
        source_period_start=period_start,
        source_period_end=period_end,
        notes=(
            notes_value.strip()
            if isinstance(notes_value, str) and notes_value.strip()
            else None
        ),
    )


def load_source_manifest(path: str | Path) -> tuple[SourceFileRequest, ...]:
    """Load and validate a JSON manifest, resolving relative paths beside it."""

    manifest_path = Path(path).expanduser().resolve()
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SourceManifestError(f"Source manifest does not exist: {manifest_path}") from exc
    except json.JSONDecodeError as exc:
        raise SourceManifestError(f"Source manifest is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise SourceManifestError("Source manifest root must be a JSON object.")
    source_entries = data.get("sources")
    if not isinstance(source_entries, list) or not source_entries:
        raise SourceManifestError("Source manifest must contain a non-empty sources list.")

    requests: list[SourceFileRequest] = []
    seen_source_types: set[str] = set()
    for position, entry in enumerate(source_entries, start=1):
        if not isinstance(entry, dict):
            raise SourceManifestError(f"Source entry {position} must be a JSON object.")
        request = _request_from_mapping(entry, manifest_parent=manifest_path.parent)
        if request.source_type in seen_source_types:
            raise SourceManifestError(
                f"Source type {request.source_type!r} appears more than once in the manifest."
            )
        seen_source_types.add(request.source_type)
        requests.append(request)
    return tuple(requests)


def inspect_source_files(requests: Iterable[SourceFileRequest]) -> tuple[SourceFileResult, ...]:
    """Fully count source rows and issues without connecting to SQL Server."""

    results: list[SourceFileResult] = []
    for request in requests:
        contract = SOURCE_CONTRACTS[request.source_type]
        inspection = inspect_myob_text(request.path, contract)
        row_count = 0
        review_row_count = 0
        issue_count = len(inspection.issues)
        for row in iter_myob_rows(request.path, contract, inspection=inspection):
            row_count += 1
            review_row_count += int(row.review_required)
            issue_count += len(row.issues)
        results.append(
            SourceFileResult(
                source_type=request.source_type,
                source_path=str(request.path),
                status="review_required" if issue_count else "ready",
                file_sha256=inspection.file_sha256,
                row_count=row_count,
                parsed_row_count=row_count - review_row_count,
                review_row_count=review_row_count,
                issue_count=issue_count,
            )
        )
    return tuple(results)


def stage_source_files(
    session_factory: SessionFactory,
    requests: Iterable[SourceFileRequest],
    *,
    chunk_size: int = 1_000,
) -> tuple[SourceFileResult, ...]:
    """Stage each file in its own transaction; repeated exact files are reported safely."""

    results: list[SourceFileResult] = []
    for request in requests:
        contract = SOURCE_CONTRACTS[request.source_type]
        with session_factory() as session:
            try:
                summary = stage_myob_file(
                    session,
                    request.path,
                    contract,
                    source_period_start=request.source_period_start,
                    source_period_end=request.source_period_end,
                    notes=request.notes,
                    chunk_size=chunk_size,
                )
                session.commit()
            except DuplicateImportBatchError as exc:
                session.rollback()
                inspection = inspect_myob_text(request.path, contract)
                results.append(
                    SourceFileResult(
                        source_type=request.source_type,
                        source_path=str(request.path),
                        status="duplicate",
                        file_sha256=inspection.file_sha256,
                        row_count=None,
                        parsed_row_count=None,
                        review_row_count=None,
                        issue_count=None,
                        existing_batch_id=exc.existing_batch_id,
                    )
                )
                continue

        results.append(
            SourceFileResult(
                source_type=summary.source_type,
                source_path=str(request.path),
                status=summary.status,
                file_sha256=summary.file_sha256,
                row_count=summary.row_count,
                parsed_row_count=summary.parsed_row_count,
                review_row_count=summary.review_row_count,
                issue_count=summary.issue_count,
                import_batch_id=summary.import_batch_id,
            )
        )
    return tuple(results)


def run_import_pipeline(
    requests: Iterable[SourceFileRequest],
    *,
    commit: bool,
    session_factory: SessionFactory | None = None,
    chunk_size: int = 1_000,
) -> PipelineSummary:
    """Run a dry inspection or a staging-only import for the supplied requests."""

    if chunk_size < 1:
        raise ValueError("chunk_size must be at least 1")

    request_tuple = tuple(requests)
    started_at = datetime.now(UTC)
    if commit:
        if session_factory is None:
            raise ValueError("session_factory is required when commit=True")
        results = stage_source_files(session_factory, request_tuple, chunk_size=chunk_size)
        mode = "staging"
    else:
        results = inspect_source_files(request_tuple)
        mode = "dry_run"
    return PipelineSummary(
        mode=mode,
        started_at_utc=started_at,
        completed_at_utc=datetime.now(UTC),
        results=results,
    )


def write_pipeline_report(summary: PipelineSummary, path: str | Path) -> Path:
    """Atomically write a JSON report containing counts and hashes, not raw source data."""

    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = report_path.with_suffix(f"{report_path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(summary.to_mapping(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(report_path)
    return report_path
