# Yuchang packing-data import contract

## Approved first-pass scope

Only workbook rows whose supplier unit is exactly one of the following after
case/whitespace normalisation are eligible:

- Roll
- Rolls
- Spool
- Large Spool

The row must also have:

- one unique Windsor item number in `Sheet1` column A;
- one unique matching Widget item;
- a positive roll/spool length;
- positive metres per carton; and
- a whole positive number of supplier units per carton.

Pallet fields and FOB price are not imported in this stage.

## Storage

Packing values are stored on `ItemSupplier`, not directly on `Item`, because
packing is supplier-specific.

## Safety

The sync command is preview-only unless `--commit` is supplied.

- Empty optional workbook text does not erase existing values.
- `packing_source='user'` is preserved.
- Rejected supplier links are preserved.
- New links are approved but never set as preferred.
- Every committed create/update writes an `AuditEvent`.
- The source workbook, worksheet and current row are retained.

## Source separation for Bring In

`ITEMPUR.TXT` remains the Purchase Orders/status-O source for the future Bring
In on-order pool. `ITEMPURbills.TXT` remains bills/history only.
