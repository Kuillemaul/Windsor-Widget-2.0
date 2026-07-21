"""Loss-preserving, streaming parser for MYOB comma-delimited text exports."""

from __future__ import annotations

import codecs
import csv
import hashlib
import heapq
import io
import itertools
import re
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from windsor_widget.imports.contracts import SourceContract
from windsor_widget.imports.normalization import clean_text, parse_date, parse_decimal


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
    raw_text: str | None = None
    repairs: tuple[str, ...] = field(default_factory=tuple)


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


@dataclass(frozen=True, slots=True)
class _RepairState:
    position: int
    fields: tuple[str, ...]
    current: str
    in_quotes: bool
    at_field_start: bool
    repairs: tuple[str, ...]
    repair_cost: int


_MAX_REPAIR_STATES = 50_000
_MAX_MULTILINE_PHYSICAL_LINES = 20


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


def _parse_single_csv_record(raw_text: str, *, strict: bool) -> tuple[list[str] | None, str | None]:
    try:
        rows = list(csv.reader(io.StringIO(raw_text), strict=strict))
    except csv.Error as exc:
        return None, str(exc)

    rows = [row for row in rows if any(value.strip() for value in row)]
    if len(rows) == 1:
        return rows[0], None
    if not rows:
        return [], None
    return None, "multiple CSV records were found"


def _find_header(
    stream: TextIO, contract: SourceContract, scan_limit: int = 50
) -> tuple[int, list[str]] | None:
    """Find the one-line MYOB header by physical line number."""

    for row_number, raw_line in enumerate(stream, start=1):
        if row_number > scan_limit:
            break
        parsed, error = _parse_single_csv_record(raw_line, strict=True)
        if error is not None or parsed is None:
            continue
        header_set = {value.strip() for value in parsed}
        if contract.required_headers.issubset(header_set):
            return row_number, [value.strip() for value in parsed]
    return None


def _natural_key(values: dict[str, str | None], fields: tuple[str, ...]) -> str | None:
    parts = [clean_text(values.get(field)) for field in fields]
    if not parts or all(part is None for part in parts):
        return None
    return "|".join(part or "" for part in parts)


def _candidate_is_plausible(
    fields: tuple[str, ...], headers: tuple[str, ...], contract: SourceContract
) -> bool:
    values = dict(zip(headers, fields, strict=True))
    for field_name in contract.natural_key_fields:
        if field_name in contract.optional_natural_key_fields:
            continue
        if clean_text(values.get(field_name)) is None:
            return False

    date_value = clean_text(values.get("Date"))
    if date_value is not None and parse_date(date_value) is None:
        return False

    quantity_value = clean_text(values.get("Quantity"))
    if quantity_value is not None and parse_decimal(quantity_value) is None:
        return False

    record_id = clean_text(values.get("Record ID"))
    if record_id is not None and re.fullmatch(r"\d+", record_id) is None:
        return False

    return True


def _unique_repairs(repairs: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(repairs))


