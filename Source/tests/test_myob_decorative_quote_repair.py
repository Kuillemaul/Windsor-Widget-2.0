from __future__ import annotations

from pathlib import Path

from windsor_widget.imports.contracts import SOURCE_CONTRACTS
from windsor_widget.imports.myob_text import parse_myob_text


def test_item_description_with_decorative_brand_quotes_is_repaired(tmp_path: Path) -> None:
    source = tmp_path / "items.txt"
    source.write_text(
        "Item Number,Item Name,Buy,Sell,Inventory,Description\n"
        'WEBKEL25B,Jacquard Webbing,B,S,I,"KELMATT" POLYESTER WEBBING\n',
        encoding="utf-8",
    )

    row = parse_myob_text(source, SOURCE_CONTRACTS["item_master"]).rows[0]

    assert row.review_required is False
    assert row.values["Description"] == '"KELMATT" POLYESTER WEBBING'
    assert row.repairs == ("treated decorative quote pairs as literal text",)


def test_customer_address_with_decorative_quotes_is_repaired(tmp_path: Path) -> None:
    source = tmp_path / "customers.txt"
    source.write_text(
        "Co./Last Name,Card ID,Card Status,Record ID,Street\n"
        'Harden,C001,N,3835,"Yarra" East Street\n',
        encoding="utf-8",
    )

    row = parse_myob_text(source, SOURCE_CONTRACTS["customer_master"]).rows[0]

    assert row.review_required is False
    assert row.values["Street"] == '"Yarra" East Street'
    assert row.natural_key == "3835"


def test_decorative_quotes_do_not_break_valid_quoted_currency(tmp_path: Path) -> None:
    source = tmp_path / "sales.txt"
    source.write_text(
        "Co./Last Name,Invoice No.,Date,Item Number,Quantity,Record ID,Description,Amount\n"
        'Kelmatt,INV1,20/07/2026,WEBKEL32B,3000,4220,"KELMATT" POLYESTER WEBBING,"$1,560.00"\n',
        encoding="utf-8",
    )

    row = parse_myob_text(source, SOURCE_CONTRACTS["sales_transactions"]).rows[0]

    assert row.review_required is False
    assert row.values["Description"] == '"KELMATT" POLYESTER WEBBING'
    assert row.values["Amount"] == "$1,560.00"
    assert row.natural_key == "4220|INV1|WEBKEL32B"


def test_multiline_description_with_decorative_quotes_is_repaired(tmp_path: Path) -> None:
    source = tmp_path / "sales.txt"
    source.write_text(
        "Co./Last Name,Invoice No.,Date,Item Number,Quantity,Record ID,Description,Amount\n"
        'Kelmatt,INV1,20/07/2026,WEBKEL32B,3000,4220,"KELMATT" POLYESTER WEBBING\n'
        'CRL 230726KEL,"$1,560.00"\n',
        encoding="utf-8",
    )

    row = parse_myob_text(source, SOURCE_CONTRACTS["sales_transactions"]).rows[0]

    assert row.review_required is False
    assert row.values["Description"] == '"KELMATT" POLYESTER WEBBING\nCRL 230726KEL'
    assert row.values["Amount"] == "$1,560.00"


def test_unmatched_quote_without_unique_repair_remains_quarantined(tmp_path: Path) -> None:
    source = tmp_path / "items.txt"
    source.write_text(
        "Item Number,Item Name,Buy,Sell,Inventory\n"
        'ABC,"Unclosed description,B,S,I\n',
        encoding="utf-8",
    )

    row = parse_myob_text(source, SOURCE_CONTRACTS["item_master"]).rows[0]

    assert row.review_required is True
    assert row.issues[0].issue_code == "malformed_csv_record"
