# Windsor Widget 2.0

Windsor Widget 2.0 is a clean rebuild of the operational planning application. It is intentionally isolated from Windsor Widget v1 and its production database.

## Safety boundary

- Repository: this repository only; v1 is reference material and is not imported as application code.
- Development database: `WindsorWidgetV2_DEV` only.
- Configuration: local files are created from checked-in examples and are never committed.
- Folders: all watched, archive, failed and export folders must be within a path clearly named for Windsor Widget 2.
- Credentials: passwords are supplied through environment variables, never JSON or source code.
- Imports: source files are staged and reviewed before operational data is committed.

The application refuses to start if its development configuration points at a differently named database or folders that do not satisfy the v2 isolation marker.

## Development setup

1. Install Python 3.11 or newer and Microsoft ODBC Driver 18 for SQL Server.
2. Create and activate a virtual environment.
3. Install the project with `pip install -e ".[dev]"`.
4. Copy `config/development.example.json` to `config/development.json`.
5. Change the local SQL Server name and folder root. Keep the database name `WindsorWidgetV2_DEV`.
6. Review and explicitly approve `docs/SOURCE_CONTRACTS.md` and the proposed
   master entities. Do **not** create the database before that approval.
7. After approval, create the database with a login that has no access to the
   v1 database.
8. Run `alembic upgrade head`.
9. Run `pytest`.

Work and home can use different local server and folder values in their uncommitted `development.json` files. Both environments use the same migrations and application code from Git.

## Current status

Migration `0001_stage1_foundation` provides the review and audit foundation:

- application users;
- immutable audit events;
- import batches and exact raw source rows;
- reviewable data-quality issues; and
- explicit match candidates and approval decisions.

Migration `0002_master_data` proposes customer groups/accounts, customer price
files, suppliers, items and item-supplier relationships. It has been validated
offline but not applied. The supplied MYOB text exports can be parsed in a
bounded-memory review-first workflow; malformed rows are quarantined rather
than silently accepted.

Manufacturing orders, FIFO allocations, forecasts, shipments and operational
documents remain later transaction slices. No development database will be
created until the source contracts, the measured matching assessment in
`docs/SOURCE_MATCH_ASSESSMENT.md`, and the master-data proposal are approved.

The current supplied reference exports now produce deterministic review-only
master proposals and CSV approval queues under `reports/source_assessment`.
No SQL Server connection is used by that assessment.