def _repair_malformed_record(
    raw_text: str,
    *,
    headers: tuple[str, ...],
    contract: SourceContract,
) -> tuple[list[str], tuple[str, ...]] | None:
    """Repair only a uniquely reconstructable, single physical MYOB record.

    MYOB occasionally emits doubled opening/closing quotes or inch marks that are
    not legal CSV.  This bounded best-first parser explores only quote
    interpretations, requires the exact declared column count and validates the
    source's stable key/date/quantity anchors.  Ties remain quarantined.
    """

    if "\n" in raw_text.rstrip("\r\n") or "\r" in raw_text.rstrip("\r\n"):
        # Multiline records must be valid strict CSV; never invent a cross-line repair.
        return None

    expected = len(headers)
    counter = itertools.count()
    initial = _RepairState(0, (), "", False, True, (), 0)
    queue: list[tuple[int, int, int, _RepairState]] = [
        (0, 0, next(counter), initial)
    ]
    seen: dict[tuple[object, ...], int] = {
        (0, (), "", False, True): 0
    }
    candidates: dict[tuple[str, ...], tuple[int, tuple[str, ...]]] = {}
    best_cost: int | None = None
    explored = 0

    def push(
        state: _RepairState,
        *,
        position: int,
        fields: tuple[str, ...],
        current: str,
        in_quotes: bool,
        at_field_start: bool,
        repair: str | None = None,
        weight: int = 0,
    ) -> None:
        if len(fields) > expected:
            return
        cost = state.repair_cost + weight
        if best_cost is not None and cost > best_cost + 4:
            return
        repairs = state.repairs + ((repair,) if repair else ())
        candidate = _RepairState(
            position,
            fields,
            current,
            in_quotes,
            at_field_start,
            repairs,
            cost,
        )
        key = (position, fields, current, in_quotes, at_field_start)
        if seen.get(key, 10**9) <= cost:
            return
        seen[key] = cost
        heapq.heappush(queue, (cost, -position, next(counter), candidate))

    while queue and explored < _MAX_REPAIR_STATES:
        _, _, _, state = heapq.heappop(queue)
        explored += 1
        if best_cost is not None and state.repair_cost > best_cost + 4:
            break

        if state.position >= len(raw_text):
            if state.in_quotes:
                continue
            fields = state.fields + (state.current,)
            if len(fields) != expected:
                continue
            if not _candidate_is_plausible(fields, headers, contract):
                continue
            if best_cost is None:
                best_cost = state.repair_cost
            previous = candidates.get(fields)
            if previous is None or state.repair_cost < previous[0]:
                candidates[fields] = (state.repair_cost, state.repairs)
            continue

        position = state.position
        character = raw_text[position]
        following = raw_text[position + 1] if position + 1 < len(raw_text) else None
        following_two = raw_text[position + 2] if position + 2 < len(raw_text) else None
        preceding = raw_text[position - 1] if position else None

        if character in "\r\n":
            if state.in_quotes:
                push(
                    state,
                    position=position + 1,
                    fields=state.fields,
                    current=state.current + character,
                    in_quotes=True,
                    at_field_start=False,
                )
            else:
                push(
                    state,
                    position=position + 1,
                    fields=state.fields,
                    current=state.current,
                    in_quotes=False,
                    at_field_start=state.at_field_start,
                )
            continue

        if character == ",":
            if state.in_quotes:
                push(
                    state,
                    position=position + 1,
                    fields=state.fields,
                    current=state.current + character,
                    in_quotes=True,
                    at_field_start=False,
                )
            else:
                push(
                    state,
                    position=position + 1,
                    fields=state.fields + (state.current,),
                    current="",
                    in_quotes=False,
                    at_field_start=True,
                )
            continue

        if character != '"':
            push(
                state,
                position=position + 1,
                fields=state.fields,
                current=state.current + character,
                in_quotes=state.in_quotes,
                at_field_start=False,
            )
            continue

        if not state.in_quotes:
            if state.at_field_start:
                # Normal CSV opening quote.
                push(
                    state,
                    position=position + 1,
                    fields=state.fields,
                    current=state.current,
                    in_quotes=True,
                    at_field_start=False,
                )
                if following == '"':
                    # MYOB sometimes writes ""Name, With Comma"".
                    push(
                        state,
                        position=position + 2,
                        fields=state.fields,
                        current=state.current,
                        in_quotes=True,
                        at_field_start=False,
                        repair="collapsed duplicated opening quote",
                        weight=1,
                    )
            else:
                literal_weight = 1 if preceding and preceding.isdigit() else 3
                push(
                    state,
                    position=position + 1,
                    fields=state.fields,
                    current=state.current + '"',
                    in_quotes=False,
                    at_field_start=False,
                    repair="treated stray quote as literal text",
                    weight=literal_weight,
                )
                push(
                    state,
                    position=position + 1,
                    fields=state.fields,
                    current=state.current,
                    in_quotes=False,
                    at_field_start=False,
                    repair="removed stray quote",
                    weight=2,
                )
            continue

        if following == '"':
            # Standard escaped quote remains the zero-cost path.
            push(
                state,
                position=position + 2,
                fields=state.fields,
                current=state.current + '"',
                in_quotes=True,
                at_field_start=False,
            )
            if following_two in {",", None, "\r", "\n"}:
                if preceding and preceding.isdigit():
                    push(
                        state,
                        position=position + 2,
                        fields=state.fields,
                        current=state.current + '"',
                        in_quotes=False,
                        at_field_start=False,
                        repair="closed malformed quoted field after inch mark",
                        weight=1,
                    )
                    push(
                        state,
                        position=position + 2,
                        fields=state.fields,
                        current=state.current,
                        in_quotes=False,
                        at_field_start=False,
                        repair="collapsed duplicated closing quote",
                        weight=4,
                    )
                else:
                    push(
                        state,
                        position=position + 2,
                        fields=state.fields,
                        current=state.current,
                        in_quotes=False,
                        at_field_start=False,
                        repair="collapsed duplicated closing quote",
                        weight=1,
                    )
                    push(
                        state,
                        position=position + 2,
                        fields=state.fields,
                        current=state.current + '"',
                        in_quotes=False,
                        at_field_start=False,
                        repair="closed malformed quoted field after literal quote",
                        weight=4,
                    )
            continue

        if following in {",", None, "\r", "\n"}:
            # Normal CSV closing quote.
            push(
                state,
                position=position + 1,
                fields=state.fields,
                current=state.current,
                in_quotes=False,
                at_field_start=False,
            )
            if following == ",":
                # A digit followed by ", is commonly an inch mark inside MYOB text.
                literal_weight = 1 if preceding and preceding.isdigit() else 3
                push(
                    state,
                    position=position + 1,
                    fields=state.fields,
                    current=state.current + '"',
                    in_quotes=True,
                    at_field_start=False,
                    repair="treated quote before comma as literal text",
                    weight=literal_weight,
                )
            continue

        # A quote inside a quoted field that is not followed by a delimiter is illegal
        # CSV.  Dropping it is preferred to inventing punctuation in names/memos.
        push(
            state,
            position=position + 1,
            fields=state.fields,
            current=state.current,
            in_quotes=True,
            at_field_start=False,
            repair="removed unescaped inner quote",
            weight=1,
        )
        push(
            state,
            position=position + 1,
            fields=state.fields,
            current=state.current + '"',
            in_quotes=True,
            at_field_start=False,
            repair="treated inner quote as literal text",
            weight=3,
        )

    if not candidates:
        return None

    ranked = sorted(
        (cost, fields, repairs)
        for fields, (cost, repairs) in candidates.items()
    )
    minimum_cost = ranked[0][0]
    minimum = [candidate for candidate in ranked if candidate[0] == minimum_cost]
    if len(minimum) != 1:
        return None

    _, fields, repairs = minimum[0]
    return list(fields), _unique_repairs(repairs)


