"""Preview or apply customer freight-payer tags from invoiced freight data."""

from __future__ import annotations

import argparse
import csv
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from windsor_widget.config import load_settings
from windsor_widget.db.models import CustomerAccount
from windsor_widget.db.session import create_database_engine, create_session_factory
from windsor_widget.imports.promotion import ensure_app_user
from windsor_widget.services.freight_inference import (
    apply_customer_freight_inference,
    get_customer_freight_evidence,
)


def _date(value: str) -> date:
    return date.fromisoformat(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Infer Customer or Windsor freight payer from invoice freight. "
            "Freight duplicated across MYOB export lines is counted once per invoice."
        )
    )
    parser.add_argument("config", type=Path)
    parser.add_argument(
        "--months",
        type=int,
        help="use only the latest calendar months; omit for all invoiced history",
    )
    parser.add_argument("--as-of", type=_date, default=date.today())
    parser.add_argument("--minimum-invoices", type=int, default=1)
    parser.add_argument("--charge-threshold", type=Decimal, default=Decimal("0.01"))
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--username")
    parser.add_argument("--display-name")
    parser.add_argument("--report", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.commit and (not args.username or not args.display_name):
        raise SystemExit("--username and --display-name are required with --commit")

    settings = load_settings(args.config)
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)

    try:
        with session_factory() as session:
            evidence = get_customer_freight_evidence(
                session,
                as_of_date=args.as_of,
                months=args.months,
                minimum_invoices=args.minimum_invoices,
                charge_threshold=args.charge_threshold,
            )
            evidence_ids = set(evidence)
            current = {
                customer.customer_account_id: customer.freight_payer
                for customer in session.scalars(select(CustomerAccount))
                if customer.customer_account_id in evidence_ids
            }

            summary = None
            if args.commit:
                actor = ensure_app_user(
                    session,
                    username=args.username,
                    display_name=args.display_name,
                )
                summary = apply_customer_freight_inference(
                    session,
                    evidence,
                    actor_user_id=actor.user_id,
                )
                session.commit()

            report_path = args.report or (
                Path(settings.folders.exports)
                / f"customer_freight_inference_{datetime.now():%Y%m%d_%H%M%S}.csv"
            )
            report_path.parent.mkdir(parents=True, exist_ok=True)
            with report_path.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle)
                writer.writerow(
                    [
                        "customer",
                        "card_id",
                        "current_freight_payer",
                        "suggested_freight_payer",
                        "confidence",
                        "invoice_count",
                        "charged_invoices",
                        "zero_freight_invoices",
                        "charged_percent",
                        "deduplicated_total_freight",
                        "evidence_start",
                        "evidence_end",
                        "will_apply",
                        "explanation",
                    ]
                )
                for row in sorted(
                    evidence.values(),
                    key=lambda value: value.display_name.casefold(),
                ):
                    current_value = current.get(row.customer_account_id, "unknown")
                    writer.writerow(
                        [
                            row.display_name,
                            row.myob_card_id or "",
                            current_value,
                            row.suggested_payer,
                            row.confidence or "",
                            row.invoice_count,
                            row.charged_invoice_count,
                            row.zero_invoice_count,
                            f"{row.charged_ratio * 100:.2f}",
                            row.total_invoice_freight,
                            row.evidence_start or "",
                            row.evidence_end,
                            (
                                "yes"
                                if current_value == "unknown"
                                and row.suggested_payer in {"customer", "windsor"}
                                else "no"
                            ),
                            row.explanation,
                        ]
                    )

            suggested_customer = sum(
                row.suggested_payer == "customer" for row in evidence.values()
            )
            suggested_windsor = sum(
                row.suggested_payer == "windsor" for row in evidence.values()
            )
            unresolved = sum(
                row.suggested_payer == "unknown" for row in evidence.values()
            )
            would_apply = sum(
                current.get(row.customer_account_id, "unknown") == "unknown"
                and row.suggested_payer in {"customer", "windsor"}
                for row in evidence.values()
            )

            print("Customer freight inference")
            print("Invoice freight is deduplicated with MAX(ABS(freight)) per invoice.")
            print(
                f"Evidence customers={len(evidence)}; "
                f"suggest_customer={suggested_customer}; "
                f"suggest_windsor={suggested_windsor}; unresolved={unresolved}"
            )
            print(f"Unknown tags eligible for update: {would_apply}")
            if summary is None:
                print("Preview only; no customer records were changed.")
            else:
                print(
                    f"Applied={summary.applied_total} "
                    f"(customer={summary.applied_customer}; "
                    f"windsor={summary.applied_windsor}); "
                    f"existing tags preserved={summary.skipped_existing}"
                )
            print(f"Report: {report_path}")
            return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
