"""Preview or apply inferred STOCKED policies from repeat purchase history and current SOH.

Default rule:
- active, bought, inventoried, visible item
- current policy is unknown
- current SOH > 0
- at least 2 separate positive purchase-bill receiving waves
- latest wave is within 24 months

Nearby bill dates are grouped into one receiving wave so split bills from the
same shipment do not falsely count as repeat purchasing.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import uuid
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select, true

from windsor_widget.config import load_settings
from windsor_widget.db.models import (
    AuditEvent,
    ImportBatch,
    InventorySnapshot,
    InventorySnapshotLine,
    Item,
    PurchaseDocument,
    PurchaseLine,
    Supplier,
)
from windsor_widget.db.models.audit import utc_now
from windsor_widget.db.session import create_database_engine, create_session_factory
from windsor_widget.imports.promotion import ensure_app_user
from windsor_widget.services.purchase_bill_rules import (
    purchase_bill_conditions,
    real_supplier_condition,
)

ZERO = Decimal("0")


def decimal_value(value: object) -> Decimal:
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def shift_month(value: date, offset: int) -> date:
    month_index = value.year * 12 + value.month - 1 + offset
    year, zero_month = divmod(month_index, 12)
    month = zero_month + 1
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    month_days = (next_month - date(year, month, 1)).days
    return date(year, month, min(value.day, month_days))


def receiving_waves(dates: list[date], gap_days: int) -> list[tuple[date, date]]:
    ordered = sorted(set(dates))
    if not ordered:
        return []
    waves: list[tuple[date, date]] = []
    start = end = ordered[0]
    for value in ordered[1:]:
        if (value - end).days <= gap_days:
            end = value
        else:
            waves.append((start, end))
            start = end = value
    waves.append((start, end))
    return waves


def median_interval(waves: list[tuple[date, date]]) -> int | None:
    if len(waves) < 2:
        return None
    intervals = [
        (waves[index][0] - waves[index - 1][0]).days
        for index in range(1, len(waves))
    ]
    return int(round(statistics.median(intervals)))


def progress(label: str, current: int, total: int) -> None:
    width = 30
    ratio = 1 if total <= 0 else min(max(current / total, 0), 1)
    filled = int(width * ratio)
    bar = "#" * filled + "-" * (width - filled)
    print(
        f"\r{label:<34} [{bar}] {int(ratio * 100):3d}%  {current:,}/{total:,}",
        end="",
        flush=True,
    )
    if current >= total:
        print()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--username")
    parser.add_argument("--display-name")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--minimum-waves", type=int, default=2)
    parser.add_argument("--recent-months", type=int, default=24)
    parser.add_argument("--wave-gap-days", type=int, default=10)
    parser.add_argument("--include-user-unknown", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.minimum_waves < 2:
        raise SystemExit("--minimum-waves must be at least 2")
    if args.recent_months < 1 or args.recent_months > 120:
        raise SystemExit("--recent-months must be between 1 and 120")
    if args.wave_gap_days < 0 or args.wave_gap_days > 60:
        raise SystemExit("--wave-gap-days must be between 0 and 60")
    if args.commit and (not args.username or not args.display_name):
        raise SystemExit("--username and --display-name are required with --commit")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = args.output or (
        Path("..") / "DEV" / "exports" / f"stocked_policy_inference_{timestamp}.csv"
    ).resolve()

    settings = load_settings(args.config)
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)

    try:
        with factory() as session:
            item_query = (
                select(Item, InventorySnapshotLine.on_hand)
                .select_from(Item)
                .join(InventorySnapshotLine, InventorySnapshotLine.item_id == Item.item_id)
                .join(
                    InventorySnapshot,
                    InventorySnapshot.inventory_snapshot_id
                    == InventorySnapshotLine.inventory_snapshot_id,
                )
                .where(
                    InventorySnapshot.is_current == true(),
                    Item.is_active == true(),
                    Item.is_bought == true(),
                    Item.is_inventoried == true(),
                    Item.excluded_from_item_view != true(),
                    Item.replenishment_policy == "unknown",
                    InventorySnapshotLine.on_hand > 0,
                )
                .order_by(Item.item_number)
            )
            if not args.include_user_unknown:
                item_query = item_query.where(Item.policy_source != "user")

            item_rows = session.execute(item_query).all()
            items = {item.item_id: item for item, _ in item_rows}
            on_hand = {item.item_id: decimal_value(value) for item, value in item_rows}
            print(f"Unknown-policy items with positive SOH: {len(items):,}")

            bill_rows = session.execute(
                select(
                    PurchaseLine.item_id,
                    PurchaseLine.transaction_date,
                    PurchaseLine.quantity,
                    PurchaseDocument.supplier_id,
                    Supplier.display_name,
                )
                .select_from(PurchaseLine)
                .join(
                    PurchaseDocument,
                    PurchaseDocument.purchase_document_id
                    == PurchaseLine.purchase_document_id,
                )
                .join(
                    ImportBatch,
                    ImportBatch.import_batch_id == PurchaseLine.last_import_batch_id,
                )
                .join(Supplier, Supplier.supplier_id == PurchaseDocument.supplier_id)
                .where(
                    PurchaseLine.item_id.is_not(None),
                    *purchase_bill_conditions(
                        as_of_date=args.as_of,
                        positive_quantity_only=True,
                    ),
                    real_supplier_condition(),
                )
                .order_by(
                    PurchaseLine.item_id,
                    PurchaseLine.transaction_date,
                    PurchaseDocument.purchase_no,
                    PurchaseLine.line_sequence,
                )
            ).all()

            dates_by_item: dict[uuid.UUID, list[date]] = defaultdict(list)
            quantity_by_item: dict[uuid.UUID, Decimal] = defaultdict(lambda: ZERO)
            suppliers_by_item: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
            latest_supplier: dict[uuid.UUID, tuple[date, str]] = {}

            total_rows = len(bill_rows)
            for index, (item_id, tx_date, quantity, supplier_id, supplier_name) in enumerate(
                bill_rows, start=1
            ):
                if item_id in items:
                    dates_by_item[item_id].append(tx_date)
                    quantity_by_item[item_id] += decimal_value(quantity)
                    suppliers_by_item[item_id].add(supplier_id)
                    previous = latest_supplier.get(item_id)
                    if previous is None or tx_date >= previous[0]:
                        latest_supplier[item_id] = (tx_date, supplier_name)
                if index == total_rows or index % 250 == 0:
                    progress("Reading purchase bills", index, total_rows)

            recent_cutoff = shift_month(args.as_of, -args.recent_months)
            candidates: list[dict[str, object]] = []
            item_total = len(items)

            for index, (item_id, item) in enumerate(items.items(), start=1):
                waves = receiving_waves(
                    dates_by_item.get(item_id, []),
                    args.wave_gap_days,
                )
                if len(waves) >= args.minimum_waves and waves[-1][1] >= recent_cutoff:
                    candidates.append(
                        {
                            "item_id": item_id,
                            "item_number": item.item_number,
                            "item_name": item.item_name,
                            "on_hand": on_hand[item_id],
                            "wave_count": len(waves),
                            "first_wave_date": waves[0][0],
                            "last_wave_date": waves[-1][1],
                            "median_interval_days": median_interval(waves),
                            "total_purchase_quantity": quantity_by_item[item_id],
                            "supplier_count": len(suppliers_by_item[item_id]),
                            "latest_supplier": latest_supplier.get(
                                item_id, (waves[-1][1], "Unknown supplier")
                            )[1],
                        }
                    )
                if index == item_total or index % 100 == 0:
                    progress("Evaluating candidates", index, item_total)

            candidates.sort(key=lambda row: str(row["item_number"]).casefold())
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("w", encoding="utf-8-sig", newline="") as handle:
                fieldnames = [
                    "item_number",
                    "item_name",
                    "on_hand",
                    "wave_count",
                    "first_wave_date",
                    "last_wave_date",
                    "median_interval_days",
                    "total_purchase_quantity",
                    "supplier_count",
                    "latest_supplier",
                    "proposed_policy",
                ]
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                for candidate in candidates:
                    writer.writerow(
                        {
                            **{key: candidate[key] for key in fieldnames if key != "proposed_policy"},
                            "proposed_policy": "stocked",
                        }
                    )

            print()
            print(f"Eligible items: {len(candidates):,}")
            print(f"CSV report:     {output}")
            for candidate in candidates[:20]:
                interval = candidate["median_interval_days"] or "n/a"
                print(
                    f"  {candidate['item_number']:<24} "
                    f"SOH {candidate['on_hand']:>12,.3f}  "
                    f"waves {candidate['wave_count']:>2}  "
                    f"median {interval!s:>5}d  "
                    f"last {candidate['last_wave_date']}"
                )
            if len(candidates) > 20:
                print(f"  ...and {len(candidates) - 20:,} more in the CSV.")

            if not args.commit:
                session.rollback()
                print("Preview only; no policies were changed.")
                return 0

            actor = ensure_app_user(
                session,
                username=args.username,
                display_name=args.display_name,
            )
            reviewed_at = utc_now()
            correlation_id = uuid.uuid4()
            changed = 0
            total = len(candidates)

            for index, candidate in enumerate(candidates, start=1):
                item = items[candidate["item_id"]]
                if item.replenishment_policy != "unknown":
                    continue

                before = {
                    "replenishment_policy": item.replenishment_policy,
                    "policy_source": item.policy_source,
                }
                item.replenishment_policy = "stocked"
                item.policy_source = "inferred"
                item.policy_reviewed_at = reviewed_at
                item.policy_reviewed_by_user_id = actor.user_id
                session.add(
                    AuditEvent(
                        actor_user_id=actor.user_id,
                        action="item.policy.inferred",
                        entity_type="item",
                        entity_id=str(item.item_id),
                        correlation_id=correlation_id,
                        source="script",
                        summary=(
                            f"{item.item_number} inferred as stocked: "
                            f"SOH {candidate['on_hand']:,.3f}; "
                            f"{candidate['wave_count']} receiving waves; "
                            f"last receipt {candidate['last_wave_date']}."
                        ),
                        before_json=json.dumps(before, sort_keys=True),
                        after_json=json.dumps(
                            {
                                "replenishment_policy": "stocked",
                                "policy_source": "inferred",
                                "rule": {
                                    "minimum_receiving_waves": args.minimum_waves,
                                    "recent_purchase_months": args.recent_months,
                                    "receiving_wave_gap_days": args.wave_gap_days,
                                    "requires_positive_soh": True,
                                },
                            },
                            sort_keys=True,
                        ),
                    )
                )
                changed += 1
                if index == total or index % 100 == 0:
                    progress("Applying policies", index, total)

            session.commit()
            print(f"Committed {changed:,} inferred STOCKED policies.")
            print(
                "Existing STOCKED, Made to Order, Run Out / Manual and "
                "user-preserved unknown policies were not overwritten."
            )
            return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
