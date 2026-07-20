# Source-data assessment and proposed business entities

Status: **business rules approved; development foundation applied**.

The approved foundation migrations `0001_stage1_foundation` and
`0002_master_data` have been applied to the isolated
`WindsorWidgetV2_DEV` database. The controlled MYOB staging workflow is ready
for dry-run validation and review-first staging. No production database or
production data is changed by this work.

## System-of-record boundary

- MYOB remains authoritative for accounting and stock on hand.
- Windsor Widget 2.0 will be authoritative for operational manufacturing
  orders, FIFO allocations, planned shipments and shipment status.
- Imported MYOB files are immutable source evidence. Every file, row, issue,
  proposed match and approval is retained through the review-first import
  foundation.
- Widget-maintained fields such as customer payment basis, freight payer,
  replenishment policy and lead-time overrides are not silently overwritten by
  a later MYOB export.

## Source inventory

| Source | Type | Principal business entity | Candidate natural key | Primary risks |
|---|---|---|---|---|
| `ITEMMasterData.TXT` | Master data | Item | `Item Number` | MYOB uses coded markers; primary supplier is a name; control item numbers containing `/` or `\` must remain available on documents but stay out of planning views. |
| `CUSTOMERDATA.TXT` | Master data | Customer account | `Record ID`, with `Card ID` as an external identifier | A commercial customer may have several MYOB accounts; names are not a safe group key; payment and freight rules require Widget confirmation. |
| `SUPPLIERS.TXT` | Master data | Supplier | `Record ID`, with `Card ID` as an external identifier | Supplier names can change or be made inactive; item-to-supplier links need corroboration. |
| `salesdata.TXT` | Transaction history | Sale/invoice and sale line | `Record ID` plus source-row hash | An item can repeat on one invoice; returns/credits and embedded line breaks require careful parsing. Cover-order lines must not be counted as ordinary forecast demand. |
| `SALESORDERSFORCOVERORDER.TXT` | Open transaction snapshot | Sales order and sales-order line | `Record ID` plus source-row hash | The presence of a sales order is not necessarily new demand; cover orders are identified from `Journal Memo`. |
| `ITEMPUR.TXT` | Purchase transaction/history | Purchase/bill/order and purchase line | `Record ID` plus source-row hash | A supplier/item relationship inferred from history is a proposal; partially received and open orders can be confused if status fields are ignored. |
| `zinvs1.xlsx` | Point-in-time snapshot | Inventory balance by item | `Item No.` within report date/file | Report date is not embedded in the visible table; values become stale and must always display an as-at timestamp. |
| `Cust File Path.xlsx` / `Customer list Full` | Reference master | Customer account reference | Normalized name plus address evidence | It has no durable MYOB record identifier and must not overwrite the MYOB customer master. |
| `Cust File Path.xlsx` / `FILES` | Reference list | Customer-group price-file link | Normalized full file path | File names are inconsistent, obsolete `.xls` files coexist with `.xlsx`, and several accounts may share one group file. Non-exact matches need approval. |
| Customer price workbooks, for example `beard a h(1).xlsx` | Semi-structured reference | Customer group notes, contacts and pricing reference | Approved customer-group/file-path link | Layouts vary by customer and worksheet. The first release opens the approved workbook; it does not treat workbook cells as structured truth. |
| `myob_po_260716(1).txt` | Outbound interface/projection | MYOB running supplier PO | Supplier plus Widget projection version | It is a replaceable MYOB projection, not the operational order ledger. Delete/re-import must be deliberate and auditable. |
| Supplier packing lists and invoices | Transaction documents | Shipment supplier document and shipment lines | Supplier document number plus supplier | One Windsor shipment may include several suppliers and therefore several invoice/packing-list pairs. Supplier item references may be missing or ambiguous. |
| Pallet details | Reference document | Shipment handling reference | Shipment plus supplier document | Useful for storage/breakdown decisions, but not authoritative for quantities or value. |
| Forwarder emails/documents | Event/document evidence | Shipment booking and shipment event | Windsor shipment number plus external reference | Email subjects and external booking references are inconsistent; a Windsor shipment number is mandatory once information is sent externally. |

## Measured validation of the supplied MYOB exports

The streaming parser read every supplied text export without loading the full
sales history into memory. Peak measured Python memory was approximately
4.04 MB while validating the 160,211-row sales file. All six files use a
UTF-8 byte-order mark and place their detected header on row 2.

| Source | Header columns | Data rows | Rows requiring review | Natural-key issues within those rows |
|---|---:|---:|---:|---:|
| `ITEMMasterData.TXT` | 68 | 8,070 | 7 | 0 |
| `SALESORDERSFORCOVERORDER.TXT` | 52 | 27,574 | 6 | 6 |
| `salesdata.TXT` | 52 | 160,211 | 109 | 98 |
| `ITEMPUR.TXT` | 42 | 27,172 | 14 | 10 |
| `CUSTOMERDATA.TXT` | 124 | 3,565 | 28 | 4 |
| `SUPPLIERS.TXT` | 125 | 520 | 0 | 0 |

The structural exceptions are sparse but real. Sample rows contain unescaped
quotes or embedded record text that changes the apparent column count. The
importer therefore quarantines these rows for review and never pads, shifts or
commits them as trusted business facts. The source row, row number and hash are
still retained as evidence. A natural key is also considered invalid when only
some of its required components are populated. `Item Number` is an optional
transaction-key discriminator because legitimate MYOB document/comment lines
can leave it blank; `Record ID` and the document number remain required.

## Field-level mappings currently approved for the master-data slice

All unlisted source columns remain available in raw import staging. They are
not discarded, but they are not promoted into durable business fields until a
workflow requires them.

### Item master

| MYOB field | Widget field/use | Rule |
|---|---|---|
| `Item Number` | `items.item_number` | Required and unique. Retain `/` and `\` item numbers for invoice detail; flag them `excluded_from_item_view`. |
| `Item Name` | `items.item_name` and normalized search name | Required. Normalization is for searching, never for identity. |
| `Buy`, `Sell`, `Inventory` | `is_bought`, `is_sold`, `is_inventoried` | MYOB codes `B`, `S`, and `I` respectively. |
| `Inactive Item` | `is_active` | `Y` means inactive. |
| `Description` | `description` | Optional descriptive text. |
| `Buy Unit Measure`, `Sell Unit Measure` | Unit fields | Optional; mismatched units require later conversion rules before arithmetic. |
| `Reorder Quantity`, `Minimum Level`, `Standard Cost` | Planning reference fields | Imported as numeric evidence; they do not determine the forecast policy by themselves. |
| `Primary Supplier` | Proposed item-supplier match | Exact unique supplier name can be proposed. Anything else needs review. |
| `Supplier Item Number` | `item_suppliers.supplier_item_number` | Applied only after the supplier match is approved. |

The replenishment policy is Widget-owned: `stocked`, `make_to_order`,
`manual`, or `unknown`. MYOB non-inventoried status is useful evidence but is
not sufficient on its own to set `make_to_order`.

### Customer master

| MYOB field | Widget field/use | Rule |
|---|---|---|
| `Record ID` | `customer_accounts.myob_record_id` | Preferred external identity. |
| `Card ID` | `customer_accounts.myob_card_id` | Secondary external identity. |
| `Co./Last Name` | Display and normalized search name | Required; not a safe group key. |
| `Card Status` | Card status and active flag | In supplied exports, `N` is active and `Y` is inactive. |
| `Addr 1 - Line 1`, `Addr 1 - City`, `Addr 1 - State`, `Addr 1 - Postcode` | Account address | Used as grouping/matching evidence and display data. |
| `Addr 1 - Contact Name`, `Addr 1 - Email`, `Addr 1 - Phone No. 1` | Contact display | Optional. |
| `Terms - Payment is Due`, `Price Level`, `Shipping Method` | Account reference | Displayed as imported evidence. |
| Widget toggle: payment basis | `payment_basis` | `unknown`, `prepay`, or `account`; user-maintained. |
| Widget toggle: freight payer | `freight_payer` | `unknown`, `customer`, or `windsor`; user-maintained. |

Customer grouping is a reviewed relationship between one commercial
`customer_group` and one or more `customer_accounts`. The customer list/path
workbook supplies match evidence, not automatic authority. Price files attach
to the customer group because state accounts generally share the same file.

### Supplier master and item-supplier relationship

| MYOB field/evidence | Widget field/use | Rule |
|---|---|---|
| `Record ID`, `Card ID`, `Co./Last Name`, `Card Status` | Supplier identity and display | Same status interpretation as customer cards. |
| Contact/email/phone fields | Supplier contact display | Optional. |
| Item `Primary Supplier` | `item_suppliers` proposal | Accept automatically only when the normalized name resolves to one exact supplier. |
| Purchase history | Recent-supplier proposal and last-purchase facts | Prefer the most recent purchase when MYOB primary supplier is absent; non-unique or contradictory results require approval. |
| Supplier defaults | Manufacturing, transit and buffer days | Widget-maintained defaults. |
| Item-supplier overrides | MOQ, manufacturing, transit and buffer days | Optional item-level values override supplier defaults. |

### Sales, cover orders and purchase history

| Source field | Promoted meaning | Rule/risk |
|---|---|---|
| `Invoice No.` / `Purchase No.` | Document reference | Preserve punctuation exactly as exported. |
| `Date` | Transaction date | Parse with the source locale; retain original text for audit. |
| `Item Number`, `Quantity`, `Price`, `Total` | Transaction-line facts | `/` and `\` control lines remain visible in document drill-down but are excluded from item planning totals. |
| `Card ID`, `Co./Last Name`, `Record ID` | Account/supplier and external document evidence | Resolve by durable identifier first; name matching is reviewable evidence. |
| `Journal Memo` | Cover-order classification evidence | Case-insensitive `COVER ORDER` marks a cover order. The supplied example contains `Sale; Comfort Sleep Bedding Company - COVER ORDER`. |
| `Delivery Status`, `Sale Status` / `Purchase Status`, `Received`, `Billed` | Document state evidence | Required to distinguish open, delivered, partial, received and historical lines. Exact state mapping is deferred until transaction models are reviewed. |

Cover-order demand rule: open cover-order quantities are displayed as
commitments but are not added a second time to the statistical sales forecast.
Historical invoiced consumption remains part of sales history.

## Matching and inference gates

The following rules may create proposals but must not silently create an
approved relationship unless the result is uniquely exact:

1. customer account to customer group;
2. customer group to price workbook;
3. item to supplier;
4. item replenishment policy inferred as make-to-order; and
5. supplier document item to Windsor item.

Make-to-order inference will compare purchase and sales lines for the same item
in close date/quantity proximity. It is only a starting proposal. A manual
Widget policy is authoritative and remains editable with audit history.

## Missing or deferred fields needed by later workflows

These facts are not reliably available from the assessed sources and therefore
must be entered, derived with review, or supplied by later documents:

- explicit commercial customer-group identifier;
- authoritative customer payment basis and freight payer;
- definitive replenishment policy and make-to-order customer relationship;
- supplier and item-specific manufacturing, transit and buffer lead times;
- supplier MOQ where not represented in MYOB;
- forecast method, seasonality confidence, safety stock and target arrival date;
- manufacturing control number and FIFO allocation history;
- planned-shipment allocation versus physically shipped allocation;
- Windsor shipment number, forwarder booking, container, vessel, ETD and ETA;
- supplier packing-list/invoice line mapping to Windsor item number;
- inventory snapshot as-at timestamp; and
- arrival confirmation and MYOB stock-import completion evidence.

## Approved master entities in migration `0002_master_data`

- `customer_groups`
- `customer_accounts`
- `customer_price_files`
- `suppliers`
- `items`
- `item_suppliers`

This applied migration intentionally does **not** create sales, forecast,
manufacturing-order, allocation, shipment, document or inventory-snapshot
tables. Those transaction entities will follow in later reviewed migrations.

## Current implementation status

The following decisions are approved and reflected in the current contracts:

1. the system-of-record boundary;
2. the six proposed master entities;
3. the exact master-field mappings above;
4. the cover-order and control-item rules; and
5. the review gates for customer grouping, price files, item suppliers and
   make-to-order inference.

Both approved migrations are now installed in `WindsorWidgetV2_DEV`. The next
controlled step is to dry-run the six supplied MYOB exports, inspect the
quarantined rows, and then stage accepted source evidence. Staging does not
promote rows into customer, supplier or item master tables; promotion remains
a separate reviewed action.
