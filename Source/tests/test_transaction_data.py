from __future__ import annotations

from decimal import Decimal

from windsor_widget.imports.transaction_data import map_purchase_line, map_sales_line


def test_sales_mapping_parses_money_dates_and_cover_order() -> None:
    candidate = map_sales_line(
        {
            "Record ID": "150",
            "Invoice No.": "24004115",
            "Co./Last Name": "Comfort Sleep Bedding Company",
            "Date": "8/08/2024",
            "Item Number": "MTS36TURQT6984",
            "Quantity": "6000.000000",
            "Price": "$.1000",
            "Discount": "0%",
            "Total": "$600.00",
            "Journal Memo": "Sale; Comfort Sleep Bedding Company - COVER ORDER",
            "Tax Amount": "$60.00",
            "Freight Amount": "$.00",
            "Freight Tax Amount": "$.00",
            "Amount Paid": "$.00",
        }
    )
    assert candidate.document_key == ("150", "24004115")
    assert candidate.quantity == Decimal("6000.000000")
    assert candidate.unit_price == Decimal(".1000")
    assert candidate.is_cover_order is True


def test_purchase_mapping_keeps_order_received_and_billed_quantities_separate() -> None:
    candidate = map_purchase_line(
        {
            "Record ID": "1463",
            "Purchase No.": "190420#",
            "Co./Last Name": "TLNT - Taiwan",
            "Date": "4/12/2018",
            "Item Number": "CF22KG",
            "Quantity": "5376.000000",
            "Price": "$12.5",
            "Discount": "0%",
            "Total": "$67,200",
            "Order": "5376.000000",
            "Received": "0",
            "Billed": "",
            "Tax Amount": "$0.00",
            "Freight Amount": "$0.00",
            "Freight Tax Amount": "$0.00",
            "Amount Paid": "$0.00",
        }
    )
    assert candidate.order_quantity == Decimal("5376.000000")
    assert candidate.received_quantity == Decimal("0")
    assert candidate.billed_quantity is None
