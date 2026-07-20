from __future__ import annotations

from pathlib import Path

from windsor_widget.imports.contracts import SOURCE_CONTRACTS
from windsor_widget.imports.myob_text import (
    inspect_myob_text,
    iter_myob_rows,
    parse_myob_text,
)
from windsor_widget.imports.normalization import is_control_item_number, is_cover_order


def test_parser_finds_header_preserves_embedded_newline_and_builds_key(tmp_path: Path) -> None:
    source = tmp_path / "sales.txt"
    source.write_text(
        "Report title\n"
        'Co./Last Name,Invoice No.,Date,Item Number,Quantity,Record ID,Journal Memo,Description\n'
        'Example,INV1,20/07/2026,ABC,12,55,Sale; Example - COVER ORDER,"Line one\nline two"\n',
        encoding="utf-8",
    )

    parsed = parse_myob_text(source, SOURCE_CONTRACTS["sales_transactions"])

    assert parsed.header_row_number == 2
    assert len(parsed.rows) == 1
    assert parsed.rows[0].natural_key == "55|INV1|ABC"
    assert parsed.rows[0].values["Description"] == "Line one\nline two"
    assert parsed.review_required is False


def test_parser_flags_shifted_row_instead_of_silently_accepting_it(tmp_path: Path) -> None:
    source = tmp_path / "items.txt"
    source.write_text(
        "Item Number,Item Name,Buy,Sell,Inventory\nABC,Example,Yes,Yes,Yes,EXTRA\n",
        encoding="utf-8",
    )

    parsed = parse_myob_text(source, SOURCE_CONTRACTS["item_master"])

    assert parsed.rows[0].review_required is True
    assert parsed.rows[0].issues[0].issue_code == "column_count_mismatch"


def test_parser_flags_partial_natural_key_for_review(tmp_path: Path) -> None:
    source = tmp_path / "sales.txt"
    source.write_text(
        "Co./Last Name,Invoice No.,Date,Item Number,Quantity,Record ID\n"
        "Example,INV1,20/07/2026,ABC,12,\n",
        encoding="utf-8",
    )

    parsed = parse_myob_text(source, SOURCE_CONTRACTS["sales_transactions"])

    assert parsed.rows[0].natural_key == "|INV1|ABC"
    assert parsed.rows[0].review_required is True
    assert [issue.issue_code for issue in parsed.rows[0].issues] == [
        "natural_key_incomplete"
    ]
    assert parsed.rows[0].issues[0].field_name == "Record ID"


def test_transaction_item_number_is_optional_for_document_comment_lines(
    tmp_path: Path,
) -> None:
    source = tmp_path / "sales.txt"
    source.write_text(
        "Co./Last Name,Invoice No.,Date,Item Number,Quantity,Record ID\n"
        "Example,INV1,20/07/2026,,,55\n",
        encoding="utf-8",
    )

    parsed = parse_myob_text(source, SOURCE_CONTRACTS["sales_transactions"])

    assert parsed.rows[0].natural_key == "55|INV1|"
    assert parsed.rows[0].review_required is False


def test_cover_order_rule_is_memo_based() -> None:
    assert is_cover_order("Sale; Comfort Sleep Bedding Company - COVER ORDER") is True
    assert is_cover_order("Sale; Comfort Sleep Bedding Company") is False


def test_slash_and_backslash_control_items_are_hidden_from_planning_only() -> None:
    assert is_control_item_number("/COMMENT") is True
    assert is_control_item_number("\\FC") is True
    assert is_control_item_number("MTYC70775CB") is False


def test_rows_can_be_inspected_and_streamed_without_materialising(tmp_path: Path) -> None:
    source = tmp_path / "sales.txt"
    source.write_text(
        "Report title\n"
        "Co./Last Name,Invoice No.,Date,Item Number,Quantity,Record ID\n"
        "First,INV1,20/07/2026,ABC,12,55\n"
        "Second,INV2,21/07/2026,XYZ,4,56\n",
        encoding="utf-8",
    )

    inspection = inspect_myob_text(source, SOURCE_CONTRACTS["sales_transactions"])
    rows = iter_myob_rows(
        source,
        SOURCE_CONTRACTS["sales_transactions"],
        inspection=inspection,
    )

    assert inspection.header_row_number == 2
    assert iter(rows) is rows
    assert [row.natural_key for row in rows] == ["55|INV1|ABC", "56|INV2|XYZ"]


def test_cp1252_export_is_detected_without_character_loss(tmp_path: Path) -> None:
    source = tmp_path / "customers.txt"
    source.write_bytes(
        (
            "Co./Last Name,Card ID,Record ID,Card Status\n"
            "Caf\u00e9 Bedding,C0001,100,N\n"
        ).encode("cp1252")
    )

    parsed = parse_myob_text(source, SOURCE_CONTRACTS["customer_master"])

    assert parsed.encoding == "cp1252"
    assert parsed.rows[0].values["Co./Last Name"] == "Caf\u00e9 Bedding"
