"""Build ItemSupplier history and preferred suppliers from MYOB bills."""

from __future__ import annotations

from collections.abc import Callable
import json
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from windsor_widget.db.models import (
    AppUser,
    AuditEvent,
    ImportBatch,
    ItemSupplier,
    PurchaseDocument,
    PurchaseLine,
    Supplier,
)
from windsor_widget.services.purchase_bill_rules import (
    purchase_bill_conditions,
    real_supplier_condition,
)


@dataclass(frozen=True, slots=True)
class SupplierBillLinkSummary:
    bill_lines_considered: int
    purchased_items: int
    item_supplier_pairs: int
    links_created: int
    links_updated: int
    preferred_changed: int
    manual_preferred_preserved: int
    user_rejections_preserved: int


@dataclass(frozen=True, slots=True)
class _BillEvidence:
    item_id: uuid.UUID
    supplier_id: uuid.UUID
    supplier_name: str
    transaction_date: date
    purchase_no: str
    line_sequence: int
    unit_price: Decimal | None
    currency_code: str | None


def _evidence_rows(session: Session) -> tuple[_BillEvidence, ...]:
    statement = (
        select(
            PurchaseLine.item_id,
            PurchaseDocument.supplier_id,
            Supplier.display_name,
            PurchaseLine.transaction_date,
            PurchaseDocument.purchase_no,
            PurchaseLine.line_sequence,
            PurchaseLine.unit_price,
            PurchaseLine.currency_code,
        )
        .select_from(PurchaseLine)
        .join(
            PurchaseDocument,
            PurchaseDocument.purchase_document_id
            == PurchaseLine.purchase_document_id,
        )
        .join(
            Supplier,
            Supplier.supplier_id == PurchaseDocument.supplier_id,
        )
        .join(
            ImportBatch,
            ImportBatch.import_batch_id == PurchaseLine.last_import_batch_id,
        )
        .where(
            PurchaseLine.item_id.is_not(None),
            *purchase_bill_conditions(positive_quantity_only=True),
            real_supplier_condition(),
        )
        .order_by(
            PurchaseLine.item_id,
            PurchaseLine.transaction_date.desc(),
            PurchaseDocument.purchase_no.desc(),
            PurchaseLine.line_sequence.desc(),
            PurchaseLine.purchase_line_id.desc(),
        )
    )

    return tuple(
        _BillEvidence(
            item_id=row[0],
            supplier_id=row[1],
            supplier_name=row[2],
            transaction_date=row[3],
            purchase_no=row[4],
            line_sequence=int(row[5] or 0),
            unit_price=row[6],
            currency_code=row[7],
        )
        for row in session.execute(statement)
    )


