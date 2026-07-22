"""Item replenishment-policy review and visual tags.

Only invoiced sales, purchase history, the current inventory snapshot and
explicit COVER ORDER rows are used. Ordinary sales-order rows are deliberately
ignored until that source is cleaned and refreshed.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import islice

from sqlalchemy import func, or_, select, true
from sqlalchemy.orm import Session

from windsor_widget.db.models import (
    AuditEvent,
    CoverOrderDocument,
    CoverOrderLine,
    CoverOrderSnapshot,
    InventorySnapshot,
    InventorySnapshotLine,
    Item,
    PurchaseLine,
    SalesLine,
)
from windsor_widget.db.models.audit import utc_now

_ZERO = Decimal("0")
_ALLOWED_POLICIES = {"unknown", "stocked", "make_to_order", "manual"}


@dataclass(frozen=True, slots=True)
class ItemPolicyRow:
    item_id: uuid.UUID
    item_number: str
    item_name: str
    replenishment_policy: str
    policy_label: str
    tags: tuple[str, ...]
    on_hand: Decimal
    on_order: Decimal
    outstanding_cover: Decimal
    projected_pool: Decimal
    cover_gap: Decimal
    cover_surplus: Decimal
    invoiced_sale_lines: int
    purchase_lines: int
    matched_cycles: int
    match_ratio: Decimal
    review_confidence: str | None
    evidence: str


def _decimal(value: object) -> Decimal:
    if value is None:
        return _ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _chunks(values: tuple[uuid.UUID, ...], size: int = 800):
    iterator = iter(values)
    while chunk := tuple(islice(iterator, size)):
        yield chunk


def _policy_label(policy: str) -> str:
    return {
        "stocked": "Stocked",
        "make_to_order": "Made to Order",
        "manual": "Run Out / Manual",
        "unknown": "Unclassified",
    }.get(policy, "Unclassified")


def _cover_by_item(session: Session) -> dict[uuid.UUID, Decimal]:
    rows = session.execute(
        select(
            CoverOrderLine.item_id,
            func.coalesce(func.sum(CoverOrderLine.quantity), 0),
        )
        .select_from(CoverOrderLine)
        .join(
            CoverOrderDocument,
            CoverOrderDocument.cover_order_document_id
            == CoverOrderLine.cover_order_document_id,
        )
        .join(
            CoverOrderSnapshot,
            CoverOrderSnapshot.cover_order_snapshot_id
            == CoverOrderDocument.cover_order_snapshot_id,
        )
        .where(
            CoverOrderSnapshot.is_current == true(),
            CoverOrderLine.item_id.is_not(None),
            CoverOrderLine.is_cover_order == true(),
        )
        .group_by(CoverOrderLine.item_id)
    )
    return {item_id: _decimal(quantity) for item_id, quantity in rows}


def _line_counts(
    session: Session,
    item_ids: tuple[uuid.UUID, ...],
) -> tuple[dict[uuid.UUID, int], dict[uuid.UUID, int]]:
    sales_result: dict[uuid.UUID, int] = {}
    purchase_result: dict[uuid.UUID, int] = {}
    for chunk in _chunks(item_ids):
        for item_id, count in session.execute(
            select(SalesLine.item_id, func.count(SalesLine.sales_line_id))
            .where(
                SalesLine.item_id.in_(chunk),
                SalesLine.is_active == true(),
                func.upper(func.coalesce(SalesLine.sale_status, "")) == "I",
            )
            .group_by(SalesLine.item_id)
        ):
            sales_result[item_id] = int(count)
        for item_id, count in session.execute(
            select(PurchaseLine.item_id, func.count(PurchaseLine.purchase_line_id))
            .where(
                PurchaseLine.item_id.in_(chunk),
                PurchaseLine.is_active == true(),
            )
            .group_by(PurchaseLine.item_id)
        ):
            purchase_result[item_id] = int(count)
    return sales_result, purchase_result


def _candidate_cycle_evidence(
    session: Session,
    candidate_ids: tuple[uuid.UUID, ...],
    *,
    maximum_days: int = 45,
    quantity_tolerance: Decimal = Decimal("0.15"),
) -> dict[uuid.UUID, tuple[int, Decimal, str | None, str]]:
    """Pair nearby purchase and invoiced-sale lines for review evidence only."""
    sales_by_item: dict[uuid.UUID, list[tuple[date, Decimal]]] = defaultdict(list)
    purchases_by_item: dict[uuid.UUID, list[tuple[date, Decimal]]] = defaultdict(list)

    for chunk in _chunks(candidate_ids):
        for item_id, transaction_date, quantity in session.execute(
            select(SalesLine.item_id, SalesLine.transaction_date, SalesLine.quantity)
            .where(
                SalesLine.item_id.in_(chunk),
                SalesLine.is_active == true(),
                func.upper(func.coalesce(SalesLine.sale_status, "")) == "I",
                SalesLine.quantity > 0,
            )
            .order_by(SalesLine.item_id, SalesLine.transaction_date)
        ):
            sales_by_item[item_id].append((transaction_date, _decimal(quantity)))

        for item_id, transaction_date, quantity in session.execute(
            select(PurchaseLine.item_id, PurchaseLine.transaction_date, PurchaseLine.quantity)
            .where(
                PurchaseLine.item_id.in_(chunk),
                PurchaseLine.is_active == true(),
                PurchaseLine.quantity > 0,
            )
            .order_by(PurchaseLine.item_id, PurchaseLine.transaction_date)
        ):
            purchases_by_item[item_id].append((transaction_date, _decimal(quantity)))

    result: dict[uuid.UUID, tuple[int, Decimal, str | None, str]] = {}
    for item_id in candidate_ids:
        sales = sales_by_item.get(item_id, [])
        purchases = purchases_by_item.get(item_id, [])
        used_purchase_indexes: set[int] = set()
        matched = 0

        for sale_date, sale_quantity in sales:
            best_index: int | None = None
            best_days: int | None = None
            for index, (purchase_date, purchase_quantity) in enumerate(purchases):
                if index in used_purchase_indexes:
                    continue
                days = abs((sale_date - purchase_date).days)
                if days > maximum_days:
                    continue
                tolerance = max(
                    Decimal("1"),
                    max(abs(sale_quantity), abs(purchase_quantity)) * quantity_tolerance,
                )
                if abs(sale_quantity - purchase_quantity) > tolerance:
                    continue
                if best_days is None or days < best_days:
                    best_index = index
                    best_days = days
            if best_index is not None:
                used_purchase_indexes.add(best_index)
                matched += 1

        possible = min(len(sales), len(purchases))
        ratio = _ZERO if possible == 0 else Decimal(matched) / Decimal(possible)
        confidence: str | None = None
        if matched >= 3 and ratio >= Decimal("0.70"):
            confidence = "High"
        elif matched >= 2 and ratio >= Decimal("0.50"):
            confidence = "Medium"

        evidence = (
            f"{matched} nearby purchase/sale cycle(s) matched within {maximum_days} days; "
            f"{ratio * 100:.0f}% of possible cycles matched. SOH is currently zero. "
            "Review before changing policy."
            if matched
            else "SOH is currently zero, but there is not enough nearby purchase/sale "
            "evidence for an MTO recommendation."
        )
        result[item_id] = (matched, ratio, confidence, evidence)
    return result


def list_item_policy_rows(
    session: Session,
    *,
    query: str = "",
    tag: str = "",
    limit: int = 500,
) -> tuple[ItemPolicyRow, ...]:
    limit = max(1, min(int(limit), 2_000))
    normalized_tag = tag.strip().casefold()
    search_text = query.strip().casefold()

    snapshot = session.scalar(
        select(InventorySnapshot)
        .where(InventorySnapshot.is_current == true())
        .order_by(InventorySnapshot.captured_at.desc())
        .limit(1)
    )

    stmt = select(Item, InventorySnapshotLine).outerjoin(
        InventorySnapshotLine,
        (InventorySnapshotLine.item_id == Item.item_id)
        & (
            InventorySnapshotLine.inventory_snapshot_id
            == (snapshot.inventory_snapshot_id if snapshot is not None else uuid.uuid4())
        ),
    ).where(
        Item.is_active == true(),
        Item.excluded_from_item_view != true(),
        or_(
            Item.is_bought == true(),
            Item.is_sold == true(),
            Item.is_inventoried == true(),
        ),
    )

    if search_text:
        pattern = f"%{search_text}%"
        stmt = stmt.where(
            or_(
                func.lower(Item.item_number).like(pattern),
                func.lower(Item.item_name).like(pattern),
            )
        )

    if normalized_tag == "mto":
        stmt = stmt.where(Item.replenishment_policy == "make_to_order")
    elif normalized_tag in {"runout", "manual"}:
        stmt = stmt.where(Item.replenishment_policy == "manual")
    elif normalized_tag == "stocked":
        stmt = stmt.where(Item.replenishment_policy == "stocked")
    elif normalized_tag == "unclassified":
        stmt = stmt.where(Item.replenishment_policy == "unknown")

    # Four thousand item-master rows are small enough for a complete review pass.
    base_rows = list(
        session.execute(
            stmt.order_by(Item.item_number).limit(10_000)
        ).all()
    )
    cover_by_item = _cover_by_item(session)
    if normalized_tag == "cover":
        base_rows = [
            pair for pair in base_rows if cover_by_item.get(pair[0].item_id, _ZERO) > 0
        ]

    item_ids = tuple(item.item_id for item, _ in base_rows)
    sale_counts, purchase_counts = _line_counts(session, item_ids)
    candidate_ids = tuple(
        item.item_id
        for item, inventory in base_rows
        if item.replenishment_policy == "unknown"
        and inventory is not None
        and _decimal(inventory.on_hand) == 0
        and sale_counts.get(item.item_id, 0) >= 2
        and purchase_counts.get(item.item_id, 0) >= 2
    )
    evidence_by_item = _candidate_cycle_evidence(session, candidate_ids)

    result: list[ItemPolicyRow] = []
    for item, inventory in base_rows:
        on_hand = _decimal(inventory.on_hand if inventory is not None else 0)
        on_order = _decimal(inventory.on_order if inventory is not None else 0)
        cover = cover_by_item.get(item.item_id, _ZERO)
        pool = on_hand + on_order
        cover_gap = max(_ZERO, cover - pool)
        cover_surplus = max(_ZERO, pool - cover) if cover > 0 else _ZERO
        matched, ratio, confidence, evidence = evidence_by_item.get(
            item.item_id,
            (0, _ZERO, None, ""),
        )

        policy = item.replenishment_policy or "unknown"
        tags: list[str] = []
        if cover > 0:
            tags.append("COVER")
        if policy == "make_to_order":
            tags.append("MTO")
        elif policy == "manual":
            tags.append("RUN OUT")
        elif policy == "stocked":
            tags.append("STOCKED")
        elif confidence is not None:
            tags.append("REVIEW")

        if normalized_tag == "review" and confidence is None:
            continue

        result.append(
            ItemPolicyRow(
                item_id=item.item_id,
                item_number=item.item_number,
                item_name=item.item_name,
                replenishment_policy=policy,
                policy_label=_policy_label(policy),
                tags=tuple(tags),
                on_hand=on_hand,
                on_order=on_order,
                outstanding_cover=cover,
                projected_pool=pool,
                cover_gap=cover_gap,
                cover_surplus=cover_surplus,
                invoiced_sale_lines=sale_counts.get(item.item_id, 0),
                purchase_lines=purchase_counts.get(item.item_id, 0),
                matched_cycles=matched,
                match_ratio=ratio,
                review_confidence=confidence,
                evidence=evidence,
            )
        )
        if len(result) >= limit:
            break

    return tuple(result)


def set_item_policy(
    session: Session,
    *,
    item_id: uuid.UUID,
    policy: str,
    actor_user_id: uuid.UUID,
) -> Item:
    normalized = policy.strip().casefold()
    if normalized not in _ALLOWED_POLICIES:
        raise ValueError(f"Unsupported replenishment policy: {policy!r}")

    item = session.get(Item, item_id)
    if item is None:
        raise LookupError(f"No item exists for {item_id}.")

    old_policy = item.replenishment_policy or "unknown"
    item.replenishment_policy = normalized
    item.policy_source = "user"
    item.policy_reviewed_at = utc_now()
    item.policy_reviewed_by_user_id = actor_user_id
    session.add(
        AuditEvent(
            actor_user_id=actor_user_id,
            action="item.policy.updated",
            entity_type="item",
            entity_id=str(item.item_id),
            source="web",
            summary=(
                f"{item.item_number} purchasing policy changed from "
                f"{old_policy} to {normalized}."
            ),
        )
    )
    session.flush()
    return item
