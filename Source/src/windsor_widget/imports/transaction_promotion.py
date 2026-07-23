"""Approval and audited promotion of MYOB sales, purchase and cover-order batches."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable, Iterable, Iterator, TypeVar

from sqlalchemy import and_, func, insert, or_, select, true, update
from sqlalchemy.orm import Session

from windsor_widget.db.models import (
    AppUser,
    AuditEvent,
    CoverOrderDocument,
    CoverOrderLine,
    CoverOrderSnapshot,
    CustomerAccount,
    ImportBatch,
    ImportIssue,
    ImportRow,
    Item,
    PurchaseDocument,
    PurchaseLine,
    SalesDocument,
    SalesLine,
    Supplier,
    TransactionLineObservation,
)
from windsor_widget.db.models.audit import utc_now
from windsor_widget.imports.promotion import ensure_app_user
from windsor_widget.imports.transaction_data import (
    PurchaseLineCandidate,
    SalesLineCandidate,
    TransactionMappingError,
    map_purchase_line,
    map_sales_line,
)

TRANSACTION_SOURCE_TYPES = (
    "sales_transactions",
    "cover_order_snapshot",
    "purchase_transactions",
)


class TransactionImportError(ValueError):
    """Raised when transaction promotion would be incomplete or ambiguous."""


@dataclass(frozen=True, slots=True)
class TransactionBatchReview:
    source_type: str
    import_batch_id: uuid.UUID
    status: str
    source_file_name: str
    row_count: int
    stored_row_count: int
    issue_count: int


@dataclass(frozen=True, slots=True)
class TransactionApprovalSummary:
    approved_batch_ids: tuple[uuid.UUID, ...]
    already_approved_batch_ids: tuple[uuid.UUID, ...]
    accepted_row_count: int


@dataclass(frozen=True, slots=True)
class TransactionChangeCounts:
    source_type: str
    documents_created: int = 0
    documents_updated: int = 0
    documents_unchanged: int = 0
    lines_created: int = 0
    lines_updated: int = 0
    lines_unchanged: int = 0
    lines_retired: int = 0
    snapshots_created: int = 0

    @property
    def document_total(self) -> int:
        return self.documents_created + self.documents_updated + self.documents_unchanged

    @property
    def line_total(self) -> int:
        return (
            self.lines_created
            + self.lines_updated
            + self.lines_unchanged
            + self.lines_retired
        )


@dataclass(frozen=True, slots=True)
class TransactionPromotionSummary:
    mode: str
    changes: tuple[TransactionChangeCounts, ...]
    committed_batch_ids: tuple[uuid.UUID, ...] = ()

    @property
    def document_total(self) -> int:
        return sum(change.document_total for change in self.changes)

    @property
    def line_total(self) -> int:
        return sum(change.line_total for change in self.changes)

    @property
    def lines_created(self) -> int:
        return sum(change.lines_created for change in self.changes)

    @property
    def lines_updated(self) -> int:
        return sum(change.lines_updated for change in self.changes)

    @property
    def lines_unchanged(self) -> int:
        return sum(change.lines_unchanged for change in self.changes)


@dataclass(frozen=True, slots=True)
class _StagedRow:
    import_row_id: int
    row_number: int
    row_sha256: str
    raw_json: str | None


@dataclass(slots=True)
class _DocumentPlan:
    entity_id: uuid.UUID
    master_entity_id: uuid.UUID
    document_number: str
    first_date: date
    last_date: date
    line_count: int = 0
    exists: bool = False
    changed: bool = False


T = TypeVar("T")


def _json_default(value: object) -> str:
    if isinstance(value, (Decimal, uuid.UUID, date, datetime)):
        return str(value)
    raise TypeError(f"Cannot serialise {type(value).__name__}")


def _json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, default=_json_default)


def _row_values(row: ImportRow) -> dict[str, str | None]:
    if not row.raw_json:
        raise TransactionImportError(
            f"Import row {row.import_row_id} has no raw_json and cannot be promoted."
        )
    property_marker = ', "values": '
    value_position = row.raw_json.find(property_marker)
    values: object
    try:
        if value_position >= 0:
            value_position += len(property_marker)
            values, _ = json.JSONDecoder().raw_decode(row.raw_json, value_position)
        else:
            payload = json.loads(row.raw_json)
            values = payload.get("values")
    except json.JSONDecodeError as exc:
        raise TransactionImportError(
            f"Import row {row.import_row_id} contains invalid raw_json."
        ) from exc
    if not isinstance(values, dict):
        raise TransactionImportError(
            f"Import row {row.import_row_id} raw_json does not contain a values object."
        )
    return {
        str(key): value if value is None or isinstance(value, str) else str(value)
        for key, value in values.items()
    }


def _candidate_dict(candidate: object) -> dict[str, Any]:
    return {field.name: getattr(candidate, field.name) for field in fields(candidate)}


def _chunks(values: list[dict[str, Any]], size: int = 1_000) -> Iterator[list[dict[str, Any]]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _bulk_insert(session: Session, model: type[Any], values: list[dict[str, Any]]) -> None:
    for chunk in _chunks(values):
        session.execute(insert(model), chunk)


def _bulk_update(
    session: Session,
    model: type[Any],
    primary_key: str,
    values: list[dict[str, Any]],
) -> None:
    for mapping in values:
        entity_id = mapping.pop(primary_key)
        session.execute(
            update(model)
            .where(getattr(model, primary_key) == entity_id)
            .values(**mapping)
        )


def review_transaction_batches(session: Session) -> tuple[TransactionBatchReview, ...]:
    batches = list(
        session.scalars(
            select(ImportBatch)
            .where(
                ImportBatch.source_type.in_(TRANSACTION_SOURCE_TYPES),
                ImportBatch.status.in_(("staged", "approved")),
                ImportBatch.committed_at.is_(None),
            )
            .order_by(ImportBatch.source_type, ImportBatch.received_at, ImportBatch.import_batch_id)
        )
    )
    reviews: list[TransactionBatchReview] = []
    for batch in batches:
        stored_rows = session.scalar(
            select(func.count(ImportRow.import_row_id)).where(
                ImportRow.import_batch_id == batch.import_batch_id
            )
        ) or 0
        issues = session.scalar(
            select(func.count(ImportIssue.import_issue_id)).where(
                ImportIssue.import_batch_id == batch.import_batch_id
            )
        ) or 0
        reviews.append(
            TransactionBatchReview(
                source_type=batch.source_type,
                import_batch_id=batch.import_batch_id,
                status=batch.status,
                source_file_name=batch.source_file_name,
                row_count=batch.row_count,
                stored_row_count=stored_rows,
                issue_count=issues,
            )
        )
    return tuple(reviews)


def _require_one_batch(
    reviews: Iterable[TransactionBatchReview],
    *,
    allowed_statuses: set[str],
    source_types: Iterable[str] = TRANSACTION_SOURCE_TYPES,
) -> dict[str, TransactionBatchReview]:
    requested = tuple(dict.fromkeys(source_types))
    if not requested:
        raise TransactionImportError("At least one transaction source type is required.")

    invalid = [source_type for source_type in requested if source_type not in TRANSACTION_SOURCE_TYPES]
    if invalid:
        raise TransactionImportError(
            "Unsupported transaction source type(s): " + ", ".join(invalid) + "."
        )

    grouped: dict[str, list[TransactionBatchReview]] = {
        source_type: [] for source_type in requested
    }
    for review in reviews:
        if review.source_type in grouped and review.status in allowed_statuses:
            grouped[review.source_type].append(review)

    selected: dict[str, TransactionBatchReview] = {}
    problems: list[str] = []
    for source_type in requested:
        candidates = grouped[source_type]
        if not candidates:
            problems.append(f"no eligible {source_type} batch")
        elif len(candidates) > 1:
            ids = ", ".join(str(candidate.import_batch_id) for candidate in candidates)
            problems.append(f"multiple eligible {source_type} batches ({ids})")
        else:
            selected[source_type] = candidates[0]
    if problems:
        raise TransactionImportError(
            "Transaction batch selection is not unambiguous: "
            + "; ".join(problems)
            + "."
        )
    return selected


def _validate_clean_batch(review: TransactionBatchReview) -> None:
    if review.issue_count:
        raise TransactionImportError(
            f"{review.source_type} batch {review.import_batch_id} has "
            f"{review.issue_count} review issue(s)."
        )
    if review.row_count != review.stored_row_count:
        raise TransactionImportError(
            f"{review.source_type} batch {review.import_batch_id} declares "
            f"{review.row_count} rows but stores {review.stored_row_count}."
        )


def approve_transaction_batches(
    session: Session,
    *,
    actor: AppUser,
    source_types: Iterable[str] = TRANSACTION_SOURCE_TYPES,
) -> TransactionApprovalSummary:
    requested = tuple(dict.fromkeys(source_types))
    selected = _require_one_batch(
        review_transaction_batches(session),
        allowed_statuses={"staged", "approved"},
        source_types=requested,
    )
    correlation_id = uuid.uuid4()
    approved: list[uuid.UUID] = []
    already: list[uuid.UUID] = []
    accepted_rows = 0

    for source_type in requested:
        review = selected[source_type]
        _validate_clean_batch(review)
        batch = session.get(ImportBatch, review.import_batch_id)
        if batch is None:
            raise TransactionImportError(f"Import batch {review.import_batch_id} disappeared.")

        invalid = session.scalar(
            select(func.count(ImportRow.import_row_id)).where(
                ImportRow.import_batch_id == batch.import_batch_id,
                ImportRow.status.not_in(("parsed", "accepted")),
            )
        ) or 0
        if invalid:
            raise TransactionImportError(
                f"{source_type} batch {batch.import_batch_id} contains {invalid} "
                "row(s) that are not parsed or accepted."
            )

        accepted_rows += batch.row_count
        if batch.status == "approved":
            already.append(batch.import_batch_id)
            continue

        session.execute(
            update(ImportRow)
            .where(ImportRow.import_batch_id == batch.import_batch_id)
            .values(status="accepted")
        )
        batch.status = "approved"
        batch.accepted_row_count = batch.row_count
        batch.rejected_row_count = 0
        session.add(
            AuditEvent(
                actor_user_id=actor.user_id,
                action="transaction_import_batch_approved",
                entity_type="import_batch",
                entity_id=str(batch.import_batch_id),
                correlation_id=correlation_id,
                source="myob_import",
                summary=f"Approved clean {source_type} batch with {batch.row_count} rows.",
                after_json=_json(
                    {
                        "source_type": source_type,
                        "status": "approved",
                        "row_count": batch.row_count,
                        "file_sha256": batch.file_sha256,
                    }
                ),
            )
        )
        approved.append(batch.import_batch_id)

    session.flush()
    return TransactionApprovalSummary(tuple(approved), tuple(already), accepted_rows)


def _approved_batches(
    session: Session,
    *,
    source_types: Iterable[str] = TRANSACTION_SOURCE_TYPES,
) -> dict[str, ImportBatch]:
    requested = tuple(dict.fromkeys(source_types))
    selected = _require_one_batch(
        review_transaction_batches(session),
        allowed_statuses={"approved"},
        source_types=requested,
    )
    batches: dict[str, ImportBatch] = {}
    for source_type, review in selected.items():
        _validate_clean_batch(review)
        batch = session.get(ImportBatch, review.import_batch_id)
        if batch is None:
            raise TransactionImportError(f"Import batch {review.import_batch_id} disappeared.")
        accepted = session.scalar(
            select(func.count(ImportRow.import_row_id)).where(
                ImportRow.import_batch_id == batch.import_batch_id,
                ImportRow.status == "accepted",
            )
        ) or 0
        if accepted != batch.row_count:
            raise TransactionImportError(
                f"{source_type} batch {batch.import_batch_id} has {accepted} accepted "
                f"rows but declares {batch.row_count}."
            )
        batches[source_type] = batch
    return batches


def _rows(session: Session, batch: ImportBatch) -> Iterator[_StagedRow]:
    """Yield accepted rows in fully buffered pages.

    SQL Server/pyodbc cannot execute promotion writes while a streaming SELECT cursor is
    still active on the same connection unless MARS is enabled. Keyset paging keeps the
    workflow independent of MARS: every page is consumed with ``all()`` before its rows
    are yielded to code that may insert or update operational records.
    """

    page_size = 2_000
    last_key: tuple[int, int] | None = None
    while True:
        statement = select(
            ImportRow.import_row_id,
            ImportRow.row_number,
            ImportRow.row_sha256,
            ImportRow.raw_json,
        ).where(
            ImportRow.import_batch_id == batch.import_batch_id,
            ImportRow.status == "accepted",
        )
        if last_key is not None:
            last_row_number, last_import_row_id = last_key
            statement = statement.where(
                or_(
                    ImportRow.row_number > last_row_number,
                    and_(
                        ImportRow.row_number == last_row_number,
                        ImportRow.import_row_id > last_import_row_id,
                    ),
                )
            )
        page = session.execute(
            statement.order_by(ImportRow.row_number, ImportRow.import_row_id).limit(page_size)
        ).all()
        if not page:
            return

        for import_row_id, row_number, row_sha256, raw_json in page:
            yield _StagedRow(
                import_row_id=import_row_id,
                row_number=row_number,
                row_sha256=row_sha256,
                raw_json=raw_json,
            )
        last_import_row_id, last_row_number, _, _ = page[-1]
        last_key = (last_row_number, last_import_row_id)


def _master_maps(session: Session) -> tuple[dict[str, uuid.UUID], dict[str, uuid.UUID], dict[str, uuid.UUID]]:
    customers = {
        entity.myob_record_id: entity.customer_account_id
        for entity in session.scalars(select(CustomerAccount))
        if entity.myob_record_id is not None
    }
    suppliers = {
        entity.myob_record_id: entity.supplier_id
        for entity in session.scalars(select(Supplier))
        if entity.myob_record_id is not None
    }
    items = {entity.item_number: entity.item_id for entity in session.scalars(select(Item))}
    return customers, suppliers, items


def _map_row(
    row: ImportRow,
    mapper: Callable[[dict[str, str | None]], T],
    source_type: str,
) -> T:
    try:
        return mapper(_row_values(row))
    except TransactionMappingError as exc:
        raise TransactionImportError(
            f"{source_type} row {row.row_number} cannot be mapped: {exc}"
        ) from exc



def _normalise_document_key(key: tuple[str, str]) -> tuple[str, str]:
    """Match SQL Server's case-insensitive document-number identity."""

    # SQL Server compares document numbers case-insensitively under the DEV
    # database collation. Python dictionary keys must use the same rule or a
    # case-only MYOB variant (for example STOCK / stock) becomes two inserts.
    return key[0], key[1].casefold()


