# Transaction import workflow

This phase promotes three already-staged MYOB sources:

- `sales_transactions`
- `cover_order_snapshot`
- `purchase_transactions`

The workflow is deliberately explicit:

1. **Review** confirms exactly one clean, uncommitted batch for each source.
2. **Approve** changes only staging statuses and records approval audit events.
3. **Preview** maps every row to exact customer, supplier and item master keys without writing operational data.
4. **Commit** writes all operational transaction records in one database transaction, creates one immutable cover-order snapshot, records row-to-line lineage, commits the three import batches and writes batch audit events.

Sales and purchase documents are keyed only by exact MYOB Record ID plus document number. Transaction lines are ordered within each document using their MYOB export order. A later overlapping export updates matching document line positions rather than duplicating them. Missing master references stop the whole promotion; names are never used as substitute keys.

Cover-order exports are point-in-time snapshots. A new committed snapshot marks the previous snapshot non-current but never deletes or overwrites it.
