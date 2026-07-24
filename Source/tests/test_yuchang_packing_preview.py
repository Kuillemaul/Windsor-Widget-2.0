from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from windsor_widget.services.yuchang_packing_preview import (
    build_yuchang_packing_preview_row,
    clean_item_key,
    extract_yuchang_packing_rows,
    workbook_mapping_counts,
)


def create_workbook(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    sheet["A12"] = "Our Number"
    sheet["B12"] = "Item"
    sheet["E12"] = "Roll /"
    sheet["F12"] = "Mt /"
    sheet["H12"] = "Mt per"
    sheet["L12"] = "Qty"
    sheet["E13"] = "Spool"
    sheet["F13"] = "Unit"
    sheet["H13"] = "Carton"

    sheet["A15"] = "ITEM-A"
    sheet["B15"] = "Poly Tape"
    sheet["C15"] = "22 mm"
    sheet["D15"] = "Black"
    sheet["E15"] = "Roll"
    sheet["F15"] = 100
    sheet["G15"] = "TAPE 22"
    sheet["H15"] = 3000
    sheet["I15"] = 9000
    sheet["K15"] = 0.02

    sheet["A16"] = "ITEM-A"
    sheet["B16"] = "Poly Tape"
    sheet["C16"] = "22 mm"
    sheet["D16"] = "Blue"
    sheet["E16"] = "Roll"
    sheet["F16"] = 100
    sheet["G16"] = "TAPE 22"
    sheet["H16"] = 3000

    sheet["A17"] = "ITEM-B"
    sheet["B17"] = "Buckle"
    sheet["C17"] = "50 mm"
    sheet["D17"] = "Silver"
    sheet["E17"] = "piece"
    sheet["F17"] = 100
    sheet["G17"] = "BUCKLE"
    sheet["H17"] = 500

    sheet["B20"] = "METHOD OF TRANSPORT"
    sheet["B25"] = "ORDER TOTAL"
    workbook.save(path)


def test_extracts_packing_rows_and_stops_before_footer(tmp_path: Path) -> None:
    path = tmp_path / "yu.xlsx"
    create_workbook(path)

    rows = extract_yuchang_packing_rows(path)

    assert len(rows) == 3
    assert rows[0].item_number == "ITEM-A"
    assert rows[0].quantity_per_supplier_unit_raw == "100"
    assert rows[0].quantity_per_carton_raw == "3000"
    assert rows[0].quantity_per_pallet_raw == "9000"
    assert rows[-1].source_row == 17


def test_preview_preserves_raw_values_and_derives_pack_quantities(tmp_path: Path) -> None:
    path = tmp_path / "yu.xlsx"
    create_workbook(path)
    rows = extract_yuchang_packing_rows(path)
    counts = workbook_mapping_counts(rows)

    preview = build_yuchang_packing_preview_row(
        rows[2],
        mapping_count=counts[clean_item_key("ITEM-B")],
        widget_matches=[{"item_number": "ITEM-B", "item_name": "Buckle"}],
        supplier_link={"match_status": "approved", "supplier_item_number": "YU-B"},
    )

    assert preview.preview_status == "ready"
    assert preview.inferred_measure == "piece"
    assert preview.parsed_quantity_per_supplier_unit == "100"
    assert preview.parsed_quantity_per_carton == "500"
    assert preview.parsed_supplier_units_per_carton == "5"
    assert preview.parsed_roll_or_spool_length_metres == ""
    assert preview.supplier_description_raw == "Buckle"


def test_duplicate_mapping_is_reviewed(tmp_path: Path) -> None:
    path = tmp_path / "yu.xlsx"
    create_workbook(path)
    rows = extract_yuchang_packing_rows(path)
    counts = workbook_mapping_counts(rows)

    preview = build_yuchang_packing_preview_row(
        rows[0],
        mapping_count=counts[clean_item_key("ITEM-A")],
        widget_matches=[{"item_number": "ITEM-A", "item_name": "Poly Tape"}],
    )

    assert preview.preview_status == "review"
    assert "mapped to 2 workbook rows" in preview.review_reason
    assert preview.parsed_roll_or_spool_length_metres == "100"
    assert preview.parsed_metres_per_carton == "3000"
    assert preview.parsed_supplier_units_per_carton == "30"
