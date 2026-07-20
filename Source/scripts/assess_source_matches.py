#!/usr/bin/env python3
"""Build deterministic, review-only source and match assessment files.

This command deliberately has no database dependency. It reads supplied exports,
retains parse/mapping problems for review, and emits proposals rather than approvals.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TypeVar

from windsor_widget.imports.contracts import SOURCE_CONTRACTS
from windsor_widget.imports.master_data import (
    CustomerMasterCandidate,
    ItemMasterCandidate,
    SupplierMasterCandidate,
    map_customer_master,
    map_item_master,
    map_supplier_master,
)
from windsor_widget.imports.matching import (
    CustomerPriceFileProposal,
    ItemSupplierProposal,
    load_customer_price_file_references,
    propose_customer_groups,
    propose_customer_price_file_matches,
    propose_item_supplier_matches,
)
from windsor_widget.imports.myob_text import inspect_myob_text, iter_myob_rows

Candidate = TypeVar("Candidate")
SourceRow = Mapping[str, str | None]


def _write_csv(path: Path, fieldnames: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _alternatives_text(proposal: CustomerPriceFileProposal | ItemSupplierProposal) -> str:
    return " | ".join(
        f"{alternative.target_key} :: {alternative.target_name} :: {alternative.score}"
        for alternative in proposal.alternatives
    )


def _load_source_rows(
    path: Path,
    source_type: str,
) -> tuple[list[SourceRow], dict[str, Any], list[dict[str, Any]]]:
    contract = SOURCE_CONTRACTS[source_type]
    inspection = inspect_myob_text(path, contract)
    valid_rows: list[SourceRow] = []
    row_reviews: list[dict[str, Any]] = []
    total_rows = 0
    rows_requiring_review = 0
    for parsed in iter_myob_rows(path, contract, inspection=inspection):
        total_rows += 1
        if parsed.review_required:
            rows_requiring_review += 1
            row_reviews.extend(
                {
                    "source_type": source_type,
                    "source_file": path.name,
                    "row_number": parsed.row_number,
                    "natural_key": parsed.natural_key or "",
                    "issue_code": issue.issue_code,
                    "field_name": issue.field_name or "",
                    "message": issue.message,
                }
                for issue in parsed.issues
            )
            continue
        valid_rows.append(parsed.values)

    source_summary = {
        "source_file": path.name,
        "source_type": source_type,
        "file_sha256": inspection.file_sha256,
        "encoding": inspection.encoding,
        "header_row_number": inspection.header_row_number,
        "rows_seen": total_rows,
        "rows_usable": len(valid_rows),
        "rows_requiring_review": rows_requiring_review,
        "file_issue_count": len(inspection.issues),
    }
    return valid_rows, source_summary, row_reviews


def _map_candidates(
    rows: list[SourceRow],
    source_type: str,
    source_file: str,
    mapper: Callable[[SourceRow], Candidate],
) -> tuple[list[Candidate], list[dict[str, Any]]]:
    candidates: list[Candidate] = []
    reviews: list[dict[str, Any]] = []
    for offset, row in enumerate(rows, start=1):
        try:
            candidates.append(mapper(row))
        except ValueError as error:
            reviews.append(
                {
                    "source_type": source_type,
                    "source_file": source_file,
                    "row_number": offset,
                    "natural_key": row.get("Item Number")
                    or row.get("Record ID")
                    or "",
                    "issue_code": "master_mapping_failed",
                    "field_name": "",
                    "message": str(error),
                }
            )
    return candidates, reviews


def _count(proposals: tuple[Any, ...], predicate: Callable[[Any], bool]) -> int:
    return sum(1 for proposal in proposals if predicate(proposal))


def build_assessment(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    item_rows, item_summary, row_reviews = _load_source_rows(
        args.item_master, "item_master"
    )
    customer_rows, customer_summary, customer_reviews = _load_source_rows(
        args.customer_master, "customer_master"
    )
    supplier_rows, supplier_summary, supplier_reviews = _load_source_rows(
        args.supplier_master, "supplier_master"
    )
    purchase_rows, purchase_summary, purchase_reviews = _load_source_rows(
        args.purchases, "purchase_transactions"
    )
    row_reviews.extend(customer_reviews)
    row_reviews.extend(supplier_reviews)
    row_reviews.extend(purchase_reviews)

    items, mapping_reviews = _map_candidates(
        item_rows, "item_master", args.item_master.name, map_item_master
    )
    customers, customer_mapping_reviews = _map_candidates(
        customer_rows,
        "customer_master",
        args.customer_master.name,
        map_customer_master,
    )
    suppliers, supplier_mapping_reviews = _map_candidates(
        supplier_rows,
        "supplier_master",
        args.supplier_master.name,
        map_supplier_master,
    )
    row_reviews.extend(mapping_reviews)
    row_reviews.extend(customer_mapping_reviews)
    row_reviews.extend(supplier_mapping_reviews)

    typed_items = list(items)
    typed_customers = list(customers)
    typed_suppliers = list(suppliers)
    assert all(isinstance(item, ItemMasterCandidate) for item in typed_items)
    assert all(isinstance(customer, CustomerMasterCandidate) for customer in typed_customers)
    assert all(isinstance(supplier, SupplierMasterCandidate) for supplier in typed_suppliers)

    group_proposals = propose_customer_groups(typed_customers)
    price_references = load_customer_price_file_references(args.customer_paths)
    price_proposals = propose_customer_price_file_matches(group_proposals, price_references)
    supplier_proposals = propose_item_supplier_matches(
        typed_items,
        typed_suppliers,
        purchase_rows,
    )

    multi_account_rows = [
        {
            "customer_group_key": proposal.group_key,
            "display_name": proposal.display_name,
            "account_count": len(proposal.account_record_ids),
            "account_record_ids": " | ".join(proposal.account_record_ids),
            "account_names": " | ".join(proposal.account_names),
            "score": proposal.score,
            "method": proposal.method,
            "requires_review": proposal.requires_review,
            "evidence": " | ".join(proposal.evidence),
        }
        for proposal in group_proposals
        if len(proposal.account_record_ids) > 1
    ]
    price_review_rows = [
        {
            "file_path": proposal.file_path,
            "file_name": proposal.file_name,
            "proposed_customer_group_key": proposal.customer_group_key or "",
            "proposed_customer_group_name": proposal.customer_group_name or "",
            "score": proposal.score,
            "review_reason": (
                "unmatched"
                if proposal.score == 0
                else "ambiguous"
                if proposal.customer_group_key is None
                else "non_exact"
            ),
            "method": proposal.method,
            "alternatives": _alternatives_text(proposal),
            "evidence": " | ".join(proposal.evidence),
            "approval": "",
            "approved_customer_group_key": "",
            "review_notes": "",
        }
        for proposal in price_proposals
        if proposal.requires_review
    ]
    price_review_rows.sort(
        key=lambda row: (
            {"ambiguous": 0, "non_exact": 1, "unmatched": 2}[row["review_reason"]],
            -int(row["score"]),
            str(row["file_name"]).casefold(),
        )
    )
    supplier_review_rows = [
        {
            "item_number": proposal.item_number,
            "source_supplier_name": proposal.source_supplier_name,
            "proposed_supplier_record_id": proposal.supplier_record_id or "",
            "proposed_supplier_name": proposal.supplier_name or "",
            "score": proposal.score,
            "method": proposal.method,
            "last_purchase_number": (
                proposal.last_purchase.purchase_number if proposal.last_purchase else ""
            ),
            "last_purchase_date": (
                proposal.last_purchase.purchase_date.isoformat()
                if proposal.last_purchase
                else ""
            ),
            "alternatives": _alternatives_text(proposal),
            "evidence": " | ".join(proposal.evidence),
            "approval": "",
            "approved_supplier_record_id": "",
            "review_notes": "",
        }
        for proposal in supplier_proposals
        if proposal.requires_review
    ]

    _write_csv(
        output_dir / "source_row_review.csv",
        (
            "source_type",
            "source_file",
            "row_number",
            "natural_key",
            "issue_code",
            "field_name",
            "message",
        ),
        row_reviews,
    )
    _write_csv(
        output_dir / "customer_group_multi_account.csv",
        (
            "customer_group_key",
            "display_name",
            "account_count",
            "account_record_ids",
            "account_names",
            "score",
            "method",
            "requires_review",
            "evidence",
        ),
        multi_account_rows,
    )
    _write_csv(
        output_dir / "customer_price_file_review.csv",
        (
            "file_path",
            "file_name",
            "proposed_customer_group_key",
            "proposed_customer_group_name",
            "score",
            "review_reason",
            "method",
            "alternatives",
            "evidence",
            "approval",
            "approved_customer_group_key",
            "review_notes",
        ),
        price_review_rows,
    )
    _write_csv(
        output_dir / "item_supplier_review.csv",
        (
            "item_number",
            "source_supplier_name",
            "proposed_supplier_record_id",
            "proposed_supplier_name",
            "score",
            "method",
            "last_purchase_number",
            "last_purchase_date",
            "alternatives",
            "evidence",
            "approval",
            "approved_supplier_record_id",
            "review_notes",
        ),
        supplier_review_rows,
    )

    summary = {
        "database_created": False,
        "assessment_mode": "review_only",
        "sources": {
            "item_master": item_summary,
            "customer_master": customer_summary,
            "supplier_master": supplier_summary,
            "purchase_transactions": purchase_summary,
            "customer_price_paths": {
                "source_file": args.customer_paths.name,
                "current_excel_paths": len(price_references),
            },
        },
        "master_candidates": {
            "items": len(typed_items),
            "planning_items": _count(
                tuple(typed_items), lambda item: not item.excluded_from_item_view
            ),
            "hidden_control_items": _count(
                tuple(typed_items), lambda item: item.excluded_from_item_view
            ),
            "customers": len(typed_customers),
            "suppliers": len(typed_suppliers),
            "mapping_review_rows": len(mapping_reviews)
            + len(customer_mapping_reviews)
            + len(supplier_mapping_reviews),
        },
        "customer_groups": {
            "proposals": len(group_proposals),
            "multi_account_groups": len(multi_account_rows),
            "accounts_in_multi_account_groups": sum(
                len(proposal.account_record_ids)
                for proposal in group_proposals
                if len(proposal.account_record_ids) > 1
            ),
            "requires_review": _count(
                group_proposals, lambda proposal: proposal.requires_review
            ),
        },
        "customer_price_files": {
            "proposals": len(price_proposals),
            "exact_unique": _count(
                price_proposals,
                lambda proposal: not proposal.requires_review and proposal.score == 100,
            ),
            "requires_review": len(price_review_rows),
            "unmatched": _count(
                price_proposals, lambda proposal: proposal.score == 0
            ),
            "ambiguous": _count(
                price_proposals,
                lambda proposal: (
                    proposal.score > 0 and proposal.customer_group_key is None
                ),
            ),
            "non_exact_unique": _count(
                price_proposals,
                lambda proposal: (
                    proposal.requires_review and proposal.customer_group_key is not None
                ),
            ),
        },
        "item_suppliers": {
            "proposals": len(supplier_proposals),
            "exact_unique": _count(
                supplier_proposals,
                lambda proposal: not proposal.requires_review and proposal.score == 100,
            ),
            "requires_review": len(supplier_review_rows),
            "unmatched_or_ambiguous": _count(
                supplier_proposals, lambda proposal: proposal.supplier_record_id is None
            ),
            "planning_items_without_source_supplier": (
                _count(tuple(typed_items), lambda item: not item.excluded_from_item_view)
                - len(supplier_proposals)
            ),
        },
        "review_files": {
            "source_rows": "source_row_review.csv",
            "multi_account_groups": "customer_group_multi_account.csv",
            "customer_price_files": "customer_price_file_review.csv",
            "item_suppliers": "item_supplier_review.csv",
        },
    }
    with (output_dir / "source_match_summary.json").open("w", encoding="utf-8") as stream:
        json.dump(summary, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--item-master", type=Path, required=True)
    parser.add_argument("--customer-master", type=Path, required=True)
    parser.add_argument("--supplier-master", type=Path, required=True)
    parser.add_argument("--purchases", type=Path, required=True)
    parser.add_argument("--customer-paths", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    summary = build_assessment(args)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
