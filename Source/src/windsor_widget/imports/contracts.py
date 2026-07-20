"""Declared contracts for the supplied MYOB text exports."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SourceContract:
    """A source file contract used before rows reach database staging."""

    source_type: str
    required_headers: frozenset[str]
    natural_key_fields: tuple[str, ...]
    description: str
    optional_natural_key_fields: frozenset[str] = frozenset()


SOURCE_CONTRACTS = {
    "item_master": SourceContract(
        source_type="item_master",
        required_headers=frozenset({"Item Number", "Item Name", "Buy", "Sell", "Inventory"}),
        natural_key_fields=("Item Number",),
        description="MYOB item master export",
    ),
    "customer_master": SourceContract(
        source_type="customer_master",
        required_headers=frozenset({"Co./Last Name", "Card ID", "Card Status", "Record ID"}),
        natural_key_fields=("Record ID",),
        description="MYOB customer card export",
    ),
    "supplier_master": SourceContract(
        source_type="supplier_master",
        required_headers=frozenset({"Co./Last Name", "Card ID", "Card Status", "Record ID"}),
        natural_key_fields=("Record ID",),
        description="MYOB supplier card export",
    ),
    "sales_transactions": SourceContract(
        source_type="sales_transactions",
        required_headers=frozenset(
            {"Co./Last Name", "Invoice No.", "Date", "Item Number", "Quantity", "Record ID"}
        ),
        natural_key_fields=("Record ID", "Invoice No.", "Item Number"),
        description="MYOB item sales and cover-order export",
        optional_natural_key_fields=frozenset({"Item Number"}),
    ),
    "cover_order_snapshot": SourceContract(
        source_type="cover_order_snapshot",
        required_headers=frozenset(
            {"Co./Last Name", "Invoice No.", "Date", "Item Number", "Quantity", "Record ID"}
        ),
        natural_key_fields=("Record ID", "Invoice No.", "Item Number"),
        description="MYOB open sales-order snapshot used to identify cover-order commitments",
        optional_natural_key_fields=frozenset({"Item Number"}),
    ),
    "purchase_transactions": SourceContract(
        source_type="purchase_transactions",
        required_headers=frozenset(
            {"Co./Last Name", "Purchase No.", "Date", "Item Number", "Quantity", "Record ID"}
        ),
        natural_key_fields=("Record ID", "Purchase No.", "Item Number"),
        description="MYOB item purchase or purchase-order export",
        optional_natural_key_fields=frozenset({"Item Number"}),
    ),
}
