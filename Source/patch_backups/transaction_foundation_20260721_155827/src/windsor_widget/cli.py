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

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