def inspect_myob_text(path: str | Path, contract: SourceContract) -> MyobFileInspection:
    """Inspect encoding, hash and header without loading transaction rows into memory."""

    source_path = Path(path)
    encoding, file_hash = _file_identity(source_path)
    with source_path.open("r", encoding=encoding, newline=None) as stream:
        found = _find_header(stream, contract)

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
    raw_text: str,
    repairs: tuple[str, ...] = (),
    structural_issue: str | None = None,
) -> ParsedRow:
    row_issues: list[ParseIssue] = []
    column_count_matches = structural_issue is None and len(raw_values) == len(headers)
    if structural_issue == "malformed_csv_record":
        row_issues.append(
            ParseIssue(
                severity="error",
                issue_code="malformed_csv_record",
                row_number=row_number,
                message="Row contains malformed CSV quoting and could not be repaired uniquely.",
            )
        )
    elif not column_count_matches:
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
    natural_key = _natural_key(values, contract.natural_key_fields) if column_count_matches else None

    # Structural damage makes downstream key positions unreliable.  Do not create
    # misleading duplicate key errors for the same quarantined row.
    if column_count_matches:
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

    return ParsedRow(
        row_number=row_number,
        values=values,
        raw_values=tuple(raw_values),
        natural_key=natural_key,
        row_sha256=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
        review_required=bool(row_issues),
        issues=tuple(row_issues),
        raw_text=raw_text,
        repairs=repairs,
    )


