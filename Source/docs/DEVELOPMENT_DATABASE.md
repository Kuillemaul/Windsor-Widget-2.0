# Development database setup

Windsor Widget 2.0 must use a dedicated SQL Server database. The application
will refuse to start unless the configured database name is exactly
`WindsorWidgetV2_DEV` and every operational folder contains a Widget 2 marker.

## Recommended database identities

Use two identities with separate responsibilities:

- `WindsorWidgetV2_Migrator` — schema migrations only; may create and alter
  objects inside `WindsorWidgetV2_DEV`.
- `WindsorWidgetV2_App` — normal application use; may read and write application
  data but may not alter the schema.

Neither identity should be mapped to the Windsor Widget v1 database. This is a
hard safety boundary, not merely a configuration convention.

Windows authentication is suitable for local development when the developer's
Windows account has access only to the v2 development database. SQL
authentication is also supported, but credentials must be supplied through the
environment variables named in the local configuration file.

## Configuration

1. Copy `config/development.example.json` to
   `config/development.local.json`.
2. Set the SQL Server host and the v2-only folder paths.
3. Keep `development.local.json` out of Git.
4. Run:

   ```powershell
   windsor-widget check-config config/development.local.json
   ```

The command validates safety rules without connecting to SQL Server or printing
credentials.

## Create and verify the development database

Prerequisites on the work PC are Python with this project installed, Microsoft
ODBC Driver 18 for SQL Server, and a Windows or SQL login allowed to create this
one development database. The approved command is:

```powershell
.\scripts\setup_dev_database.ps1 -Config config\development.local.json
```

The command is intentionally non-generic. It can only:

1. connect to SQL Server's `master` catalogue;
2. check for the exact name `WindsorWidgetV2_DEV`;
3. create that database when it does not exist;
4. apply the committed Alembic migrations; and
5. verify the connected database name, migration revision and tables.

It contains no database drop, rename or overwrite operation and has no code
path that connects to Windsor Widget v1. Re-running it is safe: an existing
`WindsorWidgetV2_DEV` database is migrated and verified rather than recreated.

The Stage 1 import schema deliberately has one SQL Server cascade route from an
import batch to its issues. Deleting a batch removes its issues and rows. A raw
SQL delete of an individual import row is blocked while issues refer to it; the
application deletes those issues explicitly when an individual row is removed.

For SQL authentication, first set the credential environment variables named
by the local configuration. Credentials are never stored in configuration or
printed:

```powershell
$env:WINDSOR_WIDGET_V2_DB_USERNAME = "<v2 database setup login>"
$env:WINDSOR_WIDGET_V2_DB_PASSWORD = "<password>"
.\scripts\setup_dev_database.ps1
```

The current migrations create:

- application users;
- immutable audit events;
- import batches and raw source rows;
- import review issues; and
- explicit match candidates and approvals;
- customer groups, accounts and price-file links; and
- supplier, item and item-supplier master data.

If the command reports a connection or driver error, do not substitute the v1
database name. Correct the server, driver or v2-only login and run it again.

## Home and work development

Use the same Git repository on both computers, but create a separate local
configuration and a separate local development database on each computer. Run
the same Alembic migrations on both databases. Do not copy live database files
between locations and do not commit configuration secrets or imported business
data.
