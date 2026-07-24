from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

from windsor_widget.db.models import ItemSupplier
from windsor_widget.services.yuchang_packing_preview import YuchangPackingPreviewRow
from windsor_widget.services.yuchang_packing_sync import (
    classify_action,
    proposal_from_preview,
)


def preview_row(**changes):
    values = {
        "source_row": 742,
        "item_number": "TEST-ROLL",
        "item_number_key": "TEST-ROLL",
        "workbook_mapping_count": 1,
        "widget_match_status": "matched",
        "widget_item_number": "TEST-ROLL",
        "widget_item_name": "Test roll",
        "supplier_link_status": "approved",
        "supplier_item_number": "",
        "supplier_description_raw": "Supplier test roll",
        "size_raw": "50 mm",
        "colour_raw": "Black",
        "supplier_unit_raw": "Roll",
        "quantity_per_supplier_unit_raw": "100",
        "label_description_raw": "TEST ROLL",
        "quantity_per_carton_raw": "2000",
        "quantity_per_pallet_raw": "NO",
        "fob_price_raw": "1.25",
        "inferred_measure": "metre",
        "parsed_quantity_per_supplier_unit": "100",
        "parsed_quantity_per_carton": "2000",
        "parsed_supplier_units_per_carton": "20",
        "parsed_roll_or_spool_length_metres": "100",
        "parsed_metres_per_carton": "2000",
        "parsed_quantity_per_pallet": "",
        "parsed_cartons_per_pallet": "",
        "parsed_fob_price": "1.25",
        "preview_status": "review",
        "review_reason": "Could not parse quantity per pallet: 'NO'.",
        "proposed_action": "review_before_field_design",
    }
    values.update(changes)
    return YuchangPackingPreviewRow(**values)


def test_approved_roll_rule_ignores_unreliable_pallet_column(tmp_path):
    proposal, reason = proposal_from_preview(
        preview_row(),
        item_id=uuid.uuid4(),
        workbook_path=tmp_path / "YU.xlsx",
        worksheet_name="Sheet1",
    )
    assert reason == ""
    assert proposal is not None
    assert proposal.roll_spool_length_metres == Decimal("100")
    assert proposal.metres_per_carton == Decimal("2000")
    assert proposal.supplier_units_per_carton == Decimal("20")


def test_non_roll_first_pass_is_held(tmp_path):
    proposal, reason = proposal_from_preview(
        preview_row(supplier_unit_raw="piece", inferred_measure="piece"),
        item_id=uuid.uuid4(),
        workbook_path=tmp_path / "YU.xlsx",
        worksheet_name="Sheet1",
    )
    assert proposal is None
    assert "outside the approved roll/spool" in reason


def test_duplicate_workbook_mapping_is_held(tmp_path):
    proposal, reason = proposal_from_preview(
        preview_row(workbook_mapping_count=2),
        item_id=uuid.uuid4(),
        workbook_path=tmp_path / "YU.xlsx",
        worksheet_name="Sheet1",
    )
    assert proposal is None
    assert "mapped to 2" in reason


def test_manual_packing_values_are_preserved(tmp_path):
    proposal, _ = proposal_from_preview(
        preview_row(),
        item_id=uuid.uuid4(),
        workbook_path=tmp_path / "YU.xlsx",
        worksheet_name="Sheet1",
    )
    assert proposal is not None
    link = SimpleNamespace(
        match_status="approved",
        packing_source="user",
    )
    action, reason, changed = classify_action(link, proposal)
    assert action == "held_manual_values"
    assert "manually maintained" in reason
    assert changed == ()


def test_item_supplier_metadata_contains_packing_fields():
    columns = set(ItemSupplier.__table__.c.keys())
    assert {
        "supplier_unit_type",
        "roll_spool_length_metres",
        "metres_per_carton",
        "supplier_units_per_carton",
        "packing_source",
        "packing_source_workbook",
        "packing_source_worksheet",
        "packing_source_row",
        "packing_verified_at",
    }.issubset(columns)