def _plan_documents(
    session: Session,
    *,
    batch: ImportBatch,
    source_type: str,
    mapper: Callable[[dict[str, str | None]], SalesLineCandidate | PurchaseLineCandidate],
    master_ids: dict[str, uuid.UUID],
    existing_entities: Iterable[SalesDocument | PurchaseDocument],
    key_of_entity: Callable[[SalesDocument | PurchaseDocument], tuple[str, str]],
    id_of_entity: Callable[[SalesDocument | PurchaseDocument], uuid.UUID],
) -> dict[tuple[str, str], _DocumentPlan]:
    existing = {
        _normalise_document_key(key_of_entity(entity)): entity
        for entity in existing_entities
    }
    plans: dict[tuple[str, str], _DocumentPlan] = {}

    for row in _rows(session, batch):
        candidate = _map_row(row, mapper, source_type)
        raw_key = candidate.document_key
        key = _normalise_document_key(raw_key)
        record_id = raw_key[0]
        master_id = master_ids.get(record_id)
        if master_id is None:
            raise TransactionImportError(
                f"{source_type} row {row.row_number} references MYOB Record ID "
                f"{record_id!r}, which has no promoted master record."
            )
        plan = plans.get(key)
        if plan is None:
            entity = existing.get(key)
            if entity is None:
                document_number = raw_key[1]
                plan = _DocumentPlan(
                    entity_id=uuid.uuid4(),
                    master_entity_id=master_id,
                    document_number=document_number,
                    first_date=candidate.transaction_date,
                    last_date=candidate.transaction_date,
                )
            else:
                document_number = (
                    entity.invoice_no
                    if isinstance(entity, SalesDocument)
                    else entity.purchase_no
                )
                plan = _DocumentPlan(
                    entity_id=id_of_entity(entity),
                    master_entity_id=master_id,
                    document_number=document_number,
                    first_date=candidate.transaction_date,
                    last_date=candidate.transaction_date,
                    exists=True,
                )
            plans[key] = plan
        elif plan.master_entity_id != master_id:
            raise TransactionImportError(
                f"{source_type} document {key!r} resolves to multiple master records."
            )
        plan.first_date = min(plan.first_date, candidate.transaction_date)
        plan.last_date = max(plan.last_date, candidate.transaction_date)
        plan.line_count += 1

    for key, plan in plans.items():
        entity = existing.get(key)
        if entity is not None:
            plan.changed = any(
                (
                    (
                        entity.customer_account_id
                        if isinstance(entity, SalesDocument)
                        else entity.supplier_id
                    )
                    != plan.master_entity_id,
                    entity.first_transaction_date != plan.first_date,
                    entity.last_transaction_date != plan.last_date,
                    entity.line_count != plan.line_count,
                )
            )
    return plans

