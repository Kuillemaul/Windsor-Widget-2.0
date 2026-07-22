"""Small operational commands that are safe to run before the UI exists."""

from __future__ import annotations

import argparse
from pathlib import Path

from windsor_widget.config import load_settings
from windsor_widget.db.bootstrap import (
    ensure_development_database,
    upgrade_development_database,
    verify_development_database,
)
from windsor_widget.db.session import create_database_engine, create_session_factory
from windsor_widget.imports.pipeline import (
    load_source_manifest,
    run_import_pipeline,
    write_pipeline_report,
)
from windsor_widget.imports.promotion import (
    MasterImportError,
    approve_master_batches,
    ensure_app_user,
    promote_master_batches,
    review_master_batches,
)
from windsor_widget.imports.transaction_promotion import (
    TransactionImportError,
    approve_transaction_batches,
    promote_transaction_batches,
    review_transaction_batches,
)
from windsor_widget.services.reporting import (
    ReportingLookupError,
    get_customer_summary,
    get_foundation_counts,
    get_item_summary,
    parse_iso_date,
    search_customers,
    search_items,
    validate_foundation_counts,
)
from windsor_widget.imports.inventory_snapshot import (
    InventorySnapshotError,
    commit_inventory_snapshot,
    current_inventory_snapshot,
    inventory_snapshot_age_days,
    parse_iso_datetime,
    preview_inventory_snapshot,
)
from windsor_widget.services.planning import (
    PlanningLookupError,
    get_item_planning_analysis,
    get_order_analysis,
    get_planning_readiness,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="windsor-widget")
    subcommands = parser.add_subparsers(dest="command", required=True)

    check = subcommands.add_parser(
        "check-config",
        help="validate a development configuration without connecting to SQL Server",
    )
    check.add_argument("config", type=Path)

    setup = subcommands.add_parser(
        "setup-dev-database",
        help="create, migrate and verify only WindsorWidgetV2_DEV",
    )
    setup.add_argument("config", type=Path)
    setup.add_argument(
        "--alembic-config",
        type=Path,
        default=Path("alembic.ini"),
        help="path to alembic.ini (default: ./alembic.ini)",
    )

    stage = subcommands.add_parser(
        "stage-myob-exports",
        help="inspect MYOB exports or stage them for review without promoting data",
    )
    stage.add_argument("config", type=Path)
    stage.add_argument("--manifest", type=Path, required=True)
    stage.add_argument(
        "--commit",
        action="store_true",
        help="write review-first staging rows; otherwise perform a database-free dry run",
    )
    stage.add_argument(
        "--report",
        type=Path,
        help="JSON report path (default: configured exports folder/myob_staging_report.json)",
    )
    stage.add_argument(
        "--chunk-size",
        type=int,
        default=1_000,
        help="rows flushed per database chunk (default: 1000)",
    )

    review = subcommands.add_parser(
        "review-master-imports",
        help="list uncommitted MYOB item, customer and supplier batches",
    )
    review.add_argument("config", type=Path)

    approve = subcommands.add_parser(
        "approve-master-imports",
        help="explicitly approve exactly one clean batch for each master source",
    )
    approve.add_argument("config", type=Path)
    approve.add_argument("--username", required=True)
    approve.add_argument("--display-name", required=True)

    promote = subcommands.add_parser(
        "promote-master-imports",
        help="preview or commit exact-key master-data promotion",
    )
    promote.add_argument("config", type=Path)
    promote.add_argument(
        "--commit",
        action="store_true",
        help="write master records and audit events; otherwise preview only",
    )
    promote.add_argument("--username")
    promote.add_argument("--display-name")

    transaction_review = subcommands.add_parser(
        "review-transaction-imports",
        help="list uncommitted MYOB sales, cover-order and purchase batches",
    )
    transaction_review.add_argument("config", type=Path)

    transaction_approve = subcommands.add_parser(
        "approve-transaction-imports",
        help="explicitly approve exactly one clean batch for each transaction source",
    )
    transaction_approve.add_argument("config", type=Path)
    transaction_approve.add_argument("--username", required=True)
    transaction_approve.add_argument("--display-name", required=True)

    transaction_promote = subcommands.add_parser(
        "promote-transaction-imports",
        help="preview or commit exact-key transaction promotion",
    )
    transaction_promote.add_argument("config", type=Path)
    transaction_promote.add_argument(
        "--commit",
        action="store_true",
        help="write transaction records and audit lineage; otherwise preview only",
    )
    transaction_promote.add_argument("--username")
    transaction_promote.add_argument("--display-name")

    reporting_verify = subcommands.add_parser(
        "verify-reporting-data",
        help="verify committed reporting counts and transaction lineage",
    )
    reporting_verify.add_argument("config", type=Path)

    find_items = subcommands.add_parser(
        "find-items",
        help="search active planning items by number or name",
    )
    find_items.add_argument("config", type=Path)
    find_items.add_argument("query")
    find_items.add_argument("--limit", type=int, default=20)

    find_customers = subcommands.add_parser(
        "find-customers",
        help="search active customers by name, card ID or record ID",
    )
    find_customers.add_argument("config", type=Path)
    find_customers.add_argument("query")
    find_customers.add_argument("--limit", type=int, default=20)

    item_summary = subcommands.add_parser(
        "item-summary",
        help="show a read-only Item Summary for one exact MYOB item number",
    )
    item_summary.add_argument("config", type=Path)
    item_summary.add_argument("item_number")
    item_summary.add_argument("--months", type=int, default=12)
    item_summary.add_argument("--as-of", type=parse_iso_date)

    customer_summary = subcommands.add_parser(
        "customer-summary",
        help="show a read-only Customer Summary for one MYOB record ID",
    )
    customer_summary.add_argument("config", type=Path)
    customer_summary.add_argument("myob_record_id")
    customer_summary.add_argument("--months", type=int, default=12)
    customer_summary.add_argument("--as-of", type=parse_iso_date)

    inventory_preview = subcommands.add_parser(
        "preview-inventory-snapshot",
        help="validate an MYOB Analyse Inventory workbook without writing data",
    )
    inventory_preview.add_argument("config", type=Path)
    inventory_preview.add_argument("source_file", type=Path)
    inventory_preview.add_argument("--captured-at", type=parse_iso_datetime)

    inventory_commit = subcommands.add_parser(
        "commit-inventory-snapshot",
        help="commit a clean immutable inventory snapshot",
    )
    inventory_commit.add_argument("config", type=Path)
    inventory_commit.add_argument("source_file", type=Path)
    inventory_commit.add_argument("--captured-at", type=parse_iso_datetime)
    inventory_commit.add_argument("--username", required=True)
    inventory_commit.add_argument("--display-name", required=True)

    inventory_status = subcommands.add_parser(
        "inventory-snapshot-status",
        help="show the current committed inventory snapshot",
    )
    inventory_status.add_argument("config", type=Path)

    planning_readiness = subcommands.add_parser(
        "planning-readiness",
        help="show remaining data gaps before the Order Analysis UI",
    )
    planning_readiness.add_argument("config", type=Path)

    item_planning = subcommands.add_parser(
        "item-planning",
        help="show explainable demand and inventory planning for one item",
    )
    item_planning.add_argument("config", type=Path)
    item_planning.add_argument("item_number")
    item_planning.add_argument("--months", type=int, default=12)
    item_planning.add_argument("--lead-weeks", type=int, default=14)
    item_planning.add_argument("--trend", choices=("3v3", "6v6", "yoy"), default="3v3")
    item_planning.add_argument("--as-of", type=parse_iso_date)

    order_analysis = subcommands.add_parser(
        "order-analysis",
        help="show the first all-item Order Analysis read model",
    )
    order_analysis.add_argument("config", type=Path)
    order_analysis.add_argument("--months", type=int, default=12)
    order_analysis.add_argument("--lead-weeks", type=int, default=14)
    order_analysis.add_argument("--trend", choices=("3v3", "6v6", "yoy"), default="3v3")
    order_analysis.add_argument("--as-of", type=parse_iso_date)
    order_analysis.add_argument("--limit", type=int, default=50)
    order_analysis.add_argument("--include-ok", action="store_true")
    return parser