def sync_supplier_links_from_bills(
    session: Session,
    *,
    commit: bool,
    actor: AppUser | None = None,
    preserve_manual_preferred: bool = True,
    progress: Callable[[str, int, int], None] | None = None,
) -> SupplierBillLinkSummary:
    """Create supplier links and infer the preferred supplier from the latest bill.

    Every real supplier-item pair appearing on a positive-quantity bill receives
    an ItemSupplier link. The supplier on the latest bill becomes preferred,
    unless a user-selected preferred supplier already exists.

    Explicit user rejections are never silently reversed.
    """

    if commit and actor is None:
        raise ValueError("An application user is required for committed synchronisation.")

    if progress is not None:
        progress("Reading purchase-bill history", 0, 1)

    evidence = _evidence_rows(session)

    if progress is not None:
        progress("Reading purchase-bill history", 1, 1)

    latest_by_pair: dict[tuple[uuid.UUID, uuid.UUID], _BillEvidence] = {}
    latest_by_item: dict[uuid.UUID, _BillEvidence] = {}
    for row in evidence:
        pair = (row.item_id, row.supplier_id)
        latest_by_pair.setdefault(pair, row)
        latest_by_item.setdefault(row.item_id, row)

    item_ids = set(latest_by_item)

    if progress is not None:
        progress("Loading existing supplier links", 0, 1)

    # Load the relatively small supplier-link table directly. This avoids
    # SQL Server's 2,100-parameter limit when thousands of item UUIDs exist.
    existing_links = (
        tuple(session.scalars(select(ItemSupplier)))
        if item_ids
        else ()
    )

    if progress is not None:
        progress("Loading existing supplier links", 1, 1)
    existing_by_pair = {
        (link.item_id, link.supplier_id): link for link in existing_links
    }

    manual_preferred_by_item: dict[uuid.UUID, ItemSupplier] = {}
    if preserve_manual_preferred:
        for link in existing_links:
            if (
                link.is_preferred
                and link.match_method == "user"
                and link.match_status != "rejected"
            ):
                manual_preferred_by_item.setdefault(link.item_id, link)

    links_created = 0
    links_updated = 0
    preferred_changed = 0
    manual_preferred_preserved = 0
    user_rejections_preserved = 0

    pair_total = len(latest_by_pair)
    for pair_index, (pair, latest) in enumerate(
        latest_by_pair.items(),
        start=1,
    ):
        if progress is not None and (
            pair_index == 1
            or pair_index == pair_total
            or pair_index % 100 == 0
        ):
            progress(
                "Synchronising supplier-item links",
                pair_index,
                pair_total,
            )

        link = existing_by_pair.get(pair)
        if link is not None and (
            link.match_method == "user" and link.match_status == "rejected"
        ):
            user_rejections_preserved += 1
            continue

        if link is None:
            links_created += 1
            if not commit:
                continue
            link = ItemSupplier(
                item_id=latest.item_id,
                supplier_id=latest.supplier_id,
                match_status="approved",
                match_method="recent_purchase",
                is_preferred=False,
            )
            session.add(link)
            existing_by_pair[pair] = link
        else:
            changed = any(
                (
                    link.last_purchase_date != latest.transaction_date,
                    link.last_purchase_price != latest.unit_price,
                    link.last_purchase_currency != latest.currency_code,
                    link.match_status == "rejected",
                )
            )
            if changed:
                links_updated += 1

        if commit:
            if link.match_status != "rejected":
                link.match_status = "approved"
            if not link.match_method:
                link.match_method = "recent_purchase"
            link.last_purchase_date = latest.transaction_date
            link.last_purchase_price = latest.unit_price
            link.last_purchase_currency = latest.currency_code

    preferred_total = len(latest_by_item)
    for preferred_index, (item_id, latest) in enumerate(
        latest_by_item.items(),
        start=1,
    ):
        if progress is not None and (
            preferred_index == 1
            or preferred_index == preferred_total
            or preferred_index % 100 == 0
        ):
            progress(
                "Selecting preferred suppliers",
                preferred_index,
                preferred_total,
            )

        manual = manual_preferred_by_item.get(item_id)
        if manual is not None:
            manual_preferred_preserved += 1
            continue

        target_supplier_id = latest.supplier_id
        for pair, link in tuple(existing_by_pair.items()):
            if pair[0] != item_id:
                continue
            if link.match_status == "rejected":
                continue
            desired = pair[1] == target_supplier_id
            if bool(link.is_preferred) != desired:
                preferred_changed += 1
                if commit:
                    link.is_preferred = desired

    summary = SupplierBillLinkSummary(
        bill_lines_considered=len(evidence),
        purchased_items=len(latest_by_item),
        item_supplier_pairs=len(latest_by_pair),
        links_created=links_created,
        links_updated=links_updated,
        preferred_changed=preferred_changed,
        manual_preferred_preserved=manual_preferred_preserved,
        user_rejections_preserved=user_rejections_preserved,
    )

    if commit:
        session.flush()
        assert actor is not None
        session.add(
            AuditEvent(
                actor_user_id=actor.user_id,
                action="supplier.bill_history.links.synchronised",
                entity_type="item_supplier",
                entity_id="bulk",
                source="maintenance",
                summary=(
                    "Synchronised supplier links and preferred suppliers from "
                    "ITEMPURbills.TXT."
                ),
                after_json=json.dumps(
                    {
                        "bill_lines_considered": summary.bill_lines_considered,
                        "purchased_items": summary.purchased_items,
                        "item_supplier_pairs": summary.item_supplier_pairs,
                        "links_created": summary.links_created,
                        "links_updated": summary.links_updated,
                        "preferred_changed": summary.preferred_changed,
                        "manual_preferred_preserved": (
                            summary.manual_preferred_preserved
                        ),
                        "user_rejections_preserved": (
                            summary.user_rejections_preserved
                        ),
                    },
                    sort_keys=True,
                ),
            )
        )
        session.flush()

    return summary
