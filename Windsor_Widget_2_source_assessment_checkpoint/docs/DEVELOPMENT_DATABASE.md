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

## Create the initial schema

After the database and identity have been created by the SQL Server
administrator:

```powershell
$env:WINDSOR_WIDGET_V2_CONFIG = "config/development.local.json"
alembic upgrade head
```

The first migration contains governance infrastructure only:

- application users;
- immutable audit events;
- import batches and raw source rows;
- import review issues; and
- explicit match candidates and approvals.

Customer, item, supplier, order and shipment business tables deliberately do
not appear in this migration. They will be introduced only after their source
contracts have been exercised against representative MYOB exports.

## Home and work development

Use the same Git repository on both computers, but create a separate local
configuration and a separate local development database on each computer. Run
the same Alembic migrations on both databases. Do not copy live database files
between locations and do not commit configuration secrets or imported business
data.