def _print_promotion(summary) -> None:
    print(f"Master promotion mode: {summary.mode}")
    for change in summary.changes:
        print(
            f"{change.source_type}: total={change.total}; created={change.created}; "
            f"updated={change.updated}; unchanged={change.unchanged}"
        )
    print(
        f"Totals: total={summary.total}; created={summary.created}; "
        f"updated={summary.updated}; unchanged={summary.unchanged}"
    )


def _print_transaction_promotion(summary) -> None:
    print(f"Transaction promotion mode: {summary.mode}")
    for change in summary.changes:
        snapshot_text = (
            f"; snapshots_created={change.snapshots_created}"
            if change.snapshots_created
            else ""
        )
        print(
            f"{change.source_type}: documents={change.document_total} "
            f"(created={change.documents_created}, updated={change.documents_updated}, "
            f"unchanged={change.documents_unchanged}); lines={change.line_total} "
            f"(created={change.lines_created}, updated={change.lines_updated}, "
            f"unchanged={change.lines_unchanged}, retired={change.lines_retired})"
            f"{snapshot_text}"
        )
    print(
        f"Totals: documents={summary.document_total}; lines={summary.line_total}; "
        f"created={summary.lines_created}; updated={summary.lines_updated}; "
        f"unchanged={summary.lines_unchanged}"
    )


