from __future__ import annotations

from decimal import Decimal

import pytest

from windsor_widget.imports.master_data import (
    map_customer_master,
    map_item_master,
    map_supplier_master,
)


def test_item_mapping_understands_myob_codes_and_retains_control_items() -> None:
    candidate = map_item_master(
        {
            "Item Number": "\\FC",
            "Item Name": "Freight charge",
            "Buy": "B",
            "Sell": "S",
            "Inventory": "I",
            "Inactive Item": "N",
            "Standard Cost": "$12.50",
            "Reorder Quantity": "1,000",
            "Minimum Level": "250",
            "Primary Supplier": "Example Supplier",
        }
    )

    assert candidate.is_bought is True
    assert candidate.is_sold is True
    assert candidate.is_inventoried is True
    assert candidate.is_active is True
    assert candidate.excluded_from_item_view is True
    assert candidate.standard_cost == Decimal("12.50")
    assert candidate.reorder_quantity == Decimal("1000")


def test_unnamed_control_item_uses_its_code_as_evidence_name() -> None:
    candidate = map_item_master(
        {
            "Item Number": "\\LUV",
            "Item Name": "",
            "Buy": "B",
            "Sell": "S",
            "Inventory": "I",
        }
    )

    assert candidate.item_name == "\\LUV"
    assert candidate.excluded_from_item_view is True


def test_customer_mapping_keeps_user_toggles_out_of_source_inference() -> None:
    candidate = map_customer_master(
        {
            "Co./Last Name": "Comfort Sleep Bedding Company",
            "Card ID": "*None",
            "Card Status": "N",
            "Record ID": "150",
            "Addr 1 - City": "Thomastown",
            "Shipping Method": "Advance Transport",
        }
    )

    assert candidate.myob_card_id is None
    assert candidate.is_active is True
    assert candidate.normalized_name == "comfort sleep bedding company"
    assert candidate.city == "Thomastown"


def test_inactive_supplier_card_is_mapped_without_deleting_history() -> None:
    candidate = map_supplier_master(
        {
            "Co./Last Name": "ZZZZZZATI Pty Ltd",
            "Card Status": "Y",
            "Record ID": "722",
        }
    )

    assert candidate.is_active is False
    assert candidate.display_name == "ZZZZZZATI Pty Ltd"


def test_missing_item_identity_stops_mapping_for_review() -> None:
    with pytest.raises(ValueError, match="Item Number"):
        map_item_master({"Item Name": "Unnamed code"})