def _sales_business_mapping(
    candidate: SalesLineCandidate,
    *,
    item_id: uuid.UUID | None,
) -> dict[str, Any]:
    values = _candidate_dict(candidate)
    for key in ("myob_customer_record_id", "invoice_no"):
        values.pop(key)
    values["item_id"] = item_id
    return values


def _purchase_business_mapping(
    candidate: PurchaseLineCandidate,
    *,
    item_id: uuid.UUID | None,
) -> dict[str, Any]:
    values = _candidate_dict(candidate)
    for key in ("myob_supplier_record_id", "purchase_no"):
        values.pop(key)
    values["item_id"] = item_id
    return values


def _entity_business_mapping(entity: object, names: Iterable[str]) -> dict[str, Any]:
    return {name: getattr(entity, name) for name in names}


def _promote_documents_and_lines(
    session: Session,
    *,
    batch: ImportBatch,
    source_type: str,
    commit: bool,
    mapper: Callable[[dict[str, str | None]], SalesLineCandidate | PurchaseLineCandidate],
    document_model: type[SalesDocument] | type[PurchaseDocument],
    line_model: type[SalesLine] | type[PurchaseLine],
    document_primary_key: str,
    line_primary_key: str,
    document_foreign_key: str,
    master_ids: dict[str, uuid.UUID],
    item_ids: dict[str, uuid.UUID],
    entity_type: str,
) -> TransactionChangeCounts:
    existing_documents = list(session.scalars(select(document_model)))
    if document_model is SalesDocument:
        key_of_entity = lambda entity: (entity.myob_customer_record_id, entity.invoice_no)
        id_of_entity = lambda entity: entity.sales_document_id
    else:
        key_of_entity = lambda entity: (entity.myob_supplier_record_id, entity.purchase_no)
        id_of_entity = lambda entity: entity.purchase_document_id

    plans = _plan_documents(
        session,
        batch=batch,
        source_type=source_type,
        mapper=mapper,
        master_ids=master_ids,
        existing_entities=existing_documents,
        key_of_entity=key_of_entity,
        id_of_entity=id_of_entity,
    )

    docs_created = sum(not plan.exists for plan in plans.values())
    docs_updated = sum(plan.exists and plan.changed for plan in plans.values())
    docs_unchanged = sum(plan.exists and not plan.changed for plan in plans.values())

    if commit:
        document_inserts: list[dict[str, Any]] = []
        document_updates: list[dict[str, Any]] = []
        now = utc_now()
        for key, plan in plans.items():
            if document_model is SalesDocument:
                mapping = {
                    "sales_document_id": plan.entity_id,
                    "customer_account_id": plan.master_entity_id,
                    "myob_customer_record_id": key[0],
                    "invoice_no": plan.document_number,
                }
            else:
                mapping = {
                    "purchase_document_id": plan.entity_id,
                    "supplier_id": plan.master_entity_id,
                    "myob_supplier_record_id": key[0],
                    "purchase_no": plan.document_number,
                }
            mapping.update(
                {
                    "first_transaction_date": plan.first_date,
                    "last_transaction_date": plan.last_date,
                    "line_count": plan.line_count,
                    "last_import_batch_id": batch.import_batch_id,
                    "source_updated_at": now,
                }
            )
            if plan.exists:
                mapping[document_primary_key] = plan.entity_id
                document_updates.append(mapping)
            else:
                mapping["first_import_batch_id"] = batch.import_batch_id
                document_inserts.append(mapping)
        _bulk_insert(session, document_model, document_inserts)
        _bulk_update(session, document_model, document_primary_key, document_updates)
        session.flush()

    existing_lines = list(session.scalars(select(line_model)))
    existing_by_key = {
        (getattr(line, document_foreign_key), line.line_sequence): line
        for line in existing_lines
        if getattr(line, document_foreign_key) in {plan.entity_id for plan in plans.values()}
    }
    business_names = tuple(
        column.name
        for column in line_model.__table__.columns
        if column.name
        not in {
            line_primary_key,
            document_foreign_key,
            "line_sequence",
            "source_import_row_id",
            "source_row_sha256",
            "last_import_batch_id",
            "is_active",
        }
    )

    sequences: dict[tuple[str, str], int] = {}
    seen_line_keys: set[tuple[uuid.UUID, int]] = set()
    line_inserts: list[dict[str, Any]] = []
    line_updates: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    lines_created = lines_updated = lines_unchanged = 0

    def flush_buffers() -> None:
        if not commit:
            return
        _bulk_insert(session, line_model, line_inserts)
        _bulk_update(session, line_model, line_primary_key, line_updates)
        _bulk_insert(session, TransactionLineObservation, observations)
        line_inserts.clear()
        line_updates.clear()
        observations.clear()

    for row in _rows(session, batch):
        candidate = _map_row(row, mapper, source_type)
        key = _normalise_document_key(candidate.document_key)
        sequences[key] = sequences.get(key, 0) + 1
        sequence = sequences[key]
        plan = plans[key]
        item_number = candidate.myob_item_number
        item_id = item_ids.get(item_number) if item_number is not None else None
        if item_number is not None and item_id is None:
            raise TransactionImportError(
                f"{source_type} row {row.row_number} references item {item_number!r}, "
                "which has no promoted item master record."
            )
        if isinstance(candidate, SalesLineCandidate):
            business = _sales_business_mapping(candidate, item_id=item_id)
        else:
            business = _purchase_business_mapping(candidate, item_id=item_id)

        line_key = (plan.entity_id, sequence)
        seen_line_keys.add(line_key)
        entity = existing_by_key.get(line_key)
        if entity is None:
            action = "created"
            lines_created += 1
            entity_id = uuid.uuid4()
            if commit:
                line_inserts.append(
                    {
                        line_primary_key: entity_id,
                        document_foreign_key: plan.entity_id,
                        "line_sequence": sequence,
                        "source_import_row_id": row.import_row_id,
                        "source_row_sha256": row.row_sha256,
                        "last_import_batch_id": batch.import_batch_id,
                        "is_active": True,
                        **business,
                    }
                )
        else:
            entity_id = getattr(entity, line_primary_key)
            before = _entity_business_mapping(entity, business_names)
            changed = before != business or not entity.is_active
            if changed:
                action = "reactivated" if not entity.is_active and before == business else "updated"
                lines_updated += 1
            else:
                action = "unchanged"
                lines_unchanged += 1
            if commit:
                line_updates.append(
                    {
                        line_primary_key: entity_id,
                        "source_import_row_id": row.import_row_id,
                        "source_row_sha256": row.row_sha256,
                        "last_import_batch_id": batch.import_batch_id,
                        "is_active": True,
                        **business,
                    }
                )

        if commit:
            observations.append(
                {
                    "source_type": source_type,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "import_batch_id": batch.import_batch_id,
                    "import_row_id": row.import_row_id,
                    "action": action,
                    "observed_at": utc_now(),
                }
            )
            if len(line_inserts) + len(line_updates) >= 1_000:
                flush_buffers()

    lines_retired = 0
    retired_updates: list[dict[str, Any]] = []
    for line_key, entity in existing_by_key.items():
        if line_key not in seen_line_keys and entity.is_active:
            lines_retired += 1
            if commit:
                retired_updates.append(
                    {
                        line_primary_key: getattr(entity, line_primary_key),
                        "is_active": False,
                        "last_import_batch_id": batch.import_batch_id,
                    }
                )
    if commit:
        flush_buffers()
        _bulk_update(session, line_model, line_primary_key, retired_updates)
        session.flush()

    return TransactionChangeCounts(
        source_type=source_type,
        documents_created=docs_created,
        documents_updated=docs_updated,
        documents_unchanged=docs_unchanged,
        lines_created=lines_created,
        lines_updated=lines_updated,
        lines_unchanged=lines_unchanged,
        lines_retired=lines_retired,
    )