def _fallback_values(raw_text: str) -> list[str]:
    """Return loss-preserving comma fragments for a row that remains quarantined."""

    rows = list(csv.reader(io.StringIO(raw_text), quoting=csv.QUOTE_NONE))
    if not rows:
        return []
    if len(rows) == 1:
        return rows[0]
    flattened: list[str] = []
    for row in rows:
        if flattened:
            flattened.append("\\n")
        flattened.extend(row)
    return flattened


def _iter_data_records(
    stream: TextIO,
    *,
    first_row_number: int,
    headers: tuple[str, ...],
    contract: SourceContract,
) -> Iterator[tuple[int, str, list[str], tuple[str, ...], str | None]]:
    physical_lines = enumerate(stream, start=first_row_number)
    pushed_back: deque[tuple[int, str]] = deque()

    def next_line() -> tuple[int, str] | None:
        if pushed_back:
            return pushed_back.popleft()
        try:
            return next(physical_lines)
        except StopIteration:
            return None

    while True:
        current = next_line()
        if current is None:
            return
        row_number, line = current
        if not line.strip():
            continue

        strict_values, strict_error = _parse_single_csv_record(line, strict=True)
        if strict_error is None and strict_values is not None and len(strict_values) == len(headers):
            yield row_number, line, strict_values, (), None
            continue

        repaired = _repair_malformed_record(line, headers=headers, contract=contract)
        if repaired is not None:
            repaired_values, repairs = repaired
            yield row_number, line, repaired_values, repairs, None
            continue

        if strict_error != "unexpected end of data":
            fallback = strict_values if strict_values is not None else _fallback_values(line)
            issue = "column_count_mismatch" if strict_error is None else "malformed_csv_record"
            yield row_number, line, fallback, (), issue
            continue

        # A valid quoted field may span physical lines.  Accumulate only while the
        # following physical line cannot stand alone as a complete source record.
        pending = [line]
        completed = False
        while len(pending) < _MAX_MULTILINE_PHYSICAL_LINES:
            following = next_line()
            if following is None:
                break
            following_number, following_line = following

            standalone, standalone_error = _parse_single_csv_record(
                following_line, strict=True
            )
            standalone_repair = _repair_malformed_record(
                following_line, headers=headers, contract=contract
            )
            if (
                standalone_error is None
                and standalone is not None
                and len(standalone) == len(headers)
            ) or standalone_repair is not None:
                pushed_back.appendleft((following_number, following_line))
                break

            pending.append(following_line)
            combined = "".join(pending)
            combined_values, combined_error = _parse_single_csv_record(
                combined, strict=True
            )
            if (
                combined_error is None
                and combined_values is not None
                and len(combined_values) == len(headers)
            ):
                yield row_number, combined, combined_values, (), None
                completed = True
                break
            if combined_error != "unexpected end of data":
                break

        if completed:
            continue

        raw_text = "".join(pending)
        yield row_number, raw_text, _fallback_values(raw_text), (), "malformed_csv_record"


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
    with source_path.open("r", encoding=inspected.encoding, newline=None) as stream:
        for _ in range(inspected.header_row_number):
            next(stream, None)
        for row_number, raw_text, raw_values, repairs, structural_issue in _iter_data_records(
            stream,
            first_row_number=inspected.header_row_number + 1,
            headers=inspected.headers,
            contract=contract,
        ):
            if not any(value.strip() for value in raw_values):
                continue
            yield _row_from_values(
                raw_values,
                row_number=row_number,
                headers=inspected.headers,
                contract=contract,
                raw_text=raw_text,
                repairs=repairs,
                structural_issue=structural_issue,
            )


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
