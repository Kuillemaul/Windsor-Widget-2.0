from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from windsor_widget.imports.master_data import (
    map_customer_master,
    map_item_master,
    map_supplier_master,
)
from windsor_widget.imports.matching import (
    PriceFileReference,
    load_customer_price_file_references,
    most_recent_purchase_by_item,
    propose_customer_groups,
    propose_customer_price_file_matches,
    propose_item_supplier_matches,
)


def _customer(name: str, record_id: str):
    return map_customer_master(
        {
            "Co./Last Name": name,
            "Card Status": "N",
            "Record ID": record_id,
        }
    )


def _supplier(name: str, record_id: str):
    return map_supplier_master(
        {
            "Co./Last Name": name,
            "Card Status": "N",
            "Record ID": record_id,
        }
    )


def _item(number: str, primary_supplier: str | None = None):
    return map_item_master(
        {
            "Item Number": number,
            "Item Name": f"Item {number}",
            "Buy": "B",
            "Sell": "S",
            "Inventory": "I",
            "Inactive Item": "N",
            "Primary Supplier": primary_supplier,
        }
    )


def test_state_accounts_group_only_on_exact_business_family() -> None:
    proposals = propose_customer_groups(
        (
            _customer("Beard A. H. Pty Ltd - NSW", "63"),
            _customer("Beard A. H. Pty Ltd - Victoria", "1038"),
            _customer("Unrelated Bedding Pty Ltd", "2000"),
        )
    )

    beard = next(proposal for proposal in proposals if proposal.group_key == "a beard h")
    assert beard.account_record_ids == ("63", "1038")
    assert beard.requires_review is False
    assert len(proposals) == 2


def test_dotted_state_abbreviation_groups_with_plain_state_suffix() -> None:
    proposals = propose_customer_groups(
        (
            _customer("Sealy of Australia (QLD) Pty Ltd - N.S.W.", "100"),
            _customer("Sealy of Australia (QLD) Pty Ltd - NSW", "101"),
        )
    )

    assert len(proposals) == 1
    assert proposals[0].account_record_ids == ("100", "101")


def test_price_path_workbook_keeps_current_files_only(tmp_path: Path) -> None:
    path = tmp_path / "paths.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "FILES"
    sheet.append([r"S:\Customer Prices\beard a h.xlsx"])
    sheet.append([r"S:\Customer Prices\Old\beard a h.xlsx"])
    sheet.append([r"S:\Customer Prices\beard a h.xlsx"])
    sheet.append([r"S:\Customer Prices\readme.txt"])
    workbook.save(path)

    references = load_customer_price_file_references(path)

    assert len(references) == 1
    assert references[0].file_name == "beard a h.xlsx"
    assert references[0].normalized_stem == "beard a h"


def test_price_file_exact_match_is_automatic_and_partial_match_is_reviewed() -> None:
    groups = propose_customer_groups(
        (
            _customer("Beard A. H. Pty Ltd - NSW", "63"),
            _customer("Comfort Sleep Bedding Company", "150"),
        )
    )
    proposals = propose_customer_price_file_matches(
        groups,
        (
            PriceFileReference(
                path=r"S:\Customer Prices\beard a h.xlsx",
                file_name="beard a h.xlsx",
                normalized_stem="beard a h",
            ),
            PriceFileReference(
                path=r"S:\Customer Prices\comfort sleep.xlsx",
                file_name="comfort sleep.xlsx",
                normalized_stem="comfort sleep",
            ),
        ),
    )

    by_name = {proposal.file_name: proposal for proposal in proposals}
    assert by_name["beard a h.xlsx"].score == 100
    assert by_name["beard a h.xlsx"].requires_review is False
    assert by_name["comfort sleep.xlsx"].score == 90
    assert by_name["comfort sleep.xlsx"].requires_review is True


def test_unmatched_price_file_is_retained_for_manual_review() -> None:
    groups = propose_customer_groups((_customer("Comfort Sleep Bedding Company", "150"),))
    proposals = propose_customer_price_file_matches(
        groups,
        (
            PriceFileReference(
                path=r"S:\Customer Prices\unknown.xlsx",
                file_name="unknown.xlsx",
                normalized_stem="unknown",
            ),
        ),
    )

    assert proposals[0].customer_group_key is None
    assert proposals[0].score == 0
    assert proposals[0].requires_review is True


def test_item_supplier_falls_back_to_most_recent_purchase() -> None:
    proposals = propose_item_supplier_matches(
        items=(_item("ABC-1"),),
        suppliers=(
            _supplier("Old Supplier Pty Ltd", "10"),
            _supplier("Recent Supplier Limited", "11"),
        ),
        purchases=(
            {
                "Item Number": "ABC-1",
                "Co./Last Name": "Old Supplier Pty Ltd",
                "Purchase No.": "PO-1",
                "Date": "01/01/2026",
                "Purchase Status": "O",
            },
            {
                "Item Number": "ABC-1",
                "Co./Last Name": "Recent Supplier Limited",
                "Purchase No.": "PO-2",
                "Date": "01/07/2026",
                "Purchase Status": "O",
            },
        ),
    )

    proposal = proposals[0]
    assert proposal.method == "most_recent_purchase_supplier"
    assert proposal.supplier_record_id == "11"
    assert proposal.last_purchase is not None
    assert proposal.last_purchase.purchase_number == "PO-2"
    assert proposal.requires_review is False


def test_duplicate_supplier_cards_stay_unresolved() -> None:
    proposals = propose_item_supplier_matches(
        items=(_item("ABC-1", "Pacific Textiles"),),
        suppliers=(
            _supplier("Pacific Textiles Pty Ltd", "10"),
            _supplier("Pacific Textiles Limited", "11"),
        ),
        purchases=(),
    )

    proposal = proposals[0]
    assert proposal.score == 100
    assert proposal.supplier_record_id is None
    assert proposal.requires_review is True
    assert len(proposal.alternatives) == 2


def test_control_items_are_kept_as_evidence_but_excluded_from_planning_matches() -> None:
    latest = most_recent_purchase_by_item(
        (
            {
                "Item Number": "\\FC",
                "Co./Last Name": "Freight Supplier",
                "Purchase No.": "PO-1",
                "Date": "01/07/2026",
            },
        )
    )
    proposals = propose_item_supplier_matches(
        items=(_item("\\FC", "Freight Supplier"),),
        suppliers=(_supplier("Freight Supplier", "10"),),
        purchases=(),
    )

    assert latest == {}
    assert proposals == ()
