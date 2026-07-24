"""Controlled preview and import of validated Yuchang roll/spool packing data."""

from __future__ import annotations

import json
import re
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from windsor_widget.db.models import AuditEvent, Item, ItemSupplier, Supplier
from windsor_widget.db.models.audit import utc_now
from windsor_widget.services.yuchang_packing_preview import (
    YuchangPackingPreviewRow,
    build_yuchang_packing_preview_row,
    clean_item_key,
    extract_yuchang_packing_rows,
    workbook_mapping_counts,
)

ALLOWED_ROLL_SPOOL_UNITS = frozenset({"roll", "rolls", "spool", "large spool"})
PACKING_SOURCE_UNKNOWN = "unknown"
PACKING_SOURCE_WORKBOOK = "supplier_workbook"
PACKING_SOURCE_USER = "user"
ZERO = Decimal("0")
WHOLE_TOLERANCE = Decimal("0.001")


@dataclass(frozen=True, slots=True)
class YuchangPackingProposal:
    item_id: uuid.UUID
    item_number: str
    item_name: str
    source_row: int
    supplier_description_raw: str
    supplier_size_raw: str
    supplier_colour_raw: str
    supplier_unit_type: str
    packing_quantity_per_unit_raw: str
    roll_spool_length_metres: Decimal
    packing_quantity_per_carton_raw: str
    metres_per_carton: Decimal
    supplier_units_per_carton: Decimal
    supplier_label_description_raw: str
    source_workbook: str
    source_worksheet: str

    def proposed_snapshot(self) -> dict[str, Any]:
        return {
            "supplier_description_raw": self.supplier_description_raw or None,
            "supplier_size_raw": self.supplier_size_raw or None,
            "supplier_colour_raw": self.supplier_colour_raw or None,
            "supplier_unit_type": self.supplier_unit_type or None,
            "packing_quantity_per_unit_raw": self.packing_quantity_per_unit_raw or None,
            "roll_spool_length_metres": self.roll_spool_length_metres,
            "packing_quantity_per_carton_raw": self.packing_quantity_per_carton_raw or None,
            "metres_per_carton": self.metres_per_carton,
            "supplier_units_per_carton": self.supplier_units_per_carton,
            "supplier_label_description_raw": self.supplier_label_description_raw or None,
            "packing_source": PACKING_SOURCE_WORKBOOK,
            "packing_source_workbook": self.source_workbook,
            "packing_source_worksheet": self.source_worksheet,
            "packing_source_row": self.source_row,
        }


@dataclass(slots=True)
class YuchangPackingAction:
    source_row: int
    item_number: str
    item_name: str
    supplier_unit_type: str
    action: str
    reason: str
    item_supplier_id: uuid.UUID | None
    current_match_status: str
    current_is_preferred: bool
    current_packing_source: str
    current_roll_spool_length_metres: Decimal | None
    proposed_roll_spool_length_metres: Decimal | None
    current_metres_per_carton: Decimal | None
    proposed_metres_per_carton: Decimal | None
    current_supplier_units_per_carton: Decimal | None
    proposed_supplier_units_per_carton: Decimal | None
    changed_fields: tuple[str, ...] = ()
    proposal: YuchangPackingProposal | None = field(default=None, repr=False)

    def as_csv_dict(self) -> dict[str, Any]:
        return {
            "source_row": self.source_row,
            "item_number": self.item_number,
            "item_name": self.item_name,
            "supplier_unit_type": self.supplier_unit_type,
            "action": self.action,
            "reason": self.reason,
            "item_supplier_id": str(self.item_supplier_id or ""),
            "current_match_status": self.current_match_status,
            "current_is_preferred": self.current_is_preferred,
            "current_packing_source": self.current_packing_source,
            "current_roll_spool_length_metres": _decimal_text(
                self.current_roll_spool_length_metres
            ),
            "proposed_roll_spool_length_metres": _decimal_text(
                self.proposed_roll_spool_length_metres
            ),
            "current_metres_per_carton": _decimal_text(
                self.current_metres_per_carton
            ),
            "proposed_metres_per_carton": _decimal_text(
                self.proposed_metres_per_carton
            ),
            "current_supplier_units_per_carton": _decimal_text(
                self.current_supplier_units_per_carton
            ),
            "proposed_supplier_units_per_carton": _decimal_text(
                self.proposed_supplier_units_per_carton
            ),
            "changed_fields": ", ".join(self.changed_fields),
        }


