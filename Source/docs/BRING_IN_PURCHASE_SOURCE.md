# Bring In purchase-source contract

## Source separation

- `ITEMPUR.TXT` is the Purchase Orders export. It is the authoritative MYOB source for active on-order quantities and should contain Purchase Status `O` rows.
- `ITEMPURbills.TXT` is the Bills export. It is the source for actual purchase/receipt history, supplier behaviour and historical cost. It must not populate the Bring In on-order pool.

## Bring In pool

For a selected supplier, the future Bring In screen must show every active Purchase Order line from `ITEMPUR.TXT`, including lines that have not been manually selected for the next shipment.

Widget-created Manufacture Orders appear immediately. When the matching MYOB Purchase Order is imported, the two records are reconciled by supplier, original order number and item number rather than double-counted.

Available quantity for a new shipment draft is calculated from the current MYOB open quantity, less quantities already finalised/shipped or allocated to other active Widget drafts.

This document records the agreed source contract only. The Yuchang packing preview stage does not yet change the Bring In screen.