def _promote_cover_snapshot(
    session: Session,
    *,
    batch: ImportBatch,
    actor: AppUser | None,
    commit: bool,
    customer_ids: dict[str, uuid.UUID],
    item_ids: dict[str, uuid.UUID],
) -> TransactionChangeCounts:
    if session.scalar(
        select(CoverOrderSnapshot).where(
            CoverOrderSnapshot.import_batch_id == batch.import_batch_id
        )
    ) is not None:
        raise TransactionImportError(
            f"Cover-order batch {batch.import_batch_id} already has an operational snapshot."
        )

    plans: dict[tuple[str, str], _DocumentPlan] = {}
    for row in _rows(session, batch):
        candidate = _map_row(row, map_sales_line, "cover_order_snapshot")
        customer_id = customer_ids.get(candidate.myob_customer_record_id)
        if customer_id is None:
            raise TransactionImportError(
                f"cover_order_snapshot row {row.row_number} references MYOB Record ID "
                f"{candidate.myob_customer_record_id!r}, which has no customer account."
            )
        key = _normalise_document_key(candidate.document_key)
        plan = plans.get(key)
        if plan is None:
            plan = _DocumentPlan(
                entity_id=uuid.uuid4(),
                master_entity_id=customer_id,
                document_number=candidate.invoice_no,
                first_date=candidate.transaction_date,
                last_date=candidate.transaction_date,
            )
            plans[key] = plan
        plan.first_date = min(plan.first_date, candidate.transaction_date)
        plan.last_date = max(plan.last_date, candidate.transaction_date)
        plan.line_count += 1

    snapshot_id = uuid.uuid4()
    if commit:
        if actor is None:
            raise TransactionImportError("A user is required to commit a cover snapshot.")
        session.execute(
            update(CoverOrderSnapshot)
            .where(CoverOrderSnapshot.is_current == true())
            .values(is_current=False)
        )
        session.add(
            CoverOrderSnapshot(
                cover_order_snapshot_id=snapshot_id,
                import_batch_id=batch.import_batch_id,
                captured_at=batch.received_at,
                source_file_name=batch.source_file_name,
                document_count=len(plans),
                row_count=batch.row_count,
                is_current=True,
                committed_at=utc_now(),
                committed_by_user_id=actor.user_id,
            )
        )

        # The snapshot parent must exist in SQL Server before bulk inserting
        # cover-order documents that reference it.
        session.flush()

        documents = []
        for key, plan in plans.items():
            documents.append(
                {
                    "cover_order_document_id": plan.entity_id,
                    "cover_order_snapshot_id": snapshot_id,
                    "customer_account_id": plan.master_entity_id,
                    "myob_customer_record_id": key[0],
                    "invoice_no": plan.document_number,
                    "first_transaction_date": plan.first_date,
                    "last_transaction_date": plan.last_date,
                    "line_count": plan.line_count,
                }
            )
        _bulk_insert(session, CoverOrderDocument, documents)
        session.flush()

    sequences: dict[tuple[str, str], int] = {}
    line_inserts: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []

    def flush_buffers() -> None:
        if not commit:
            return
        _bulk_insert(session, CoverOrderLine, line_inserts)
        _bulk_insert(session, TransactionLineObservation, observations)
        line_inserts.clear()
        observations.clear()

    for row in _rows(session, batch):
        candidate = _map_row(row, map_sales_line, "cover_order_snapshot")
        key = _normalise_document_key(candidate.document_key)
        sequences[key] = sequences.get(key, 0) + 1
        item_number = candidate.myob_item_number
        item_id = item_ids.get(item_number) if item_number is not None else None
        if item_number is not None and item_id is None:
            raise TransactionImportError(
                f"cover_order_snapshot row {row.row_number} references item "
                f"{item_number!r}, which has no promoted item."
            )
        line_id = uuid.uuid4()
        if commit:
            business = _sales_business_mapping(candidate, item_id=item_id)
            business.pop("currency_code")
            business.pop("exchange_rate")
            line_inserts.append(
                {
                    "cover_order_line_id": line_id,
                    "cover_order_document_id": plans[key].entity_id,
                    "line_sequence": sequences[key],
                    "source_import_row_id": row.import_row_id,
                    "source_row_sha256": row.row_sha256,
                    **business,
                }
            )
            observations.append(
                {
                    "source_type": "cover_order_snapshot",
                    "entity_type": "cover_order_line",
                    "entity_id": line_id,
                    "import_batch_id": batch.import_batch_id,
                    "import_row_id": row.import_row_id,
                    "action": "created",
                    "observed_at": utc_now(),
                }
            )
            if len(line_inserts) >= 1_000:
                flush_buffers()
    if commit:
        flush_buffers()
        session.flush()

    return TransactionChangeCounts(
        source_type="cover_order_snapshot",
        documents_created=len(plans),
        lines_created=batch.row_count,
        snapshots_created=1,
    )


