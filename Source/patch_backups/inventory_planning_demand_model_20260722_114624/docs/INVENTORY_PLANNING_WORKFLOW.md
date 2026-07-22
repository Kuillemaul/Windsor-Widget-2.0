# Inventory snapshot and planning workflow

## Purpose

Order Analysis needs a current inventory position. The MYOB item master does not contain
On Hand, Committed, On Order or Available. Those values come from the MYOB **Analyse
Inventory [Summary]** workbook and are stored as immutable snapshots.

The current source workbook contains 4,152 balanced item rows. Each row must satisfy:

`Available = On Hand - Committed + On Order`

Every item number must resolve exactly to the committed v2 item master before the snapshot
can be committed.

## Safety model

- Preview parses, balances and links the workbook without writing data.
- Commit requires an application user.
- A SHA-256 hash prevents the same workbook content from being imported twice.
- Historical snapshots remain unchanged.
- Exactly one snapshot is marked current.
- An append-only audit event records the commit.

## Planning model

The first planning read model uses completed calendar months only. The default is the last
12 completed months and a 14-week fallback lead time, matching the existing v1 control.

The inventory snapshot's Available value is authoritative:

`Available = On Hand - Committed + On Order`

Current cover-order quantity is shown as a reconciliation against MYOB Committed and is not
added again. Suggested order is based on the greater of lead-time demand and MYOB Minimum
Level, less Available, rounded to the MYOB Reorder Quantity when one exists.

Trend modes:

- `3v3`: last 3 completed months versus the previous 3
- `6v6`: last 6 completed months versus the previous 6
- `yoy`: last 12 completed months versus the previous 12

Dated inbound/container ETA data is deliberately not guessed. At-risk timing remains a
known gap until the shipment and container foundation is built.