def _print_activity(label, totals) -> None:
    print(
        f"{label}: documents={totals.document_count}; lines={totals.line_count}; "
        f"quantity={totals.quantity}; value={totals.value}; "
        f"first={totals.first_date or '-'}; last={totals.last_date or '-'}"
    )


def _print_item_summary(summary) -> None:
    print(f"Item: {summary.item_number} — {summary.item_name}")
    print(
        f"Status: active={summary.is_active}; bought={summary.is_bought}; "
        f"sold={summary.is_sold}; inventoried={summary.is_inventoried}; "
        f"excluded={summary.excluded_from_item_view}"
    )
    print(
        f"Planning: policy={summary.replenishment_policy}; source={summary.policy_source}; "
        f"reorder_quantity={summary.reorder_quantity}; minimum_level={summary.minimum_level}; "
        f"standard_cost={summary.standard_cost}"
    )
    print(f"Period: {summary.period_start} to {summary.as_of_date}")
    print(f"Current cover snapshot: {summary.cover_snapshot_captured_at or '-'}")
    _print_activity("Sales all time", summary.sales_all_time)
    _print_activity("Sales period", summary.sales_period)
    _print_activity("Current cover orders", summary.current_cover_orders)
    _print_activity("Purchases all time", summary.purchases_all_time)
    _print_activity("Purchases period", summary.purchases_period)


def _print_customer_summary(summary) -> None:
    print(
        f"Customer: {summary.display_name} "
        f"(record={summary.myob_record_id or '-'}; card={summary.myob_card_id or '-'})"
    )
    print(
        f"Location: {summary.address_line_1 or '-'}, {summary.city or '-'}, "
        f"{summary.state or '-'} {summary.postcode or '-'}"
    )
    print(
        f"Commercial: active={summary.is_active}; payment_basis={summary.payment_basis}; "
        f"freight_payer={summary.freight_payer}; price_level={summary.price_level or '-'}; "
        f"shipping_method={summary.shipping_method or '-'}"
    )
    print(f"Period: {summary.period_start} to {summary.as_of_date}")
    print(f"Current cover snapshot: {summary.cover_snapshot_captured_at or '-'}")
    _print_activity("Sales all time", summary.sales_all_time)
    _print_activity("Sales period", summary.sales_period)
    _print_activity("Current cover orders", summary.current_cover_orders)


def _print_inventory_preview(preview) -> None:
    print("Inventory snapshot preview")
    print(f"File: {preview.source_file_name}")
    print(f"SHA-256: {preview.source_sha256}")
    print(f"Captured at: {preview.captured_at}")
    print(
        f"Rows: {preview.row_count}; matched={preview.matched_item_count}; "
        f"unmatched={len(preview.unmatched_item_numbers)}"
    )
    print(
        f"Totals: on_hand={preview.total_on_hand}; committed={preview.total_committed}; "
        f"on_order={preview.total_on_order}; available={preview.total_available}"
    )
    if preview.unmatched_item_numbers:
        print("Unmatched item numbers:")
        for item_number in preview.unmatched_item_numbers[:25]:
            print(f"- {item_number}")
    print(
        "Already imported: "
        + (str(preview.existing_snapshot_id) if preview.already_imported else "no")
    )