def promote_transaction_batches(
    session: Session,
    *,
    commit: bool,
    actor: AppUser | None = None,
    source_types: Iterable[str] = TRANSACTION_SOURCE_TYPES,
) -> TransactionPromotionSummary:
    if commit and actor is None:
        raise TransactionImportError("An application user is required for committed promotion.")

    requested = tuple(dict.fromkeys(source_types))
    batches = _approved_batches(session, source_types=requested)
    customer_ids, supplier_ids, item_ids = _master_maps(session)
    changes_list: list[TransactionChangeCounts] = []

    for source_type in requested:
        if source_type == "sales_transactions":
            changes_list.append(
                _promote_documents_and_lines(
                    session,
                    batch=batches[source_type],
                    source_type=source_type,
                    commit=commit,
                    mapper=map_sales_line,
                    document_model=SalesDocument,
                    line_model=SalesLine,
                    document_primary_key="sales_document_id",
                    line_primary_key="sales_line_id",
                    document_foreign_key="sales_document_id",
                    master_ids=customer_ids,
                    item_ids=item_ids,
                    entity_type="sales_line",
                )
            )
        elif source_type == "cover_order_snapshot":
            changes_list.append(
                _promote_cover_snapshot(
                    session,
                    batch=batches[source_type],
                    actor=actor,
                    commit=commit,
                    customer_ids=customer_ids,
                    item_ids=item_ids,
                )
            )
        elif source_type == "purchase_transactions":
            changes_list.append(
                _promote_documents_and_lines(
                    session,
                    batch=batches[source_type],
                    source_type=source_type,
                    commit=commit,
                    mapper=map_purchase_line,
                    document_model=PurchaseDocument,
                    line_model=PurchaseLine,
                    document_primary_key="purchase_document_id",
                    line_primary_key="purchase_line_id",
                    document_foreign_key="purchase_document_id",
                    master_ids=supplier_ids,
                    item_ids=item_ids,
                    entity_type="purchase_line",
                )
            )
        else:
            raise TransactionImportError(f"Unsupported transaction source type: {source_type}.")

    changes = tuple(changes_list)
    committed_ids: list[uuid.UUID] = []
    if commit:
        correlation_id = uuid.uuid4()
        now = utc_now()
        by_source = {change.source_type: change for change in changes}
        for source_type in requested:
            batch = batches[source_type]
            session.execute(
                update(ImportRow)
                .where(ImportRow.import_batch_id == batch.import_batch_id)
                .values(status="committed")
            )
            batch.status = "committed"
            batch.committed_at = now
            batch.accepted_row_count = batch.row_count
            batch.rejected_row_count = 0
            change = by_source[source_type]
            assert actor is not None
            session.add(
                AuditEvent(
                    actor_user_id=actor.user_id,
                    action="transaction_import_batch_committed",
                    entity_type="import_batch",
                    entity_id=str(batch.import_batch_id),
                    correlation_id=correlation_id,
                    source="myob_import",
                    summary=(
                        f"Committed {source_type}: {change.document_total} documents, "
                        f"{change.lines_created} lines created, {change.lines_updated} updated, "
                        f"{change.lines_unchanged} unchanged."
                    ),
                    after_json=_json(
                        {
                            "source_type": source_type,
                            "status": "committed",
                            "row_count": batch.row_count,
                            "changes": asdict(change),
                        }
                    ),
                )
            )
            committed_ids.append(batch.import_batch_id)
        session.flush()

    return TransactionPromotionSummary(
        mode="committed" if commit else "preview",
        changes=changes,
        committed_batch_ids=tuple(committed_ids),
    )


__all__ = [
    "TRANSACTION_SOURCE_TYPES",
    "TransactionApprovalSummary",
    "TransactionBatchReview",
    "TransactionChangeCounts",
    "TransactionImportError",
    "TransactionPromotionSummary",
    "approve_transaction_batches",
    "ensure_app_user",
    "promote_transaction_batches",
    "review_transaction_batches",
]
