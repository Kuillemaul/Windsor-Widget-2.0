# Inventory snapshot and planning workflow

## Purpose

Order Analysis needs a current inventory position. The MYOB item master does not contain
On Hand, Committed, On Order or Available. Those values come from the MYOB **Analyse
Inventory [Summary]** workbook and are stored as immutable snapshots.

The initial source workbook contains 4,152 balanced item rows. Each row must satisfy:

`MYOB Available = On Hand - MYOB Committed + On Order`

Every item number must resolve exactly to the committed v2 item master before the snapshot
can be committed.

## Safety model

- Preview parses, balances and links the workbook without writing data.
- Commit requires an application user.
- A SHA-256 hash prevents the same workbook content from being imported twice.
- Historical snapshots remain unchanged.
- Exactly one snapshot is marked current.
- An append-only audit event records the commit.

## Revised planning model

MYOB Available is retained for reconciliation but is **not** the operational stock pool.
MYOB Committed mixes current cover orders, recent standard sales orders and stale standard
sales orders.

The planning model separates four different concepts:

- **Actual demand history**: invoiced sales only (`Sale Status = I`). This is the only
  input to monthly sales averages, 3v3/6v6/YoY trends and lead-time demand.
- **Recent standard commitments**: active non-cover sales orders (`Sale Status = O`) no
  more than three calendar months old. These reduce immediately usable physical stock but
  are not counted again as historical demand.
- **Current customer cover orders**: standing coverage quantities that are drawn down when
  customers actually purchase. They are displayed and compared with YU/supplier cover,
  but they do not increase forecast demand and do not reserve stock a second time.
- **YU/supplier On Order**: standing inbound coverage/supply, normally available in roughly
  three to six weeks. It contributes to projected stock but does not increase demand.

Stale non-cover sales orders older than three calendar months are displayed and ignored.

Calculated positions:

`Physical Pool = On Hand - Recent Standard Commitments - Other Current Commitments`

`Projected Pool = Physical Pool + On Order`

`Cover Alignment = On Order - Current Customer Cover Orders`

A positive unexplained residual in MYOB Committed is retained as **Other Current
Commitments** rather than silently released. A negative reconciliation difference is shown
as a data warning and never converted into extra stock.

Cover Alignment is a separate service-level check:

- negative means customer cover exceeds the matching YU/supplier cover position;
- positive means YU/supplier cover exceeds customer cover;
- it can create a warning, but it does not alter average demand or suggested replenishment.

Suggested replenishment is based on regular invoiced-sales patterns:

`Target Stock = greater of Lead-Time Invoiced Demand and MYOB Minimum Level`

`Suggested Replenishment = Target Stock - Projected Pool`

The result is rounded to the MYOB Reorder Quantity when one exists.

Trend modes:

- `3v3`: last 3 completed months versus the previous 3
- `6v6`: last 6 completed months versus the previous 6
- `yoy`: last 12 completed months versus the previous 12

Dated inbound/container ETA data is deliberately not guessed. On Order is currently treated
as a three-to-six-week item-level supply bucket. Exact YU purchase-order allocation and
arrival risk remain a later shipment/container phase.
