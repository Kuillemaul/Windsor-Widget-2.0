from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from windsor_widget.db.base import Base
from windsor_widget.db.models import ImportRow
from windsor_widget.imports import SOURCE_CONTRACTS, stage_myob_file
from windsor_widget.imports.contracts import SourceContract
from windsor_widget.imports.myob_text import parse_myob_text


def _three_column_contract() -> SourceContract:
    return SourceContract(
        source_type="repair_test",
        required_headers=frozenset({"Item Number", "Item Name", "Buy"}),
        natural_key_fields=("Item Number",),
        description="quote-repair fixture",
    )


def test_terminal_inch_quote_is_repaired(tmp_path: Path) -> None:
    source = tmp_path / "items.txt"
    source.write_text(
        'Item Number,Item Name,Buy\nYJ5206025,"BLADE, STEEL, CARBON, 7"",B\n',
        encoding="utf-8",
    )

    row = parse_myob_text(source, _three_column_contract()).rows[0]

    assert row.values["Item Name"] == 'BLADE, STEEL, CARBON, 7"'
    assert row.review_required is False
    assert row.repairs


def test_inner_inch_quote_is_repaired(tmp_path: Path) -> None:
    source = tmp_path / "items.txt"
    source.write_text(
        'Item Number,Item Name,Buy\nYJ5P79037,"BLADE, 10", TEFLON COATED",B\n',
        encoding="utf-8",
    )

    row = parse_myob_text(source, _three_column_contract()).rows[0]

    assert row.values["Item Name"] == 'BLADE, 10", TEFLON COATED'
    assert row.review_required is False


def test_double_wrapped_name_is_repaired(tmp_path: Path) -> None:
    contract = SourceContract(
        source_type="customer_repair_test",
        required_headers=frozenset({"Co./Last Name", "First Name", "Record ID"}),
        natural_key_fields=("Record ID",),
        description="customer quote-repair fixture",
    )
    source = tmp_path / "customers.txt"
    source.write_text(
        'Co./Last Name,First Name,Record ID\n""Wise, C E"",""Wise, C E"",5095\n',
        encoding="utf-8",
    )

    row = parse_myob_text(source, contract).rows[0]

    assert row.values["Co./Last Name"] == "Wise, C E"
    assert row.values["First Name"] == "Wise, C E"
    assert row.natural_key == "5095"
    assert row.review_required is False


def test_valid_multiline_description_is_preserved(tmp_path: Path) -> None:
    source = tmp_path / "sales.txt"
    source.write_text(
        "Report title\n"
        "Co./Last Name,Invoice No.,Date,Item Number,Quantity,Record ID,Description\n"
        'Example,INV1,20/07/2026,ABC,12,55,"Line one\nline two"\n',
        encoding="utf-8",
    )

    row = parse_myob_text(source, SOURCE_CONTRACTS["sales_transactions"]).rows[0]

    assert row.values["Description"] == "Line one\nline two"
    assert row.repairs == ()
    assert row.review_required is False


def test_unclosed_bad_row_does_not_swallow_next_row(tmp_path: Path) -> None:
    source = tmp_path / "items.txt"
    source.write_text(
        'Item Number,Item Name,Buy\nBAD,"Unclosed text,B\nGOOD,Normal,B\n',
        encoding="utf-8",
    )

    rows = parse_myob_text(source, _three_column_contract()).rows

    assert len(rows) == 2
    assert rows[0].review_required is True
    assert rows[0].issues[0].issue_code == "malformed_csv_record"
    assert rows[1].natural_key == "GOOD"
    assert rows[1].review_required is False


def test_structural_error_does_not_create_cascading_key_errors(tmp_path: Path) -> None:
    source = tmp_path / "items.txt"
    source.write_text(
        "Item Number,Item Name,Buy,Sell,Inventory\n"
        "ABC,Example,Yes,Yes,Yes,EXTRA\n",
        encoding="utf-8",
    )

    row = parse_myob_text(source, SOURCE_CONTRACTS["item_master"]).rows[0]

    assert [issue.issue_code for issue in row.issues] == ["column_count_mismatch"]
    assert row.natural_key is None


def test_staging_retains_original_record_and_repair_audit(tmp_path: Path) -> None:
    source = tmp_path / "items.txt"
    malformed_line = 'YJ5206025,"BLADE, STEEL, CARBON, 7"",B,S,I\n'
    source.write_text(
        "Item Number,Item Name,Buy,Sell,Inventory\n" + malformed_line,
        encoding="utf-8",
    )
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        summary = stage_myob_file(
            session,
            source,
            SOURCE_CONTRACTS["item_master"],
        )
        staged = session.scalar(
            select(ImportRow).where(ImportRow.import_batch_id == summary.import_batch_id)
        )

        assert summary.review_row_count == 0
        assert staged is not None
        assert staged.raw_text == malformed_line
        assert staged.raw_json is not None
        assert json.loads(staged.raw_json)["repairs"]
