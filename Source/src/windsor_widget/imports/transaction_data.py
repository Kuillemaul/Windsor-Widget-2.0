"""Deterministic mappings from reviewed MYOB rows into transaction candidates."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from windsor_widget.imports.normalization import (
    clean_text,
    is_cover_order,
    parse_date,
    parse_decimal,
)

SourceRow = Mapping[str, str | None]


class TransactionMappingError(ValueError):
    """Raised when a staged row cannot be mapped without guessing."""


@dataclass(frozen=True, slots=True)
class SalesLineCandidate:
    myob_customer_record_id: str
    invoice_no: str
    customer_name_snapshot: str
    transaction_date: date
    myob_item_number: str | None
    customer_po: str | None
    ship_via: str | None
    delivery_status: str | None
    description: str | None
    quantity: Decimal
    unit_price: Decimal
    discount_percent: Decimal | None
    line_total: Decimal
    inclusive: bool
    job: str | None
    comment: str | None
    journal_memo: str | None
    shipping_date: date | None
    tax_code: str | None
    tax_amount: Decimal | None
    freight_amount: Decimal | None
    freight_tax_code: str | None
    freight_tax_amount: Decimal | None
    sale_status: str | None
    currency_code: str | None
    exchange_rate: Decimal | None
    amount_paid: Decimal | None
    payment_method: str | None
    category: str | None
    location_id: str | None
    card_id_snapshot: str | None
    is_cover_order: bool

    @property
    def document_key(self) -> tuple[str, str]:
        return self.myob_customer_record_id, self.invoice_no


@dataclass(frozen=True, slots=True)
class PurchaseLineCandidate:
    myob_supplier_record_id: str
    purchase_no: str
    supplier_name_snapshot: str
    transaction_date: date
    myob_item_number: str | None
    supplier_invoice_no: str | None
    ship_via: str | None
    delivery_status: str | None
    description: str | None
    quantity: Decimal
    unit_price: Decimal
    discount_percent: Decimal | None
    line_total: Decimal
    inclusive: bool
    job: str | None
    comment: str | None
    journal_memo: str | None
    shipping_date: date | None
    tax_code: str | None
    tax_amount: Decimal | None
    freight_amount: Decimal | None
    freight_tax_code: str | None
    freight_tax_amount: Decimal | None
    purchase_status: str | None
    currency_code: str | None
    exchange_rate: Decimal | None
    amount_paid: Decimal | None
    order_quantity: Decimal | None
    received_quantity: Decimal | None
    billed_quantity: Decimal | None
    category: str | None
    location_id: str | None
    card_id_snapshot: str | None

    @property
    def document_key(self) -> tuple[str, str]:
        return self.myob_supplier_record_id, self.purchase_no


def _required_text(row: SourceRow, field_name: str) -> str:
    value = clean_text(row.get(field_name))
    if value is None:
        raise TransactionMappingError(f"Required MYOB field {field_name!r} is empty.")
    return value


def _required_date(row: SourceRow, field_name: str) -> date:
    supplied = _required_text(row, field_name)
    value = parse_date(supplied)
    if value is None:
        raise TransactionMappingError(
            f"Required MYOB field {field_name!r} is not a valid date: {supplied!r}."
        )
    return value


def _required_decimal(row: SourceRow, field_name: str) -> Decimal:
    supplied = _required_text(row, field_name)
    value = parse_decimal(supplied)
    if value is None:
        raise TransactionMappingError(
            f"Required MYOB field {field_name!r} is not numeric: {supplied!r}."
        )
    return value


def _optional_date(row: SourceRow, field_name: str) -> date | None:
    supplied = clean_text(row.get(field_name))
    if supplied is None:
        return None
    value = parse_date(supplied)
    if value is None:
        raise TransactionMappingError(
            f"MYOB field {field_name!r} is not a valid date: {supplied!r}."
        )
    return value


def _optional_decimal(row: SourceRow, field_name: str) -> Decimal | None:
    supplied = clean_text(row.get(field_name))
    if supplied is None:
        return None
    value = parse_decimal(supplied)
    if value is None:
        raise TransactionMappingError(
            f"MYOB field {field_name!r} is not numeric: {supplied!r}."
        )
    return value


def _inclusive(row: SourceRow) -> bool:
    return (clean_text(row.get("Inclusive")) or "").casefold() in {
        "x",
        "y",
        "yes",
        "true",
        "1",
    }


def map_sales_line(row: SourceRow) -> SalesLineCandidate:
    journal_memo = clean_text(row.get("Journal Memo"))
    return SalesLineCandidate(
        myob_customer_record_id=_required_text(row, "Record ID"),
        invoice_no=_required_text(row, "Invoice No."),
        customer_name_snapshot=_required_text(row, "Co./Last Name"),
        transaction_date=_required_date(row, "Date"),
        myob_item_number=clean_text(row.get("Item Number")),
        customer_po=clean_text(row.get("Customer PO")),
        ship_via=clean_text(row.get("Ship Via")),
        delivery_status=clean_text(row.get("Delivery Status")),
        description=clean_text(row.get("Description")),
        quantity=_required_decimal(row, "Quantity"),
        unit_price=_required_decimal(row, "Price"),
        discount_percent=_optional_decimal(row, "Discount"),
        line_total=_required_decimal(row, "Total"),
        inclusive=_inclusive(row),
        job=clean_text(row.get("Job")),
        comment=clean_text(row.get("Comment")),
        journal_memo=journal_memo,
        shipping_date=_optional_date(row, "Shipping Date"),
        tax_code=clean_text(row.get("Tax Code")),
        tax_amount=_optional_decimal(row, "Tax Amount"),
        freight_amount=_optional_decimal(row, "Freight Amount"),
        freight_tax_code=clean_text(row.get("Freight Tax Code")),
        freight_tax_amount=_optional_decimal(row, "Freight Tax Amount"),
        sale_status=clean_text(row.get("Sale Status")),
        currency_code=clean_text(row.get("Currency Code")),
        exchange_rate=_optional_decimal(row, "Exchange Rate"),
        amount_paid=_optional_decimal(row, "Amount Paid"),
        payment_method=clean_text(row.get("Payment Method")),
        category=clean_text(row.get("Category")),
        location_id=clean_text(row.get("Location ID")),
        card_id_snapshot=clean_text(row.get("Card ID")),
        is_cover_order=is_cover_order(journal_memo),
    )


def map_purchase_line(row: SourceRow) -> PurchaseLineCandidate:
    return PurchaseLineCandidate(
        myob_supplier_record_id=_required_text(row, "Record ID"),
        purchase_no=_required_text(row, "Purchase No."),
        supplier_name_snapshot=_required_text(row, "Co./Last Name"),
        transaction_date=_required_date(row, "Date"),
        myob_item_number=clean_text(row.get("Item Number")),
        supplier_invoice_no=clean_text(row.get("Supplier Invoice No.")),
        ship_via=clean_text(row.get("Ship Via")),
        delivery_status=clean_text(row.get("Delivery Status")),
        description=clean_text(row.get("Description")),
        quantity=_required_decimal(row, "Quantity"),
        unit_price=_required_decimal(row, "Price"),
        discount_percent=_optional_decimal(row, "Discount"),
        line_total=_required_decimal(row, "Total"),
        inclusive=_inclusive(row),
        job=clean_text(row.get("Job")),
        comment=clean_text(row.get("Comment")),
        journal_memo=clean_text(row.get("Journal Memo")),
        shipping_date=_optional_date(row, "Shipping Date"),
        tax_code=clean_text(row.get("Tax Code")),
        tax_amount=_optional_decimal(row, "Tax Amount"),
        freight_amount=_optional_decimal(row, "Freight Amount"),
        freight_tax_code=clean_text(row.get("Freight Tax Code")),
        freight_tax_amount=_optional_decimal(row, "Freight Tax Amount"),
        purchase_status=clean_text(row.get("Purchase Status")),
        currency_code=clean_text(row.get("Currency Code")),
        exchange_rate=_optional_decimal(row, "Exchange Rate"),
        amount_paid=_optional_decimal(row, "Amount Paid"),
        order_quantity=_optional_decimal(row, "Order"),
        received_quantity=_optional_decimal(row, "Received"),
        billed_quantity=_optional_decimal(row, "Billed"),
        category=clean_text(row.get("Category")),
        location_id=clean_text(row.get("Location ID")),
        card_id_snapshot=clean_text(row.get("Card ID")),
    )
