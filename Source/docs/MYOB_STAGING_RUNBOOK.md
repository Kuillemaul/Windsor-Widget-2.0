# MYOB export staging runbook

This workflow loads declared MYOB exports into review-first staging. It does not
create customers, suppliers, items, sales, purchases, forecasts, orders or
shipments. Promotion into those operational records remains a later, reviewed
step.

## One-time preparation

From the activated virtual environment in the repository's `source` folder:

```powershell
Copy-Item config\myob_sources.example.json config\myob_sources.local.json
```

Edit `config\myob_sources.local.json` so every `path` points to the corresponding
export on the work PC. Keep one declaration per source type:

| Source type | Expected MYOB export | Purpose |
|---|---|---|
| `item_master` | Item list | Item identity and stocked/non-stocked source data |
| `customer_master` | Customer cards | Customer accounts and group-match evidence |
| `supplier_master` | Supplier cards | Supplier identity evidence |
| `sales_transactions` | Item sales history | Demand history and invoice interrogation |
| `cover_order_snapshot` | Open sales orders | Cover commitments identified from `Journal Memo` |
| `purchase_transactions` | Item purchases/bills | Supplier history and inbound/MTO evidence |

The example manifest uses paths beneath
`C:\WindsorWidget2\DEV\Watched`. The manifest is local-only and should not be
committed.

## Gate 1: inspect without touching SQL Server

Run the wrapper without `-Commit`:

```powershell
.\scripts\stage_myob_exports.ps1 `
  -Config config\development.local.json `
  -Manifest config\myob_sources.local.json
```

This reads and hashes every declared file, validates its header, streams all
rows, counts review issues and writes a JSON report to:

```text
C:\WindsorWidget2\DEV\Exports\myob_staging_report.json
```

No database connection or database write occurs in this mode. Review the
per-file status and issue count before continuing.

## Gate 2: stage for review

When the selected files and dry-run counts are correct, rerun with the explicit
commit switch:

```powershell
.\scripts\stage_myob_exports.ps1 `
  -Config config\development.local.json `
  -Manifest config\myob_sources.local.json `
  -Commit
```

Each file is staged in its own transaction. An invalid later file does not undo
an earlier completed file. The exact combination of source type and file hash is
duplicate-protected, so rerunning the same manifest reports `duplicate` rather
than loading a second copy.

Rows with incomplete natural keys or malformed columns remain
`review_required`. Clean rows remain `parsed`. Neither status is approval, and
neither modifies an operational master table.

## Cover-order rule

The open sales-order export is staged separately as `cover_order_snapshot`.
Downstream processing will identify cover orders from `Journal Memo` containing
`COVER ORDER`. These are customer commitments, not an additional forecast.

## Control-item rule

Item numbers beginning with `/` or `\` remain in staged transaction source data
for complete invoice interrogation. They will be excluded only from item
planning views.

## Safe rerun after correction

If a source file needs correction, export or copy a corrected file and run the
dry-run gate again. Its new content hash allows it to be staged as a new batch;
the prior batch remains available for audit and is not overwritten.