@dataclass(frozen=True, slots=True)
class YuchangPackingSyncSummary:
    workbook_rows: int
    creates: int
    updates: int
    unchanged: int
    held: int

    @classmethod
    def from_actions(
        cls,
        actions: Iterable[YuchangPackingAction],
        *,
        workbook_rows: int,
    ) -> "YuchangPackingSyncSummary":
        counts = Counter(action.action for action in actions)
        return cls(
            workbook_rows=workbook_rows,
            creates=counts.get("create", 0),
            updates=counts.get("update", 0),
            unchanged=counts.get("unchanged", 0),
            held=sum(
                count
                for action, count in counts.items()
                if action.startswith("held_")
            ),
        )


def _normalise_unit(value: object) -> str:
    return re.sub(r"[\s\u00A0]+", " ", str(value or "").strip()).casefold()


def _decimal(value: object) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not result.is_finite():
        return None
    return result


def _decimal_text(value: Decimal | None) -> str:
    if value is None:
        return ""
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _decimal_equal(left: object, right: object) -> bool:
    return _decimal(left) == _decimal(right)


def _near_whole(value: Decimal) -> bool:
    return abs(value - value.to_integral_value()) <= WHOLE_TOLERANCE


def proposal_from_preview(
    preview: YuchangPackingPreviewRow,
    *,
    item_id: uuid.UUID,
    workbook_path: str | Path,
    worksheet_name: str,
) -> tuple[YuchangPackingProposal | None, str]:
    unit_key = _normalise_unit(preview.supplier_unit_raw)
    if unit_key not in ALLOWED_ROLL_SPOOL_UNITS:
        return None, "Supplier unit is outside the approved roll/spool first-pass rule."
    if preview.workbook_mapping_count != 1:
        return None, (
            f"Item number is mapped to {preview.workbook_mapping_count} workbook rows."
        )
    if preview.widget_match_status != "matched":
        return None, "Workbook item number does not resolve to exactly one Widget item."

    roll_length = _decimal(preview.parsed_roll_or_spool_length_metres)
    metres_per_carton = _decimal(preview.parsed_metres_per_carton)
    units_per_carton = _decimal(preview.parsed_supplier_units_per_carton)
    if roll_length is None or roll_length <= ZERO:
        return None, "Roll/spool length is blank, invalid or non-positive."
    if metres_per_carton is None or metres_per_carton <= ZERO:
        return None, "Metres per carton is blank, invalid or non-positive."
    if units_per_carton is None or units_per_carton <= ZERO:
        return None, "Supplier units per carton is blank, invalid or non-positive."
    if not _near_whole(units_per_carton):
        return None, (
            "Supplier units per carton is not a whole number under the approved rule."
        )

    return (
        YuchangPackingProposal(
            item_id=item_id,
            item_number=preview.widget_item_number,
            item_name=preview.widget_item_name,
            source_row=int(preview.source_row),
            supplier_description_raw=preview.supplier_description_raw,
            supplier_size_raw=preview.size_raw,
            supplier_colour_raw=preview.colour_raw,
            supplier_unit_type=preview.supplier_unit_raw,
            packing_quantity_per_unit_raw=preview.quantity_per_supplier_unit_raw,
            roll_spool_length_metres=roll_length,
            packing_quantity_per_carton_raw=preview.quantity_per_carton_raw,
            metres_per_carton=metres_per_carton,
            supplier_units_per_carton=units_per_carton,
            supplier_label_description_raw=preview.label_description_raw,
            source_workbook=str(Path(workbook_path).resolve()),
            source_worksheet=str(worksheet_name),
        ),
        "",
    )


def _current_snapshot(link: ItemSupplier | None) -> dict[str, Any]:
    if link is None:
        return {}
    return {
        "supplier_description_raw": link.supplier_description_raw,
        "supplier_size_raw": link.supplier_size_raw,
        "supplier_colour_raw": link.supplier_colour_raw,
        "supplier_unit_type": link.supplier_unit_type,
        "packing_quantity_per_unit_raw": link.packing_quantity_per_unit_raw,
        "roll_spool_length_metres": link.roll_spool_length_metres,
        "packing_quantity_per_carton_raw": link.packing_quantity_per_carton_raw,
        "metres_per_carton": link.metres_per_carton,
        "supplier_units_per_carton": link.supplier_units_per_carton,
        "supplier_label_description_raw": link.supplier_label_description_raw,
        "packing_source": link.packing_source,
        "packing_source_workbook": link.packing_source_workbook,
        "packing_source_worksheet": link.packing_source_worksheet,
        "packing_source_row": link.packing_source_row,
    }


def _merged_proposed_snapshot(
    link: ItemSupplier | None,
    proposal: YuchangPackingProposal,
) -> dict[str, Any]:
    proposed = proposal.proposed_snapshot()
    current = _current_snapshot(link)
    for key in (
        "supplier_description_raw",
        "supplier_size_raw",
        "supplier_colour_raw",
        "supplier_unit_type",
        "packing_quantity_per_unit_raw",
        "packing_quantity_per_carton_raw",
        "supplier_label_description_raw",
    ):
        if proposed.get(key) in (None, "") and current.get(key) not in (None, ""):
            proposed[key] = current[key]
    return proposed


