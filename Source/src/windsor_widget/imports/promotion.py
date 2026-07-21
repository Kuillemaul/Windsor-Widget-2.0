"""Explicit approval and audited promotion of clean MYOB master-data batches.

This module deliberately excludes sales, cover-order and purchase transactions.
Those sources remain in immutable staging until their operational schemas exist.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable, Iterable

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from windsor_widget.db.models import (
    AppUser,
    AuditEvent,
    CustomerAccount,
    ImportBatch,
    ImportIssue,
    ImportRow,
    Item,
    Supplier,
)
from windsor_widget.db.models.audit import utc_now
from windsor_widget.imports.master_data import (
    CustomerMasterCandidate,
    ItemMasterCandidate,
    SupplierMasterCandidate,
    map_customer_master,
    map_item_master,
    map_supplier_master,
)

MASTER_SOURCE_TYPES = ("supplier_master", "customer_master", "item_master")


class MasterImportError(ValueError):
    """Raised when approval or promotion would be ambiguous or unsafe."""


@dataclass(frozen=True, slots=True)
class MasterBatchReview:
    source_type: str
    import_batch_id: uuid.UUID
    status: str
    source_file_name: str
    row_count: int
    stored_row_count: int
    issue_count: int


@dataclass(frozen=True, slots=True)
class ApprovalSummary:
    approved_batch_ids: tuple[uuid.UUID, ...]
    already_approved_batch_ids: tuple[uuid.UUID, ...]
    accepted_row_count: int


@dataclass(frozen=True, slots=True)
class ChangeCounts:
    source_type: str
    created: int = 0
    updated: int = 0
    unchanged: int = 0

    @property
    def total(self) -> int:
        return self.created + self.updated + self.unchanged


@dataclass(frozen=True, slots=True)
class MasterPromotionSummary:
    mode: str
    changes: tuple[ChangeCounts, ...]
    committed_batch_ids: tuple[uuid.UUID, ...] = ()

    @property
    def created(self) -> int:
        return sum(change.created for change in self.changes)

    @property
    def updated(self) -> int:
        return sum(change.updated for change in self.changes)

    @property
    def unchanged(self) -> int:
        return sum(change.unchanged for change in self.changes)

    @property
    def total(self) -> int:
        return sum(change.total for change in self.changes)


def _json_default(value: object) -> str:
    if isinstance(value, (Decimal, uuid.UUID, date, datetime)):
        return str(value)
    raise TypeError(f"Cannot serialise {type(value).__name__}")


def _json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, default=_json_default)


def _row_values(row: ImportRow) -> dict[str, str | None]:
    if not row.raw_json:
        raise MasterImportError(
            f"Import row {row.import_row_id} has no raw_json and cannot be promoted."
        )
    try:
        payload = json.loads(row.raw_json)
    except json.JSONDecodeError as exc:
        raise MasterImportError(
            f"Import row {row.import_row_id} contains invalid raw_json."
        ) from exc
    values = payload.get("values")
    if not isinstance(values, dict):
        raise MasterImportError(
            f"Import row {row.import_row_id} raw_json does not contain a values object."
        )
    return {
        str(key): value if value is None or isinstance(value, str) else str(value)
        for key, value in values.items()
    }


def ensure_app_user(session: Session, *, username: str, display_name: str) -> AppUser:
    normalized_username = username.strip()
    normalized_display_name = display_name.strip()
    if not normalized_username:
        raise MasterImportError("Approval username cannot be empty.")
    if not normalized_display_name:
        raise MasterImportError("Approval display name cannot be empty.")

    user = session.scalar(select(AppUser).where(AppUser.username == normalized_username))
    if user is None:
        user = AppUser(username=normalized_username, display_name=normalized_display_name)
        session.add(user)
        session.flush()
    elif not user.is_active:
        raise MasterImportError(f"Application user {normalized_username!r} is inactive.")
    return user


def review_master_batches(session: Session) -> tuple[MasterBatchReview, ...]:
    batches = list(
        session.scalars(
            select(ImportBatch)
            .where(
                ImportBatch.source_type.in_(MASTER_SOURCE_TYPES),
                ImportBatch.status.in_(("staged", "approved")),
                ImportBatch.committed_at.is_(None),
            )
            .order_by(ImportBatch.source_type, ImportBatch.received_at, ImportBatch.import_batch_id)
        )
    )
    reviews: list[MasterBatchReview] = []
    for batch in batches:
        stored_row_count = session.scalar(
            select(func.count(ImportRow.import_row_id)).where(
                ImportRow.import_batch_id == batch.import_batch_id
            )
        ) or 0
        issue_count = session.scalar(
            select(func.count(ImportIssue.import_issue_id)).where(
                ImportIssue.import_batch_id == batch.import_batch_id
            )
        ) or 0
        reviews.append(
            MasterBatchReview(
                source_type=batch.source_type,
                import_batch_id=batch.import_batch_id,
                status=batch.status,
                source_file_name=batch.source_file_name,
                row_count=batch.row_count,
                stored_row_count=stored_row_count,
                issue_count=issue_count,
            )
        )
    return tuple(reviews)


def _require_one_master_batch(
    reviews: Iterable[MasterBatchReview], *, allowed_statuses: set[str]
) -> dict[str, MasterBatchReview]:
    by_source: dict[str, list[MasterBatchReview]] = {
        source_type: [] for source_type in MASTER_SOURCE_TYPES
    }
    for review in reviews:
        if review.status in allowed_statuses:
            by_source[review.source_type].append(review)

    selected: dict[str, MasterBatchReview] = {}
    problems: list[str] = []
    for source_type in MASTER_SOURCE_TYPES:
        candidates = by_source[source_type]
        if not candidates:
            problems.append(f"no eligible {source_type} batch")
        elif len(candidates) > 1:
            ids = ", ".join(str(candidate.import_batch_id) for candidate in candidates)
            problems.append(f"multiple eligible {source_type} batches ({ids})")
        else:
            selected[source_type] = candidates[0]
    if problems:
        raise MasterImportError(
            "Master batch selection is not unambiguous: " + "; ".join(problems) + "."
        )
    return selected


def _validate_clean_batch(review: MasterBatchReview) -> None:
    if review.issue_count:
        raise MasterImportError(
            f"{review.source_type} batch {review.import_batch_id} has "
            f"{review.issue_count} review issue(s)."
        )
    if review.row_count != review.stored_row_count:
        raise MasterImportError(
            f"{review.source_type} batch {review.import_batch_id} declares "
            f"{review.row_count} rows but stores {review.stored_row_count}."
        )


def approve_master_batches(session: Session, *, actor: AppUser) -> ApprovalSummary:
    reviews = review_master_batches(session)
    selected = _require_one_master_batch(
        reviews, allowed_statuses={"staged", "approved"}
    )
    correlation_id = uuid.uuid4()
    approved: list[uuid.UUID] = []
    already_approved: list[uuid.UUID] = []
    accepted_rows = 0

    for source_type in MASTER_SOURCE_TYPES:
        review = selected[source_type]
        _validate_clean_batch(review)
        batch = session.get(ImportBatch, review.import_batch_id)
        if batch is None:
            raise MasterImportError(f"Import batch {review.import_batch_id} disappeared.")

        invalid_status_count = session.scalar(
            select(func.count(ImportRow.import_row_id)).where(
                ImportRow.import_batch_id == batch.import_batch_id,
                ImportRow.status.not_in(("parsed", "accepted")),
            )
        ) or 0
        if invalid_status_count:
            raise MasterImportError(
                f"{source_type} batch {batch.import_batch_id} contains "
                f"{invalid_status_count} row(s) that are not parsed or accepted."
            )

        accepted_rows += review.row_count
        if batch.status == "approved":
            already_approved.append(batch.import_batch_id)
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
                action="import_batch_approved",
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
    return ApprovalSummary(
        approved_batch_ids=tuple(approved),
        already_approved_batch_ids=tuple(already_approved),
        accepted_row_count=accepted_rows,
    )


ITEM_FIELDS = (
    "item_name",
    "normalized_name",
    "description",
    "is_bought",
    "is_sold",
    "is_inventoried",
    "is_active",
    "excluded_from_item_view",
    "buy_unit_measure",
    "sell_unit_measure",
    "reorder_quantity",
    "minimum_level",
    "standard_cost",
)
CUSTOMER_FIELDS = (
    "myob_card_id",
    "display_name",
    "normalized_name",
    "card_status",
    "address_line_1",
    "city",
    "state",
    "postcode",
    "contact_name",
    "email",
    "phone",
    "terms_description",
    "price_level",
    "shipping_method",
    "is_active",
)
SUPPLIER_FIELDS = (
    "myob_card_id",
    "display_name",
    "normalized_name",
    "card_status",
    "contact_name",
    "email",
    "phone",
    "is_active",
)


def _candidate_mapping(candidate: object, fields: tuple[str, ...]) -> dict[str, Any]:
    return {field_name: getattr(candidate, field_name) for field_name in fields}


def _entity_mapping(entity: object, fields: tuple[str, ...]) -> dict[str, Any]:
    return {field_name: getattr(entity, field_name) for field_name in fields}


def _apply_mapping(entity: object, values: dict[str, Any]) -> None:
    for field_name, value in values.items():
        setattr(entity, field_name, value)


def _record_entity_event(
    session: Session,
    *,
    actor: AppUser,
    correlation_id: uuid.UUID,
    action: str,
    entity_type: str,
    entity_id: str,
    summary: str,
    before: dict[str, Any] | None,
    after: dict[str, Any],
) -> None:
    session.add(
        AuditEvent(
            actor_user_id=actor.user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            correlation_id=correlation_id,
            source="myob_import",
            summary=summary,
            before_json=_json(before) if before is not None else None,
            after_json=_json(after),
        )
    )


def _approved_batches(session: Session) -> dict[str, ImportBatch]:
    reviews = review_master_batches(session)
    selected = _require_one_master_batch(reviews, allowed_statuses={"approved"})
    batches: dict[str, ImportBatch] = {}
    for source_type, review in selected.items():
        _validate_clean_batch(review)
        batch = session.get(ImportBatch, review.import_batch_id)
        if batch is None:
            raise MasterImportError(f"Import batch {review.import_batch_id} disappeared.")
        accepted_count = session.scalar(
            select(func.count(ImportRow.import_row_id)).where(
                ImportRow.import_batch_id == batch.import_batch_id,
                ImportRow.status == "accepted",
            )
        ) or 0
        if accepted_count != batch.row_count:
            raise MasterImportError(
                f"{source_type} batch {batch.import_batch_id} has {accepted_count} accepted "
                f"rows but declares {batch.row_count}."
            )
        batches[source_type] = batch
    return batches


def _batch_rows(session: Session, batch: ImportBatch) -> list[ImportRow]:
    return list(
        session.scalars(
            select(ImportRow)
            .where(
                ImportRow.import_batch_id == batch.import_batch_id,
                ImportRow.status == "accepted",
            )
            .order_by(ImportRow.row_number)
        )
    )


def _promote_items(
    session: Session,
    *,
    batch: ImportBatch,
    actor: AppUser | None,
    correlation_id: uuid.UUID,
    commit: bool,
) -> ChangeCounts:
    existing = {item.item_number: item for item in session.scalars(select(Item))}
    seen: set[str] = set()
    created = updated = unchanged = 0

    for row in _batch_rows(session, batch):
        candidate: ItemMasterCandidate = map_item_master(_row_values(row))
        key = candidate.item_number
        if key in seen:
            raise MasterImportError(f"Duplicate item number {key!r} in batch {batch.import_batch_id}.")
        seen.add(key)
        after = _candidate_mapping(candidate, ITEM_FIELDS)
        entity = existing.get(key)
        if entity is None:
            created += 1
            if commit:
                entity = Item(item_id=uuid.uuid4(), item_number=key, **after)
                session.add(entity)
                existing[key] = entity
                assert actor is not None
                _record_entity_event(
                    session,
                    actor=actor,
                    correlation_id=correlation_id,
                    action="item_created_from_myob",
                    entity_type="item",
                    entity_id=str(entity.item_id),
                    summary=f"Created item {key} from approved MYOB batch.",
                    before=None,
                    after={"item_number": key, **after},
                )
            continue

        before = _entity_mapping(entity, ITEM_FIELDS)
        if before == after:
            unchanged += 1
            continue
        updated += 1
        if commit:
            _apply_mapping(entity, after)
            assert actor is not None
            _record_entity_event(
                session,
                actor=actor,
                correlation_id=correlation_id,
                action="item_updated_from_myob",
                entity_type="item",
                entity_id=str(entity.item_id),
                summary=f"Updated item {key} from approved MYOB batch.",
                before={"item_number": key, **before},
                after={"item_number": key, **after},
            )
    return ChangeCounts("item_master", created, updated, unchanged)


def _promote_customer_or_supplier(
    session: Session,
    *,
    batch: ImportBatch,
    actor: AppUser | None,
    correlation_id: uuid.UUID,
    commit: bool,
    model: type[CustomerAccount] | type[Supplier],
    mapper: Callable[[dict[str, str | None]], CustomerMasterCandidate | SupplierMasterCandidate],
    fields: tuple[str, ...],
    source_type: str,
    entity_type: str,
    id_field: str,
) -> ChangeCounts:
    entities = list(session.scalars(select(model)))
    by_record = {
        entity.myob_record_id: entity for entity in entities if entity.myob_record_id is not None
    }
    by_card = {
        entity.myob_card_id: entity for entity in entities if entity.myob_card_id is not None
    }
    seen_records: set[str] = set()
    seen_cards: dict[str, str] = {}
    created = updated = unchanged = 0

    for row in _batch_rows(session, batch):
        candidate = mapper(_row_values(row))
        record_id = candidate.myob_record_id
        if record_id is None:
            raise MasterImportError(
                f"{source_type} row {row.row_number} has no MYOB Record ID."
            )
        if record_id in seen_records:
            raise MasterImportError(
                f"Duplicate MYOB Record ID {record_id!r} in {source_type} batch."
            )
        seen_records.add(record_id)

        card_id = candidate.myob_card_id
        if card_id is not None:
            prior_record = seen_cards.get(card_id)
            if prior_record is not None and prior_record != record_id:
                raise MasterImportError(
                    f"MYOB Card ID {card_id!r} belongs to multiple records in {source_type}."
                )
            seen_cards[card_id] = record_id

        entity = by_record.get(record_id)
        card_owner = by_card.get(card_id) if card_id is not None else None
        if card_owner is not None and card_owner is not entity:
            raise MasterImportError(
                f"MYOB Card ID {card_id!r} is already attached to a different "
                f"{entity_type}; no automatic match was made."
            )

        after = _candidate_mapping(candidate, fields)
        if entity is None:
            created += 1
            if commit:
                entity = model(**{id_field: uuid.uuid4()}, myob_record_id=record_id, **after)
                if isinstance(entity, CustomerAccount):
                    entity.source_updated_at = utc_now()
                session.add(entity)
                by_record[record_id] = entity
                if card_id is not None:
                    by_card[card_id] = entity
                assert actor is not None
                _record_entity_event(
                    session,
                    actor=actor,
                    correlation_id=correlation_id,
                    action=f"{entity_type}_created_from_myob",
                    entity_type=entity_type,
                    entity_id=str(getattr(entity, id_field)),
                    summary=f"Created {entity_type} {record_id} from approved MYOB batch.",
                    before=None,
                    after={"myob_record_id": record_id, **after},
                )
            continue

        before = _entity_mapping(entity, fields)
        if before == after:
            unchanged += 1
            continue
        updated += 1
        if commit:
            _apply_mapping(entity, after)
            if isinstance(entity, CustomerAccount):
                entity.source_updated_at = utc_now()
            assert actor is not None
            _record_entity_event(
                session,
                actor=actor,
                correlation_id=correlation_id,
                action=f"{entity_type}_updated_from_myob",
                entity_type=entity_type,
                entity_id=str(getattr(entity, id_field)),
                summary=f"Updated {entity_type} {record_id} from approved MYOB batch.",
                before={"myob_record_id": record_id, **before},
                after={"myob_record_id": record_id, **after},
            )
    return ChangeCounts(source_type, created, updated, unchanged)


def promote_master_batches(
    session: Session,
    *,
    commit: bool,
    actor: AppUser | None = None,
) -> MasterPromotionSummary:
    if commit and actor is None:
        raise MasterImportError("An application user is required for committed promotion.")

    batches = _approved_batches(session)
    correlation_id = uuid.uuid4()
    changes = (
        _promote_customer_or_supplier(
            session,
            batch=batches["supplier_master"],
            actor=actor,
            correlation_id=correlation_id,
            commit=commit,
            model=Supplier,
            mapper=map_supplier_master,
            fields=SUPPLIER_FIELDS,
            source_type="supplier_master",
            entity_type="supplier",
            id_field="supplier_id",
        ),
        _promote_customer_or_supplier(
            session,
            batch=batches["customer_master"],
            actor=actor,
            correlation_id=correlation_id,
            commit=commit,
            model=CustomerAccount,
            mapper=map_customer_master,
            fields=CUSTOMER_FIELDS,
            source_type="customer_master",
            entity_type="customer_account",
            id_field="customer_account_id",
        ),
        _promote_items(
            session,
            batch=batches["item_master"],
            actor=actor,
            correlation_id=correlation_id,
            commit=commit,
        ),
    )

    committed_ids: list[uuid.UUID] = []
    if commit:
        now = utc_now()
        change_by_source = {change.source_type: change for change in changes}
        for source_type in MASTER_SOURCE_TYPES:
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
            change = change_by_source[source_type]
            assert actor is not None
            session.add(
                AuditEvent(
                    actor_user_id=actor.user_id,
                    action="import_batch_committed",
                    entity_type="import_batch",
                    entity_id=str(batch.import_batch_id),
                    correlation_id=correlation_id,
                    source="myob_import",
                    summary=(
                        f"Committed {source_type}: {change.created} created, "
                        f"{change.updated} updated, {change.unchanged} unchanged."
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

    return MasterPromotionSummary(
        mode="committed" if commit else "preview",
        changes=changes,
        committed_batch_ids=tuple(committed_ids),
    )