def _print_item_planning(analysis) -> None:
    print(f"Item: {analysis.item_number} — {analysis.item_name}")
    print(
        f"Analysis: {analysis.analysis_start} to {analysis.analysis_end}; "
        f"months={analysis.analysis_months}; sales={analysis.sales_quantity}; "
        f"average_monthly={analysis.average_monthly_sales}"
    )
    if analysis.inventory is None:
        print("Inventory: missing from current snapshot")
    else:
        position = analysis.inventory
        print(
            f"Inventory ({position.captured_at}): on_hand={position.on_hand}; "
            f"committed={position.committed}; on_order={position.on_order}; "
            f"available={position.available}"
        )
    if analysis.commitments is None:
        print("Pools: unavailable")
    else:
        commitments = analysis.commitments
        print(
            f"Commitments: cutoff={commitments.cutoff_date}; "
            f"recent_non_cover={commitments.recent_non_cover}; "
            f"stale_non_cover_ignored={commitments.stale_non_cover_ignored}; "
            f"other_current={commitments.other_current_committed}; "
            f"cover={commitments.current_cover}"
        )
        print(
            f"Pools: physical={commitments.physical_pool}; "
            f"cover_alignment={commitments.cover_inbound_balance}; "
            f"projected={commitments.projected_pool}; "
            f"immediate_shortage={commitments.immediate_shortage}; "
            f"unbacked_cover={commitments.uncovered_cover}"
        )
        print(
            f"MYOB reconciliation: committed_delta="
            f"{commitments.committed_reconciliation_delta}; "
            f"raw_available={analysis.inventory.available if analysis.inventory else '-'}"
        )
    print(
        f"Lead time: {analysis.lead_days} days ({analysis.lead_time_source}); "
        f"lead_demand={analysis.lead_demand}; minimum={analysis.minimum_level}; "
        f"target={analysis.target_stock}"
    )
    print(
        f"Order: raw={analysis.suggested_order_raw}; rounded={analysis.suggested_order}; "
        f"trend_adjustment={analysis.trend.lead_adjustment_rounded}; "
        f"adjusted={analysis.adjusted_suggested_order}; status={analysis.status}"
    )
    print(
        f"Trend {analysis.trend.mode}: current={analysis.trend.current_total}; "
        f"previous={analysis.trend.previous_total}; delta={analysis.trend.delta}; "
        f"percent={analysis.trend.percent_change}; significant={analysis.trend.significant}"
    )
    if analysis.latest_purchase is not None:
        purchase = analysis.latest_purchase
        print(
            f"Latest purchase: {purchase.transaction_date}; supplier={purchase.supplier_name}; "
            f"purchase={purchase.purchase_no}; qty={purchase.quantity}; "
            f"unit_price={purchase.unit_price}; currency={purchase.currency_code or '-'}"
        )
    print("Reasons:")
    for reason in analysis.reasons:
        print(f"- {reason}")
    print("Known gaps:")
    for gap in analysis.data_gaps:
        print(f"- {gap}")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "check-config":
        settings = load_settings(args.config)
        print(f"Configuration is safe: {settings.application_name}")
        print(f"Database target: {settings.database.database}")
        print(f"Operational root: {settings.folders.root}")
        return 0

    if args.command == "setup-dev-database":
        settings = load_settings(args.config)
        result = ensure_development_database(settings)
        print(f"Database {result.database}: {result.status}.")
        upgrade_development_database(
            args.config,
            alembic_config_path=args.alembic_config,
        )
        verification = verify_development_database(settings)
        print(f"Alembic revision: {verification.alembic_revision}")
        print(f"Verified application tables: {len(verification.tables) - 1}")
        print("Windsor Widget v2 development database is ready.")
        return 0

    if args.command == "stage-myob-exports":
        settings = load_settings(args.config)
        requests = load_source_manifest(args.manifest)
        session_factory = None
        engine = None
        if args.commit:
            engine = create_database_engine(settings)
            session_factory = create_session_factory(engine)
        try:
            summary = run_import_pipeline(
                requests,
                commit=args.commit,
                session_factory=session_factory,
                chunk_size=args.chunk_size,
            )
        finally:
            if engine is not None:
                engine.dispose()
        report_path = args.report or (
            Path(settings.folders.exports) / "myob_staging_report.json"
        )
        written_report = write_pipeline_report(summary, report_path)
        print(f"MYOB import mode: {summary.mode}")
        for result in summary.results:
            row_count = "n/a" if result.row_count is None else str(result.row_count)
            issue_count = "n/a" if result.issue_count is None else str(result.issue_count)
            print(
                f"{result.source_type}: {result.status}; "
                f"rows={row_count}; issues={issue_count}"
            )
        print(f"Report: {written_report}")
        if args.commit:
            print("Files were staged for review; no operational master data was changed.")
        else:
            print("Dry run only; no database connection or write was performed.")
        return 0

    if args.command in {
        "preview-inventory-snapshot",
        "commit-inventory-snapshot",
        "inventory-snapshot-status",
    }:
        settings = load_settings(args.config)
        engine = create_database_engine(settings)
        session_factory = create_session_factory(engine)
        try:
            with session_factory() as session:
                try:
                    if args.command == "preview-inventory-snapshot":
                        preview = preview_inventory_snapshot(
                            session,
                            args.source_file,
                            captured_at=args.captured_at,
                        )
                        _print_inventory_preview(preview)
                        print("Preview only; no inventory data was changed.")
                        return 0 if not preview.unmatched_item_numbers else 1

                    if args.command == "inventory-snapshot-status":
                        snapshot = current_inventory_snapshot(session)
                        if snapshot is None:
                            print("No current inventory snapshot exists.")
                            return 0
                        print(f"Snapshot: {snapshot.inventory_snapshot_id}")
                        print(f"Captured at: {snapshot.captured_at}")
                        print(f"Source: {snapshot.source_file_name}")
                        print(f"Rows: {snapshot.row_count}")
                        print(f"Age days: {inventory_snapshot_age_days(snapshot)}")
                        return 0

                    actor = ensure_app_user(
                        session,
                        username=args.username,
                        display_name=args.display_name,
                    )
                    result = commit_inventory_snapshot(
                        session,
                        args.source_file,
                        actor=actor,
                        captured_at=args.captured_at,
                    )
                    session.commit()
                    print(f"Inventory snapshot mode: {result.mode}")
                    print(f"Snapshot: {result.inventory_snapshot_id}")
                    print(f"Captured at: {result.captured_at}")
                    print(f"Source: {result.source_file_name}")
                    print(f"Rows: {result.row_count}")
                    print("Inventory history was retained; one snapshot is marked current.")
                    return 0
                except (InventorySnapshotError, MasterImportError, ValueError) as exc:
                    session.rollback()
                    print(f"Inventory snapshot stopped safely: {exc}")
                    return 1
        finally:
            engine.dispose()

    if args.command in {
        "planning-readiness",
        "item-planning",
        "order-analysis",
    }:
        settings = load_settings(args.config)
        engine = create_database_engine(settings)
        session_factory = create_session_factory(engine)
        try:
            with session_factory() as session:
                try:
                    if args.command == "planning-readiness":
                        readiness = get_planning_readiness(session)
                        print(f"Inventory snapshot: {readiness.inventory_snapshot_id or '-'}")
                        print(f"Inventory captured: {readiness.inventory_captured_at or '-'}")
                        print(f"Inventory source: {readiness.inventory_source_file_name or '-'}")
                        print(f"Inventory rows: {readiness.inventory_row_count}")
                        print(f"Active inventoried items: {readiness.active_inventoried_items}")
                        print(
                            "Active inventoried items with snapshot: "
                            f"{readiness.active_inventoried_items_with_snapshot}"
                        )
                        print(
                            "Active inventoried items missing snapshot: "
                            f"{readiness.active_inventoried_items_missing_snapshot}"
                        )
                        print(f"Preferred supplier links: {readiness.preferred_supplier_links}")
                        print(
                            "Configured supplier lead times: "
                            f"{readiness.configured_supplier_lead_times}"
                        )
                        print(
                            "Current cover-order snapshots: "
                            f"{readiness.current_cover_order_snapshots}"
                        )
                        print("Remaining gaps:")
                        for gap in readiness.gaps:
                            print(f"- {gap}")
                        return 0

                    if args.command == "item-planning":
                        analysis = get_item_planning_analysis(
                            session,
                            args.item_number,
                            analysis_months=args.months,
                            fallback_lead_weeks=args.lead_weeks,
                            trend_mode=args.trend,
                            as_of_date=args.as_of,
                        )
                        _print_item_planning(analysis)
                        return 0

                    analysis = get_order_analysis(
                        session,
                        analysis_months=args.months,
                        fallback_lead_weeks=args.lead_weeks,
                        trend_mode=args.trend,
                        as_of_date=args.as_of,
                        limit=args.limit,
                        include_ok=args.include_ok,
                    )
                    print(
                        f"Order Analysis: {analysis.analysis_start} to {analysis.analysis_end}; "
                        f"inventory={analysis.inventory_captured_at}; "
                        f"considered={analysis.considered_items}; "
                        f"flagged={analysis.flagged_items}; "
                        f"shown={len(analysis.rows)}"
                    )
                    print(
                        "Status	Item	Sales	Avg/Month	SOH	Recent Std	Stale Ignored	"
                        "Other Committed	On Order	Cover	Physical Pool	Cover Alignment	"
                        "Pool	MYOB Available	Target	Suggested	Trend	Adjusted	Reason"
                    )
                    for row in analysis.rows:
                        print(
                            f"{row.status}	{row.item_number}	{row.sales_quantity}	"
                            f"{row.average_monthly_sales}	{row.on_hand}	"
                            f"{row.recent_non_cover_commitments}	"
                            f"{row.stale_non_cover_ignored}	{row.other_current_committed}	"
                            f"{row.on_order}	{row.current_cover_quantity}	"
                            f"{row.physical_pool}	{row.cover_inbound_balance}	"
                            f"{row.projected_pool}	{row.available}	{row.target_stock}	"
                            f"{row.suggested_order}	{row.trend_adjustment}	"
                            f"{row.adjusted_suggested_order}	{row.reason}"
                        )
                    return 0
                except (PlanningLookupError, ValueError) as exc:
                    print(f"Planning query stopped safely: {exc}")
                    return 1
        finally:
            engine.dispose()

    if args.command in {
        "verify-reporting-data",
        "find-items",
        "find-customers",
        "item-summary",
        "customer-summary",
    }:
        settings = load_settings(args.config)
        engine = create_database_engine(settings)
        session_factory = create_session_factory(engine)
        try:
            with session_factory() as session:
                try:
                    if args.command == "verify-reporting-data":
                        counts = get_foundation_counts(session)
                        for field_name in counts.__dataclass_fields__:
                            print(f"{field_name}: {getattr(counts, field_name)}")
                        issues = validate_foundation_counts(counts)
                        if issues:
                            print("Reporting verification failed:")
                            for issue in issues:
                                print(f"- {issue}")
                            return 1
                        print("Reporting data verification passed.")
                        return 0

                    if args.command == "find-items":
                        results = search_items(session, args.query, limit=args.limit)
                        if not results:
                            print("No matching active planning items were found.")
                            return 0
                        for item in results:
                            print(f"{item.item_number}\t{item.item_name}")
                        return 0

                    if args.command == "find-customers":
                        results = search_customers(
                            session, args.query, limit=args.limit
                        )
                        if not results:
                            print("No matching active customers were found.")
                            return 0
                        for customer in results:
                            print(
                                f"{customer.myob_record_id or '-'}\t"
                                f"{customer.myob_card_id or '-'}\t"
                                f"{customer.display_name}\t"
                                f"{customer.city or '-'} {customer.state or '-'}"
                            )
                        return 0

                    if args.command == "item-summary":
                        summary = get_item_summary(
                            session,
                            args.item_number,
                            months=args.months,
                            as_of_date=args.as_of,
                        )
                        _print_item_summary(summary)
                        return 0

                    summary = get_customer_summary(
                        session,
                        args.myob_record_id,
                        months=args.months,
                        as_of_date=args.as_of,
                    )
                    _print_customer_summary(summary)
                    return 0
                except (ReportingLookupError, ValueError) as exc:
                    print(f"Reporting query stopped safely: {exc}")
                    return 1
        finally:
            engine.dispose()

    if args.command in {
        "review-master-imports",
        "approve-master-imports",
        "promote-master-imports",
    }:
        settings = load_settings(args.config)
        engine = create_database_engine(settings)
        session_factory = create_session_factory(engine)
        try:
            with session_factory() as session:
                try:
                    if args.command == "review-master-imports":
                        reviews = review_master_batches(session)
                        if not reviews:
                            print("No staged or approved master import batches were found.")
                            return 0
                        for review in reviews:
                            print(
                                f"{review.source_type}: batch={review.import_batch_id}; "
                                f"status={review.status}; declared_rows={review.row_count}; "
                                f"stored_rows={review.stored_row_count}; issues={review.issue_count}; "
                                f"file={review.source_file_name}"
                            )
                        return 0

                    if args.command == "approve-master-imports":
                        actor = ensure_app_user(
                            session,
                            username=args.username,
                            display_name=args.display_name,
                        )
                        summary = approve_master_batches(session, actor=actor)
                        session.commit()
                        print(
                            f"Approved batches: {len(summary.approved_batch_ids)}; "
                            f"already approved: {len(summary.already_approved_batch_ids)}; "
                            f"accepted rows: {summary.accepted_row_count}"
                        )
                        for batch_id in summary.approved_batch_ids:
                            print(f"approved: {batch_id}")
                        for batch_id in summary.already_approved_batch_ids:
                            print(f"already approved: {batch_id}")
                        print("No transaction batches were approved or promoted.")
                        return 0

                    if args.commit and (not args.username or not args.display_name):
                        parser.error(
                            "promote-master-imports --commit requires --username and --display-name"
                        )
                    actor = None
                    if args.commit:
                        actor = ensure_app_user(
                            session,
                            username=args.username,
                            display_name=args.display_name,
                        )
                    summary = promote_master_batches(
                        session,
                        commit=args.commit,
                        actor=actor,
                    )
                    if args.commit:
                        session.commit()
                    _print_promotion(summary)
                    if args.commit:
                        print("Approved master batches were committed with audit events.")
                    else:
                        print("Preview only; no master data or batch status was changed.")
                    print("Sales, cover-order and purchase transaction batches remain staged.")
                    return 0
                except MasterImportError as exc:
                    session.rollback()
                    print(f"Master import stopped safely: {exc}")
                    return 1
        finally:
            engine.dispose()

    if args.command in {
        "review-transaction-imports",
        "approve-transaction-imports",
        "promote-transaction-imports",
    }:
        settings = load_settings(args.config)
        engine = create_database_engine(settings)
        session_factory = create_session_factory(engine)
        try:
            with session_factory() as session:
                try:
                    if args.command == "review-transaction-imports":
                        reviews = review_transaction_batches(session)
                        if not reviews:
                            print("No staged or approved transaction import batches were found.")
                            return 0
                        for review in reviews:
                            print(
                                f"{review.source_type}: batch={review.import_batch_id}; "
                                f"status={review.status}; declared_rows={review.row_count}; "
                                f"stored_rows={review.stored_row_count}; issues={review.issue_count}; "
                                f"file={review.source_file_name}"
                            )
                        return 0

                    if args.command == "approve-transaction-imports":
                        actor = ensure_app_user(
                            session,
                            username=args.username,
                            display_name=args.display_name,
                        )
                        summary = approve_transaction_batches(session, actor=actor)
                        session.commit()
                        print(
                            f"Approved batches: {len(summary.approved_batch_ids)}; "
                            f"already approved: {len(summary.already_approved_batch_ids)}; "
                            f"accepted rows: {summary.accepted_row_count}"
                        )
                        for batch_id in summary.approved_batch_ids:
                            print(f"approved: {batch_id}")
                        for batch_id in summary.already_approved_batch_ids:
                            print(f"already approved: {batch_id}")
                        print("No transaction data was promoted during approval.")
                        return 0

                    if args.commit and (not args.username or not args.display_name):
                        parser.error(
                            "promote-transaction-imports --commit requires "
                            "--username and --display-name"
                        )
                    actor = None
                    if args.commit:
                        actor = ensure_app_user(
                            session,
                            username=args.username,
                            display_name=args.display_name,
                        )
                    summary = promote_transaction_batches(
                        session,
                        commit=args.commit,
                        actor=actor,
                    )
                    if args.commit:
                        session.commit()
                    _print_transaction_promotion(summary)
                    if args.commit:
                        print(
                            "Approved transaction batches were committed with "
                            "batch audit events and row lineage."
                        )
                    else:
                        print(
                            "Preview only; no transaction data or batch status was changed."
                        )
                    return 0
                except TransactionImportError as exc:
                    session.rollback()
                    print(f"Transaction import stopped safely: {exc}")
                    return 1
        finally:
            engine.dispose()

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
