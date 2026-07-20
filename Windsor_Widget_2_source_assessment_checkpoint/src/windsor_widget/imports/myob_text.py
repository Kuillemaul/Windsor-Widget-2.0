"""Loss-preserving, streaming parser for MYOB comma-delimited text exports."""

from __future__ import annotations

import codecs
import csv
import hashlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from windsor_widget.imports.contracts import SourceContract
from windsor_widget.imports.normalization import clean_text


@dataclass(frozen=True, slots=True)
class ParseIssue:
    severity: str
    issue_code: str
    message: str
    row_number: int | None = None
    field_name: str | None = None
    supplied_value: str | None = None


@dataclass(frozen=True, slots=True)
class ParsedRow:
    row_number: int
    values: dict[str, str | None]
    raw_values: tuple[str, ...]
    natural_key: str | None
    row_sha256: str
    review_required: bool
    issues: tuple[ParseIssue, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class MyobFileInspection:
    """Metadata found without materialising the transaction rows."""

    source_type: str
    source_path: Path
    encoding: str
    file_sha256: str
    headers: tuple[str, ...]
    header_row_number: int
    issues: tuple[ParseIssue, ...]

    @property
    def review_required(self) -> bool:
        return bool(self.issues)


@dataclass(frozen=True, slots=True)
class ParsedFile:
    source_type: str
    source_path: Path
    encoding: str
    file_sha256: str
    headers: tuple[str, ...]
    header_row_number: int
    rows: tuple[ParsedRow, ...]
    issues: tuple[ParseIssue, ...]

    @property
    def review_required(self) -> bool:
        return bool(self.issues or any(row.review_required for row in self.rows))


def _file_identity(path: Path) -> tuple[str, str]:
    """Hash the file and choose a lossless encoding using bounded memory."""

    digest = hashlib.sha256()
    decoders = {
        "utf-8-sig": codecs.getincrementaldecoder("utf-8-sig")(),
        "cp1252": codecs.getincrementaldecoder("cp1252")(),
    }
    valid = {encoding: True for encoding in decoders}

    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            for encoding, decoder in decoders.items():
                if not valid[encoding]:
                    continue
                try:
                    decoder.decode(chunk, final=False)
                except UnicodeDecodeError:
                    valid[encoding] = False

    for encoding, decoder in decoders.items():
        if not valid[encoding]:
            continue
        try:
            decoder.decode(b"", final=True)
        except UnicodeDecodeError:
            valid[encoding] = False

    if valid["utf-8-sig"]:
        encoding = "utf-8-sig"
    elif valid["cp1252"]:
        encoding = "cp1252"
    else:
        # Latin-1 maps every byte exactly, so an unusual legacy file is not corrupted.
        encoding = "latin-1"
    return encoding, digest.hexdigest()


def _open_csv(path: Path, encoding: str) -> tuple[TextIO, Iterator[list[str]]]:
    stream = path.open("r", encoding=encoding, newline="")
    return stream, iter(csv.reader(stream))


def _find_header(
    rows: Iterator[list[str]], contract: SourceContract, scan_limit: int = 50
) -> tuple[int, list[str]] | None:
    for row_number, row in enumerate(rows, start=1):
        if row_number > scan_limit:
            break
        header_set = {value.strip() for value in row}
        if contract.required_headers.issubset(header_set):
            return row_number, [value.strip() for value in row]
    return None


def _natural_key(values: dict[str, str | None], fields: tuple[str, ...]) -> str | None:
    parts = [clean_text(values.get(field)) for field in fields]
    if not parts or all(part is None for part in parts):
        return None
    return "|".join(part or "" for part in parts)


def inspect_myob_text(path: str | Path, contract: SourceContract) -> MyobFileInspection:
    """Inspect encoding, hash and header without loading transaction rows into memory."""

    source_path = Path(path)
    encoding, file_hash = _file_identity(source_path)
    stream, csv_rows = _open_csv(source_path, encoding)
    try:
        found = _find_header(csv_rows, contract)
    finally:
        stream.close()

    if found is None:
        issue = ParseIssue(
            severity="error",
            issue_code="header_not_found",
            message=(
                f"Could not find the {contract.source_type} header within the first 50 rows."
            ),
        )
        return MyobFileInspection(
            source_type=contract.source_type,
            source_path=source_path,
            encoding=encoding,
            file_sha256=file_hash,
            headers=(),
            header_row_number=0,
            issues=(issue,),
        )

    header_row_number, headers = found
    issues: list[ParseIssue] = []
    for field_name in sorted(contract.required_headers.difference(headers)):
        issues.append(
            ParseIssue(
                severity="error",
                issue_code="required_header_missing",
                field_name=field_name,
                message=f"Required header {field_name!r} is missing.",
            )
        )

    return MyobFileInspection(
        source_type=contract.source_type,
        source_path=source_path,
        encoding=encoding,
        file_sha256=file_hash,
        headers=tuple(headers),
        header_row_number=header_row_number,
        issues=tuple(issues),
    )


def _row_from_values(
    raw_values: list[str],
    *,
    row_number: int,
    headers: tuple[str, ...],
    contract: SourceContract,
) -> ParsedRow:
    row_issues: list[ParseIssue] = []
    if len(raw_values) != len(headers):
        row_issues.append(
            ParseIssue(
                severity="error",
                issue_code="column_count_mismatch",
                row_number=row_number,
                message=(
                    f"Row has {len(raw_values)} columns but the header has {len(headers)}; "
                    "the row requires review before commit."
                ),
            )
        )

    padded = raw_values[: len(headers)] + [""] * max(0, len(headers) - len(raw_values))
    values = {
        header: clean_text(value) for header, value in zip(headers, padded, strict=True)
    }
    natural_key = _natural_key(values, contract.natural_key_fields)
    if natural_key is None:
        row_issues.append(
            ParseIssue(
                severity="error",
                issue_code="natural_key_missing",
                row_number=row_number,
                message="No usable natural key could be built for this row.",
            )
        )
    else:
        missing_key_fields = tuple(
            field_name
            for field_name in contract.natural_key_fields
            if field_name not in contract.optional_natural_key_fields
            if clean_text(values.get(field_name)) is None
        )
        if missing_key_fields:
            row_issues.append(
                ParseIssue(
                    severity="error",
                    issue_code="natural_key_incomplete",
                    row_number=row_number,
                    field_name=", ".join(missing_key_fields),
                    message=(
                        "The natural key is incomplete; missing required key fields: "
                        f"{', '.join(missing_key_fields)}."
                    ),
                )
            )

    row_text = "\x1f".join(raw_values)
    return ParsedRow(
        row_number=row_number,
        values=values,
        raw_values=tuple(raw_values),
        natural_key=natural_key,
        row_sha256=hashlib.sha256(row_text.encode("utf-8")).hexdigest(),
        review_required=bool(row_issues),
        issues=tuple(row_issues),
    )


def iter_myob_rows(
    path: str | Path,
    contract: SourceContract,
    *,
    inspection: MyobFileInspection | None = None,
) -> Iterator[ParsedRow]:
    """Yield parsed rows incrementally; suitable for multi-hundred-thousand-row exports."""

    inspected = inspection or inspect_myob_text(path, contract)
    if inspected.header_row_number == 0 or inspected.issues:
        return

    source_path = Path(path)
    stream, csv_rows = _open_csv(source_path, inspected.encoding)
    try:
        for _ in range(inspected.header_row_number):
            next(csv_rows, None)
        for row_number, raw_values in enumerate(
            csv_rows, start=inspected.header_row_number + 1
        ):
            if not any(value.strip() for value in raw_values):
                continue
            yield _row_from_values(
                raw_values,
                row_number=row_number,
                headers=inspected.headers,
                contract=contract,
            )
    finally:
        stream.close()


def parse_myob_text(path: str | Path, contract: SourceContract) -> ParsedFile:
    """Compatibility wrapper that materialises rows; prefer ``iter_myob_rows`` for imports."""

    inspection = inspect_myob_text(path, contract)
    rows = tuple(iter_myob_rows(path, contract, inspection=inspection))
    return ParsedFile(
        source_type=inspection.source_type,
        source_path=inspection.source_path,
        encoding=inspection.encoding,
        file_sha256=inspection.file_sha256,
        headers=inspection.headers,
        header_row_number=inspection.header_row_number,
        rows=rows,
        issues=inspection.issues,
    )