def classify_action(
    link: ItemSupplier | None,
    proposal: YuchangPackingProposal,
) -> tuple[str, str, tuple[str, ...]]:
    if link is None:
        return "create", "Create an approved non-preferred Yuchang supplier link.", ()

    if str(link.match_status or "").casefold() == "rejected":
        return (
            "held_rejected_link",
            "Existing user-rejected supplier link is preserved.",
            (),
        )
    if str(link.packing_source or PACKING_SOURCE_UNKNOWN).casefold() == PACKING_SOURCE_USER:
        return (
            "held_manual_values",
            "Existing manually maintained packing values are preserved.",
            (),
        )

    current = _current_snapshot(link)
    proposed = _merged_proposed_snapshot(link, proposal)
    changed: list[str] = []
    decimal_fields = {
        "roll_spool_length_metres",
        "metres_per_carton",
        "supplier_units_per_carton",
    }
    for key, proposed_value in proposed.items():
        current_value = current.get(key)
        if key in decimal_fields:
            different = not _decimal_equal(current_value, proposed_value)
        else:
            different = current_value != proposed_value
        if different:
            changed.append(key)

    if not changed:
        return "unchanged", "Current packing values already match the workbook.", ()
    return "update", "Update supplier-workbook packing values.", tuple(changed)


def build_yuchang_packing_actions(
    session: Session,
    *,
    supplier: Supplier,
    workbook_path: str | Path,
    worksheet_name: str = "Sheet1",
) -> tuple[tuple[YuchangPackingAction, ...], YuchangPackingSyncSummary]:
    source_rows = extract_yuchang_packing_rows(
        workbook_path,
        worksheet_name=worksheet_name,
    )
    mapping_counts = workbook_mapping_counts(source_rows)

    items_by_key: dict[str, list[Item]] = defaultdict(list)
    for item in session.scalars(select(Item).order_by(Item.item_number)):
        key = clean_item_key(item.item_number)
        if key:
            items_by_key[key].append(item)

    links_by_item_id = {
        link.item_id: link
        for link in session.scalars(
            select(ItemSupplier).where(
                ItemSupplier.supplier_id == supplier.supplier_id
            )
        )
    }

    actions: list[YuchangPackingAction] = []
    for source in source_rows:
        key = clean_item_key(source.item_number)
        if not key:
            continue
        matches = items_by_key.get(key, [])
        link = links_by_item_id.get(matches[0].item_id) if len(matches) == 1 else None
        preview = build_yuchang_packing_preview_row(
            source,
            mapping_count=mapping_counts.get(key, 0),
            widget_matches=[
                {
                    "item_id": item.item_id,
                    "item_number": item.item_number,
                    "item_name": item.item_name,
                }
                for item in matches
            ],
            supplier_link=(
                {
                    "match_status": link.match_status,
                    "supplier_item_number": link.supplier_item_number,
                }
                if link is not None
                else None
            ),
        )

        if len(matches) != 1:
            reason = (
                "Workbook item number is missing from Widget."
                if not matches
                else "Cleaned workbook item number matches multiple Widget items."
            )
            actions.append(
                YuchangPackingAction(
                    source_row=source.source_row,
                    item_number=source.item_number,
                    item_name="",
                    supplier_unit_type=source.supplier_unit,
                    action="held_item_match",
                    reason=reason,
                    item_supplier_id=None,
                    current_match_status="",
                    current_is_preferred=False,
                    current_packing_source="",
                    current_roll_spool_length_metres=None,
                    proposed_roll_spool_length_metres=None,
                    current_metres_per_carton=None,
                    proposed_metres_per_carton=None,
                    current_supplier_units_per_carton=None,
                    proposed_supplier_units_per_carton=None,
                )
            )
            continue

        item = matches[0]
        proposal, hold_reason = proposal_from_preview(
            preview,
            item_id=item.item_id,
            workbook_path=workbook_path,
            worksheet_name=worksheet_name,
        )
        if proposal is None:
            actions.append(
                YuchangPackingAction(
                    source_row=source.source_row,
                    item_number=item.item_number,
                    item_name=item.item_name,
                    supplier_unit_type=source.supplier_unit,
                    action="held_rule",
                    reason=hold_reason,
                    item_supplier_id=link.item_supplier_id if link else None,
                    current_match_status=link.match_status if link else "",
                    current_is_preferred=bool(link.is_preferred) if link else False,
                    current_packing_source=(
                        link.packing_source if link else PACKING_SOURCE_UNKNOWN
                    ),
                    current_roll_spool_length_metres=(
                        link.roll_spool_length_metres if link else None
                    ),
                    proposed_roll_spool_length_metres=None,
                    current_metres_per_carton=(
                        link.metres_per_carton if link else None
                    ),
                    proposed_metres_per_carton=None,
                    current_supplier_units_per_carton=(
                        link.supplier_units_per_carton if link else None
                    ),
                    proposed_supplier_units_per_carton=None,
                )
            )
            continue

        action, reason, changed_fields = classify_action(link, proposal)
        actions.append(
            YuchangPackingAction(
                source_row=source.source_row,
                item_number=item.item_number,
                item_name=item.item_name,
                supplier_unit_type=proposal.supplier_unit_type,
                action=action,
                reason=reason,
                item_supplier_id=link.item_supplier_id if link else None,
                current_match_status=link.match_status if link else "",
                current_is_preferred=bool(link.is_preferred) if link else False,
                current_packing_source=(
                    link.packing_source if link else PACKING_SOURCE_UNKNOWN
                ),
                current_roll_spool_length_metres=(
                    link.roll_spool_length_metres if link else None
                ),
                proposed_roll_spool_length_metres=(
                    proposal.roll_spool_length_metres
                ),
                current_metres_per_carton=(
                    link.metres_per_carton if link else None
                ),
                proposed_metres_per_carton=proposal.metres_per_carton,
                current_supplier_units_per_carton=(
                    link.supplier_units_per_carton if link else None
                ),
                proposed_supplier_units_per_carton=(
                    proposal.supplier_units_per_carton
                ),
                changed_fields=changed_fields,
                proposal=proposal,
            )
        )

    return (
        tuple(actions),
        YuchangPackingSyncSummary.from_actions(
            actions,
            workbook_rows=len(source_rows),
        ),
    )


