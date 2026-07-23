"""Shared rules for genuine MYOB purchase-bill history."""

from __future__ import annotations

import os

from sqlalchemy import func, true

from windsor_widget.db.models import ImportBatch, PurchaseLine, Supplier

_DEFAULT_BILL_FILENAME = "ITEMPURbills.TXT"
_PSEUDO_SUPPLIER_NAME = "stock"


def configured_purchase_bill_filename() -> str:
    return (
        os.getenv("WINDSOR_WIDGET_PURCHASE_BILL_FILENAME", _DEFAULT_BILL_FILENAME)
        .strip()
        .casefold()
        or _DEFAULT_BILL_FILENAME.casefold()
    )


def purchase_bill_conditions(
    *,
    as_of_date=None,
    positive_quantity_only: bool = False,
):
    """Return SQL conditions that identify genuine bill lines.

    Purchase status B is necessary but not sufficient because MYOB also exports
    stock-adjustment bills. The caller should additionally join Supplier and use
    real_supplier_condition() when supplier identity matters.
    """

    conditions = [
        PurchaseLine.is_active == true(),
        func.upper(func.coalesce(PurchaseLine.purchase_status, "")) == "B",
        func.lower(ImportBatch.source_file_name).like(
            f"%{configured_purchase_bill_filename()}"
        ),
    ]
    if as_of_date is not None:
        conditions.append(PurchaseLine.transaction_date <= as_of_date)
    if positive_quantity_only:
        conditions.append(PurchaseLine.quantity > 0)
    return tuple(conditions)


def real_supplier_condition():
    """Exclude MYOB's STOCK pseudo-supplier used for stock adjustments."""

    return (
        func.lower(func.ltrim(func.rtrim(Supplier.display_name)))
        != _PSEUDO_SUPPLIER_NAME
    )
