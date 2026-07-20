"""Deterministic mappings from reviewed MYOB rows into master-data candidates."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from windsor_widget.imports.normalization import (
    clean_text,
    is_control_item_number,
    normalize_name,
    parse_decimal,
)

SourceRow = Mapping[str, str | None]


@dataclass(frozen=True, slots=True)
class ItemMasterCandidate:
    item_number: str
    item_name: str
    normalized_name: str
    description: str | None
    is_bought: bool
    is_sold: bool
    is_inventoried: bool
    is_active: bool
    excluded_from_item_view: bool
    buy_unit_measure: str | None
    sell_unit_measure: str | None
    reorder_quantity: Decimal | None
    minimum_level: Decimal | None
    standard_cost: Decimal | None
    primary_supplier_name: str | None
    supplier_item_number: str | None


@dataclass(frozen=True, slots=True)
class CustomerMasterCandidate:
    myob_record_id: str | None
    myob_card_id: str | None
    display_name: str
    normalized_name: str
    card_status: str | None
    address_line_1: str | None
    city: str | None
    state: str | None
    postcode: str | None
    contact_name: str | None
    email: str | None
    phone: str | None
    terms_description: str | None
    price_level: str | None
    shipping_method: str | None
    is_active: bool


@dataclass(frozen=True, slots=True)
class SupplierMasterCandidate:
    myob_record_id: str | None
    myob_card_id: str | None
    display_name: str
    normalized_name: str
    card_status: str | None
    contact_name: str | None
    email: str | None
    phone: str | None
    is_active: bool


def _required(row: SourceRow, field_name: str) -> str:
    value = clean_text(row.get(field_name))
    if value is None:
        raise ValueError(f"Required MYOB field {field_name!r} is empty")
    return value


def _marker_is(row: SourceRow, field_name: str, marker: str) -> bool:
    value = clean_text(row.get(field_name))
    if value is None:
        return False
    return value.casefold() in {marker.casefold(), "y", "yes", "true", "1"}


def _card_is_active(card_status: str | None) -> bool:
    """MYOB exports N for active cards and Y for inactive cards."""

    return (clean_text(card_status) or "N").casefold() != "y"


def map_item_master(row: SourceRow) -> ItemMasterCandidate:
    item_number = _required(row, "Item Number")
    item_name = clean_text(row.get("Item Name"))
    if item_name is None:
        if not is_control_item_number(item_number):
            raise ValueError("Required MYOB field 'Item Name' is empty")
        # MYOB permits slash-prefixed comment/control codes without a display name.
        # Retain them for invoice evidence while keeping them out of planning views.
        item_name = item_number
    inactive = _marker_is(row, "Inactive Item", "Y")
    return ItemMasterCandidate(
        item_number=item_number,
        item_name=item_name,
        normalized_name=normalize_name(item_name),
        description=clean_text(row.get("Description")),
        is_bought=_marker_is(row, "Buy", "B"),
        is_sold=_marker_is(row, "Sell", "S"),
        is_inventoried=_marker_is(row, "Inventory", "I"),
        is_active=not inactive,
        excluded_from_item_view=is_control_item_number(item_number),
        buy_unit_measure=clean_text(row.get("Buy Unit Measure")),
        sell_unit_measure=clean_text(row.get("Sell Unit Measure")),
        reorder_quantity=parse_decimal(row.get("Reorder Quantity")),
        minimum_level=parse_decimal(row.get("Minimum Level")),
        standard_cost=parse_decimal(row.get("Standard Cost")),
        primary_supplier_name=clean_text(row.get("Primary Supplier")),
        supplier_item_number=clean_text(row.get("Supplier Item Number")),
    )


def map_customer_master(row: SourceRow) -> CustomerMasterCandidate:
    display_name = _required(row, "Co./Last Name")
    card_status = clean_text(row.get("Card Status"))
    return CustomerMasterCandidate(
        myob_record_id=clean_text(row.get("Record ID")),
        myob_card_id=clean_text(row.get("Card ID")),
        display_name=display_name,
        normalized_name=normalize_name(display_name),
        card_status=card_status,
        address_line_1=clean_text(row.get("Addr 1 - Line 1")),
        city=clean_text(row.get("Addr 1 - City")),
        state=clean_text(row.get("Addr 1 - State")),
        postcode=clean_text(row.get("Addr 1 - Postcode")),
        contact_name=clean_text(row.get("Addr 1 - Contact Name")),
        email=clean_text(row.get("Addr 1 - Email")),
        phone=clean_text(row.get("Addr 1 - Phone No. 1")),
        terms_description=clean_text(row.get("Terms - Payment is Due")),
        price_level=clean_text(row.get("Price Level")),
        shipping_method=clean_text(row.get("Shipping Method")),
        is_active=_card_is_active(card_status),
    )


def map_supplier_master(row: SourceRow) -> SupplierMasterCandidate:
    display_name = _required(row, "Co./Last Name")
    card_status = clean_text(row.get("Card Status"))
    return SupplierMasterCandidate(
        myob_record_id=clean_text(row.get("Record ID")),
        myob_card_id=clean_text(row.get("Card ID")),
        display_name=display_name,
        normalized_name=normalize_name(display_name),
        card_status=card_status,
        contact_name=clean_text(row.get("Addr 1 - Contact Name")),
        email=clean_text(row.get("Addr 1 - Email")),
        phone=clean_text(row.get("Addr 1 - Phone No. 1")),
        is_active=_card_is_active(card_status),
    )