def _json_safe_snapshot(values: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in values.items():
        if isinstance(value, Decimal):
            safe[key] = _decimal_text(value)
        elif isinstance(value, datetime):
            safe[key] = value.isoformat()
        else:
            safe[key] = value
    return safe


def _assign_proposal(
    link: ItemSupplier,
    proposal: YuchangPackingProposal,
    *,
    verified_at: datetime,
) -> None:
    values = _merged_proposed_snapshot(link, proposal)
    for key, value in values.items():
        setattr(link, key, value)
    link.packing_verified_at = verified_at


def apply_yuchang_packing_actions(
    session: Session,
    *,
    supplier: Supplier,
    actions: Iterable[YuchangPackingAction],
    actor_user_id: uuid.UUID,
) -> tuple[int, int]:
    correlation_id = uuid.uuid4()
    verified_at = utc_now()
    created = 0
    updated = 0

    for action in actions:
        if action.action not in {"create", "update"} or action.proposal is None:
            continue
        proposal = action.proposal
        if action.action == "create":
            link = ItemSupplier(
                item_id=proposal.item_id,
                supplier_id=supplier.supplier_id,
                supplier_item_number=None,
                is_preferred=False,
                match_status="approved",
                match_method="user",
                packing_source=PACKING_SOURCE_WORKBOOK,
            )
            session.add(link)
            session.flush()
            before = {}
            created += 1
        else:
            link = session.get(ItemSupplier, action.item_supplier_id)
            if link is None:
                raise LookupError(
                    f"ItemSupplier {action.item_supplier_id} disappeared before commit."
                )
            if str(link.match_status or "").casefold() == "rejected":
                raise RuntimeError(
                    f"Rejected supplier link changed during commit for {action.item_number}."
                )
            if str(link.packing_source or PACKING_SOURCE_UNKNOWN).casefold() == PACKING_SOURCE_USER:
                raise RuntimeError(
                    f"Manual packing values changed during commit for {action.item_number}."
                )
            before = _current_snapshot(link)
            updated += 1

        _assign_proposal(link, proposal, verified_at=verified_at)
        session.flush()
        after = _current_snapshot(link)
        after["packing_verified_at"] = link.packing_verified_at

        session.add(
            AuditEvent(
                actor_user_id=actor_user_id,
                action=(
                    "yuchang_packing_link_created"
                    if action.action == "create"
                    else "yuchang_packing_updated"
                ),
                entity_type="item_supplier",
                entity_id=str(link.item_supplier_id),
                correlation_id=correlation_id,
                source=PACKING_SOURCE_WORKBOOK,
                summary=(
                    f"Imported Yuchang roll/spool packing for {action.item_number} "
                    f"from {proposal.source_worksheet} row {action.source_row}."
                ),
                before_json=json.dumps(
                    _json_safe_snapshot(before),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                after_json=json.dumps(
                    _json_safe_snapshot(after),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            )
        )

    return created, updated
