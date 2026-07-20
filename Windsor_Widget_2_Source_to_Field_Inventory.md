# Windsor Widget 2.0 — Source-to-Field Inventory and Architecture Gate

**Assessment date:** 20 July 2026  
**Status:** Approved to begin the Stage 1 foundation; database and application code are not yet created  
**Scope:** Windsor Widget 2.0 only. Windsor Widget v1 source is reference material, not a schema or code base to copy.

## 1. Outcome

The uploaded files are sufficient to define the core Windsor Widget 2.0 entities and the import boundary without copying the v1 database.

The key architectural decision is:

- **The Widget owns operational truth** for demand decisions, factory/manufacture orders, control numbers, remaining supplier quantities, FIFO allocations, container planning, shipment tracking, documents, communications, and audit history.
- **MYOB remains accounting and current-stock truth.** Its exports supply item, customer, supplier, sales, cover-order, purchase-order and inventory data.
- **MYOB purchase orders are a replaceable projection** of the Widget's remaining supplier orders: one running PO per supplier is deleted and re-imported from a reviewed Widget export.
- **Arriving a shipment closes it in the Widget.** Office staff handle discrepancies and the actual MYOB stock change using the existing MYOB stock-import process.
- **Every import is staged, validated, reviewed and auditable.** Ambiguous item, customer, supplier, order, control-number or shipment matches are never guessed.

The supplied files and confirmed business rules are now sufficient to define the entities, import boundaries and review queues. The Stage 1 architecture gate is passed. The remaining operational setup items in section 12 do not block creation of the isolated development schema.

## 2. Module status

The two statuses below avoid confusing a settled workflow with completed software. No Windsor Widget 2.0 application module has been implemented yet.

| Module | Definition status | Implementation status | Current position |
|---|---|---|---|
| Source assessment and import boundary | **Finished for Stage 1** | Not started | All current examples inventoried; quality risks, ownership and review rules identified |
| Customer enquiry | **Started** | Not started | Search/autocomplete, group accounts, sales history, monthly graphs, last price, invoice drill-down, account/prepay and freight-payer toggles, and price-file link agreed |
| Cover-order classification | **Finished at rule level** | Not started | Normalized Journal Memo ending `- COVER ORDER` is the deterministic rule; raw evidence is retained |
| Customer grouping and price-file matching | **Finished at rule level** | Not started | MYOB accounts remain separate; Widget groups link state/site accounts and shared price files through reviewed evidence |
| Item enquiry | **Started** | Not started | Stock, on order, planned, shipped, sales/customer history, forecast, trend, seasonality and Christmas-arrival view agreed |
| Demand and forecasting | **Started** | Not started | Hybrid history/cover/manual model agreed; exact model and confidence rules await historical testing |
| Made-to-order handling | **Finished at rule level** | Not started | Non-stocked bespoke items require an actual customer order; supplier MOQ becomes the customer order quantity |
| Item-to-supplier matching | **Finished at rule level** | Not started | Exact bill/purchase history proposes suppliers, most recent first; non-exact matches require approval |
| Supplier enquiry | **Started** | Not started | Reverse-customer view agreed; supplier defaults and item-specific lead-time overrides required |
| Manufacture/factory orders | **Finished at rule level** | Not started | Widget order/control number, multi-item orders, remaining quantity and cancellation history agreed |
| FIFO allocation | **Finished at rule level** | Not started | Container quantities consume the oldest eligible supplier-order line first; allocations remain traceable |
| Container planning/finalisation | **Finished at rule level** | Not started | Proposed and final packed quantities remain separate; partial quantities and multiple suppliers supported |
| Shipments and bookings | **Finished at rule level** | Not started | One Windsor shipment per travelling container/consignment; one forwarder booking; multiple supplier documents allowed |
| Email and document tracking | **Started** | Not started | Shipment number required once anything is sent externally; draft/manual `.msg` capture first, API optional later |
| Arrival/receiving boundary | **Finished at rule level** | Not started | Widget records arrival and closes shipment; no warehouse discrepancy workflow or direct stock mutation |
| MYOB running PO replacement | **Started** | Not started | Exact 42-field import example inspected; preview, delete/re-import, reconciliation and audit controls still to build |
| Security, users and audit | Not started | Not started | Required in Stage 1 foundation |
| Environment isolation/configuration | **Started** | Not started | Separate repo/database/folders/executable agreed; runtime configuration and start-up protection not built |
| Database and migrations | **Approved to begin** | Not started | The design gate is passed; implementation remains isolated to the v2 development database |
| PySide6 navigation shell and UI | Not started | Not started | Build after workflow/entities and source contracts are accepted |

## 3. Source catalogue

| Source | Type | Business authority | Expected handling |
|---|---|---|---|
| `ITEMMasterData.TXT` | Master-data export | MYOB item identity and accounting attributes | Replace/upsert current item attributes; preserve Widget-managed planning settings |
| `CUSTOMERDATA.TXT` | Master-data export | MYOB customer account/site identity and contact details | Replace/upsert accounts; map accounts to Widget customer groups |
| `Cust File Path.xlsx` | Reference index | Customer/site list and customer price-file paths | Stage both sheets; propose account groups and shared price-file links; prefer current `.xlsx` files outside `old` folders; review ambiguous matches |
| `SUPPLIERS.TXT` | Master-data export | MYOB supplier identity and contact details | Replace/upsert suppliers; retain Widget lead-time settings |
| `salesdata.TXT` | Transaction export | Historical invoiced sales | Append/idempotently reload transaction lines for enquiry and forecasting |
| `SALESORDERSFORCOVERORDER.TXT` | Open transaction/snapshot export | Current MYOB sales orders, including cover orders | Snapshot current open orders; classify cover orders from approved memo/comment rules |
| `ITEMPUR.TXT` | Purchase transaction/snapshot export | MYOB purchase orders and purchase history | Reconcile MYOB running POs to Widget projections; not the Widget's order-line authority |
| `zinvs1.xlsx` | Inventory snapshot | Current MYOB stock position | Snapshot `On Hand`, `Committed`, `On Order`, `Available` by item and import time |
| Customer price workbook (`beard a h(1).xlsx`) | Semi-structured reference | Human-maintained customer-group prices, sites, contacts and notes | Link/version as a document first; controlled mapping/import later, never unattended |
| MYOB PO import (`myob_po_260716(1).txt`) | Outbound import contract | Exact format accepted by MYOB for a running supplier PO | Generate only after preview and validation; retain export snapshot and reconcile after import |
| Supplier packing list | Final shipment transaction document | Final packed quantity and physical package/weight details | Import to staging; match Windsor part number + control number; review before finalising shipment lines |
| Supplier commercial invoice | Final financial shipment document | Supplier price, quantity and invoice total | Reconcile against packing list and Widget allocations; retain original file |
| Supplier pallet document | Reference document | Pallet/carton storage and breakdown guidance | Attach to shipment; optional pallet-count/notes extraction only |
| Forwarder/supplier `.msg` files | Communication/reference | Booking, timing, delivery, document and cost evidence | Preserve message/thread metadata and attachments; explicitly link to shipment |
| v1 Python/README files | Reference only | Evidence of existing screens, rules and pain points | Do not import code structure, status vocabulary or database assumptions into v2 |

## 4. Data-volume and quality observations

| File | Logical columns | Well-formed data rows | Observations |
|---|---:|---:|---|
| `ITEMMasterData.TXT` | 68 | 5,819 | Item Number is unique in well-formed rows; 7 physical malformed/continuation rows require quarantine |
| `SALESORDERSFORCOVERORDER.TXT` | 52 | 27,568 | 10,134 invoice numbers and about 2,181 items; repeated items within documents are common |
| `salesdata.TXT` | 52 | 160,102 | 53,349 invoice numbers and about 4,920 items; history spans 2017-11-16 to 2026-07-17 |
| `ITEMPUR.TXT` | 42 | 27,158 | 1,851 purchase numbers and about 5,365 items; repeated item lines are common |
| `CUSTOMERDATA.TXT` | 124 | 2,928 | `Record ID` and customer name are unique in well-formed rows; 28 malformed/continuation rows |
| `Cust File Path.xlsx` | 10 customer columns plus one path column | Customer sheet uses `A1:J3535`; path sheet uses `A1:A7002` | The path list includes current and legacy files but no stable customer ID; normalized filename matches are candidates, not identity |
| `SUPPLIERS.TXT` | 125 | 520 | `Record ID` and supplier name are unique in the example |
| `zinvs1.xlsx` | 6 operational fields | 4,152 lines | Includes normal items and pseudo/non-stock rows; snapshot has no reliable export timestamp inside the data |
| MYOB PO import example | 42 | 28 | 28 unique items; quantity 237,800; value 17,636; line totals reconcile exactly |

### Import quality rule

The TXT exports contain physical line anomalies consistent with embedded or unquoted line breaks. Production imports must use a robust MYOB-aware parser, retain the raw bytes, assign a raw row sequence, and send malformed rows to an issue queue. They must not silently drop, truncate or shift fields.

### Confirmed cover-order example

The supplied Comfort Sleep row maps `Invoice No.` `26002580`, Customer PO `BO00098`, item `MTYC70775CB`, quantity `600`, and Card Record ID `150`. Its Journal Memo is `Sale; Comfort Sleep Bedding Company - COVER ORDER`, which establishes the approved classifier. `CRL 250306` appears inside the item description and remains a control/batch reference; it is not cover-order evidence.

## 5. Proposed business entities

These are conceptual entities, not SQL table definitions.

### Master and configuration

- `items`
- `item_categories`
- `item_planning_settings`
- `suppliers`
- `supplier_contacts`
- `item_supplier_settings`
- `customers`
- `customer_groups`
- `customer_group_members`
- `customer_operating_settings`
- `customer_price_files`
- `customer_price_file_versions`

### Imported MYOB facts

- `sales_documents`
- `sales_document_lines`
- `open_sales_order_snapshots`
- `inventory_snapshots`
- `inventory_snapshot_lines`
- `myob_purchase_snapshots`
- `myob_purchase_snapshot_lines`

### Planning and operational truth

- `demand_classifications`
- `planning_classification_evidence`
- `demand_forecasts`
- `demand_forecast_components`
- `demand_overrides`
- `purchase_requirements`
- `supplier_orders`
- `supplier_order_lines`
- `manufacture_control_numbers`
- `containers`
- `container_proposed_lines`
- `container_final_lines`
- `supplier_order_allocations`
- `shipments`
- `shipment_suppliers`
- `shipment_lines`
- `shipment_events`
- `shipment_documents`
- `shipment_communications`
- `arrival_events`

### Integration, reconciliation and governance

- `import_batches`
- `import_rows`
- `import_issues`
- `myob_export_batches`
- `myob_running_po_snapshots`
- `myob_reconciliation_results`
- `users`
- `audit_log`

## 6. Source-to-entity mapping

| Source fields | Primary entity/meaning | Treatment |
|---|---|---|
| Item Number, Item Name, Buy/Sell/Inventory flags, descriptions, units, tax, accounts, item custom lists, standard cost, default locations | Item master | MYOB-managed attributes upserted by Item Number; Widget planning attributes stored separately |
| Reorder Quantity, Minimum Level | Imported item reference settings | Preserve as MYOB values, but do not automatically treat as approved Widget forecast policy |
| Primary Supplier, Supplier Item Number | Candidate item-supplier mapping | Mostly empty in the example; never rely on these as complete |
| Customer/Supplier Record ID | External card identifier | Store as source-system ID; never use as the Widget primary key |
| Address blocks, contacts, terms, tax and account fields | Customer/supplier master details | Upsert per MYOB account; sensitive payment fields should be excluded from normal UI/import if not required |
| Invoice No., Date, customer, item, quantity, price, discount, total | Sales document and line | Historical sales fact; retain document header and line sequence |
| Journal Memo and Comment on sales orders | Cover-order evidence | Normalize Journal Memo and classify when it ends with `- COVER ORDER`, case-insensitively; preserve raw text, classifier version and reason; near-matches become review issues |
| Purchase No., supplier, item, quantity/order/received/billed, price | MYOB purchase snapshot | Reconciliation/projection source; Widget supplier-order lines remain authoritative |
| On Hand, Committed, On Order, Available | Inventory snapshot line | Store raw values and import timestamp; compute Widget projected availability separately |
| Customer price workbook prices/sites/notes | Customer group reference | Link the document to a customer group; optional reviewed extraction into price-version rows |
| Customer list, state/site suffix and price-file basename | Customer group and price-file matching evidence | Keep MYOB accounts separate; propose Widget group membership and one shared current workbook; ambiguous candidates require approval |
| Purchase/bill item, supplier and date | Item-supplier candidate | Prefer the most recent valid supplier for an exact item; accept automatically only when the match is unique and 100% exact; preserve alternates and evidence |
| Closely timed purchase and sale for the same item | Planning-classification evidence | Propose `made_to_order` using a configurable time/quantity comparison; an audited manual toggle is authoritative |
| Item number beginning `/` | Transaction-only detail | Retain on invoices, bills and document drill-down; exclude from Item enquiry, planning and forecasting |
| Supplier packing `WT O/No` | Manufacture control number | Match against Widget control number; may be numeric or alphanumeric |
| Supplier packing `Labelled As` / future Windsor part number | Item identity | Require exact Windsor item number in future supplier files; ambiguous historical labels require review |
| Packing quantities/packages/net/gross/CBM | Final packed and logistics facts | Preserve split package rows and aggregate only through explicit line relationships |
| Invoice FOB price/quantity/US$ amount | Supplier invoice line | Reconcile with packing list and final shipment allocations |
| Email Message-ID, References/In-Reply-To, subject, sender, recipients, timestamps, body, attachments | Shipment communication | Retain immutable message evidence; link explicitly by Windsor shipment number or human review |

## 7. Source of truth matrix

| Fact | Authoritative source | Secondary/check source |
|---|---|---|
| Current MYOB on-hand/committed/available | Latest accepted inventory snapshot | None; older snapshots are history only |
| Historical invoiced sales | MYOB sales transaction export | Customer enquiry aggregates |
| Open customer/cover order | Latest accepted MYOB open-sales-order snapshot | Cover classification stored with evidence |
| Customer and supplier account details | MYOB card export | Widget group/site mapping and operating flags |
| Customer group membership | Widget | MYOB names/addresses are matching evidence only |
| Prepay/account and freight payer | Widget operating setting | MYOB terms may prompt review but do not overwrite automatically |
| Customer price file | Latest approved linked workbook version | Extracted price rows are a reviewed convenience copy |
| Demand forecast | Widget | MYOB sales history, accepted cover demand and manual overrides are components |
| Supplier/manufacture order and remaining quantity | Widget | MYOB running PO is a replaceable projection |
| Control number | Widget | Supplier packing/invoice documents must echo it |
| Proposed container quantity | Widget | No external document is authoritative until final packing |
| Final packed/shipped quantity | Approved supplier packing list + Widget review | Supplier commercial invoice |
| Shipment/booking identity and timeline | Widget shipment record | Forwarder communications and documents |
| Arrival/completion | Widget arrival event | MYOB inventory later reflects office stock import |

## 8. Identity, natural keys and matching risks

### Required internal identifiers

Every permanent entity receives an immutable Widget ID. Human references such as item number, PO number, control number, container number and shipment number remain searchable alternate identifiers, not database primary keys.

### Safe external identities

- Item master: `source_system + Item Number` is the strongest current natural key.
- Customer/supplier master: `source_system + Record ID` is the preferred external identity; names and Card IDs are matching evidence.
- Import row: `import_batch_id + raw_row_sequence` plus a raw-row hash is always retained.

### Unsafe assumptions proven by the samples

- Invoice number alone is not globally unique: sales history has invoice numbers reused across dates/customers.
- Purchase number alone is not globally unique: purchase numbers are reused across dates/suppliers.
- Document number + item is not a line key: the same item can occur repeatedly within one document.
- Transaction `Record ID` behaves like a customer/supplier card reference, not a document or line ID.
- Customer accounts can represent different states/sites of the same commercial group.
- Item master supplier fields are far too sparse to establish item-supplier relationships.
- Supplier packing documents split one control/item across multiple physical package rows.
- Historical email subjects frequently omit a Windsor shipment reference.

### Matching policy

1. Exact immutable external ID when available.
2. Exact approved alternate key within the correct source and entity type.
3. Candidate match using normalized company name, state/site suffix, shared price-file basename, transaction proximity or filename pattern, shown to a user with evidence.
4. If still ambiguous, create an import issue and do not commit the business change.

Every automatic classification stores the rule version and the evidence that produced it. Approved manual mappings persist and override future fuzzy candidates.

## 9. Approved workflow and quantity states

### Demand and supplier order

1. Customer activity or forecast creates a reviewable purchase requirement.
2. Existing unallocated supplier stock/order quantity is considered before new manufacture.
3. If manufacture is needed, create a Widget supplier order and control number.
4. One control number can cover many item lines and is usually date-based, but uniqueness is enforced independently of display format.
5. Manufacture orders remain in history even if exceptionally cancelled.

### Container and FIFO allocation

1. Proposed container lines select quantities from supplier-order balances.
2. Allocation consumes the oldest eligible supplier-order line first.
3. Proposed quantity does not reduce the permanent order balance.
4. Approved final packed quantity creates permanent allocations and reduces the remaining balance.
5. Reasons for a partial shipment—space, readiness, or deliberate deferral—are recorded without rewriting original ordered quantity.

### Shipment

1. Schedule enquiries may exist without a shipment number.
2. Before anything is sent externally, allocate a unique Windsor shipment number.
3. One physical travelling container/consignment is one Windsor shipment and one forwarder booking, even with multiple suppliers.
4. Each supplier can contribute its own packing list and invoice.
5. Arrival closes the shipment in the Widget; it does not directly change MYOB stock.

## 10. Import and export architecture

Every inbound source follows:

1. Capture original file, filename, size, hash, source type and received/import time.
2. Parse into raw staging rows without changing the original.
3. Validate header/version, row width, required values, types, totals and identifiers.
4. Resolve exact matches and create an issue for every ambiguous or missing match.
5. Show a review summary: inserts, updates, unchanged rows, warnings and rejected rows.
6. Commit the accepted batch transactionally.
7. Record user, timestamp, source hash and before/after audit facts.
8. Make re-import idempotent and detect exact duplicate files.

The MYOB running-PO export adds:

1. Snapshot the current Widget remaining quantities for one supplier.
2. Generate the exact 42-field MYOB file with `{}` marker.
3. Preview line count, quantity, value, item validity and control-number traceability.
4. Require confirmation that the existing MYOB running PO has been deleted.
5. Import the replacement PO to MYOB.
6. Re-export/reconcile MYOB and mark the Widget export batch verified.

## 11. Operational file findings

### Inventory workbook

`zinvs1.xlsx` contains `Item No.`, `Item Name`, `On Hand`, `Committed`, `On Order`, and `Available` across 4,152 lines. It includes pseudo/non-stock rows such as freight and escaped category-like codes. Import rules therefore need an exclusion/alias configuration and must not create unknown items automatically.

### Customer group price workbook

`beard a h(1).xlsx` contains six state/site account sections, contacts and delivery notes, semi-structured price lists, dated commercial notes, customer item-code mappings/usage notes, and a New Zealand freight-account note. The workbook is useful but not machine-uniform enough to be an unattended master-data source.

Recommended treatment:

- link one normal workbook to a Widget customer group;
- allow a site/account override only when necessary;
- store file path, hash, modified time and approved version;
- open the original from the Customer screen;
- optionally add a reviewed price-extraction tool later.

### Customer file-path index

`Cust File Path.xlsx` contains a customer/site sheet and a one-column list of absolute customer price-file paths. It confirms that multiple state accounts can share one group workbook, but it does not contain a stable key joining a path to a MYOB customer.

Recommended treatment:

- create Widget-owned customer groups while preserving each MYOB account/site;
- use normalized company names, state/site suffixes and shared workbook basenames as matching evidence;
- prefer the current non-`old` `.xlsx` over legacy `.xls` paths while retaining older paths as evidence;
- allow only a unique exact match to commit automatically; send collisions, near-matches and special-status accounts for approval.

### MYOB PO import example

The example uses the same 42-field contract as the purchase export. It contains one supplier, PO `260716`, 28 items, 237,800 total quantity and 17,636 total value. `Order` equals `Quantity`; `Received` and `Billed` are zero. Currency and exchange-rate fields are blank. This is a sound concrete template, but export defaults must be configuration rather than copied constants.

### Supplier packing list and invoice

The packing list and invoice share supplier, invoice number/date, consignee, ports, shipping terms, container number, package and weight details. Line matching is based on supplier description, control/order number, size, colour, packaging unit, labelled/Windsor item reference and quantity. The packing list additionally carries package count, net/gross weight and carton dimensions; the invoice carries FOB unit price and US-dollar amount.

Supplier documents currently demonstrate that:

- control numbers may be numeric or alphanumeric;
- one item/control can span several package rows;
- the future supplier file must include the exact Windsor item number;
- `SAMPLE` and other non-order lines require explicit treatment;
- packing and invoice totals must be reconciled independently.

### Pallet document

The pallet document is reference-only evidence used to decide whether goods can be stored as packed or need breakdown. Store it against the shipment, with optional pallet count and notes; do not use it to post item quantities.

### Email samples

The `.msg` files contain booking/delivery dates, skid/package counts, document requests, origin/FTA requirements, ex-works costs, consignee/contact data and status updates. They also preserve Message-ID and thread references. Historical subjects are inconsistent, so automatic matching is unsafe.

Initial v2 email scope should therefore be:

- generate reviewed draft emails with the Windsor shipment number in the subject;
- save the draft/sent metadata against the shipment;
- permit manual import/attachment of saved `.msg` files;
- add Microsoft 365 integration only later if access is available and worthwhile.

## 12. Confirmed mappings and remaining operational setup

| # | Mapping | Status | Approved rule or remaining setup |
|---|---|---|---|
| 1 | Cover-order identification | **Confirmed** | Classify an open sales order as cover demand when normalized Journal Memo ends with `- COVER ORDER`, case-insensitively. Preserve the raw memo and classifier evidence. Do not infer cover status from Customer PO, control number or description. |
| 2 | Customer grouping and price-file matching | **Confirmed design** | Preserve every MYOB account/site. Create Widget groups using customer-export fields, normalized state/site suffixes and a shared price-file basename as evidence. Prefer current non-`old` `.xlsx` paths. Ambiguous matches require approval and approved mappings persist. |
| 3 | Item-to-supplier mapping | **Confirmed design** | Use exact purchase/bill history to propose suppliers and prefer the most recent valid supplier. Automatically accept only a unique 100% exact match; route every other candidate for approval. Preserve supplier history and alternatives. |
| 4 | Made-to-order classification | **Confirmed design** | Compare exact-item purchases and sales occurring in close proximity to propose `made_to_order`. Provide an audited manual planning-class toggle for missed or incorrect candidates; the manual value is authoritative. |
| 5 | Forecast policy | **Confirmed architecture** | Forecast from invoiced sales history, seasonality and approved cover demand without double-counting cover releases. Display evidence and allow audited overrides. Exact windows and thresholds are calibration settings after history is loaded, not schema blockers. |
| 6 | Lead-time policy | **Confirmed architecture** | Store supplier manufacture defaults, item-supplier overrides, route/transit time and planning buffer separately. Learn actual durations from orders and shipments while retaining approved manual values. |
| 7 | Item-view exclusions | **Confirmed** | Exclude item numbers beginning `/` from Item enquiry, forecasting and planning. Retain them on invoice, bill and document drill-down screens. |
| 8 | Running-PO conventions | **Operational setup remains** | Define the supplier-specific PO-number pattern, MYOB delete/re-import checklist and post-import verification report. This does not block the schema. |
| 9 | Future supplier document contract | **Operational setup remains** | Require Windsor item number, supplier part number, quantity, UOM, price, currency, control reference and document number when the supplier format is updated. This does not block the schema. |
| 10 | Import cadence | **Operational setup remains** | Define the frequency, watched folder, filename convention, archive/failed handling and responsible owner for each export. This does not block the schema. |

## 13. Stage 1 start recommendation

The source architecture and business-rule gate are approved. The next implementation step should be the isolated Stage 1 foundation—not a screen copied from v1:

1. Create the isolated development configuration and environment guard.
2. Create the new SQL Server database through versioned migrations.
3. Implement `import_batches`, raw staging, validation issues, users and audit log first.
4. Implement item/customer/supplier master imports and the inventory snapshot.
5. Implement historical sales and cover-order imports.
6. Build a thin PySide6 navigation shell with read-only Customer and Item enquiry slices against imported test data.
7. Add supplier orders, FIFO allocations, containers, shipments and MYOB running-PO export after the data foundation proves stable.

---

# Appendix A — Exact MYOB field inventory

## A1. Item master — 68 fields

1. Item Number
2. Item Name
3. Buy
4. Sell
5. Inventory
6. Asset Acct
7. Income Acct
8. Expense/COS Acct
9. Description
10. Use Desc. On Invoice
11. Custom List 1
12. Custom List 2
13. Custom List 3
14. Custom Field 1
15. Custom Field 2
16. Custom Field 3
17. Primary Supplier
18. Supplier Item Number
19. Tax Code When Bought
20. Buy Unit Measure
21. No. Items/Buy Unit
22. Reorder Quantity
23. Minimum Level
24. Selling Price
25. Sell Unit Measure
26. Tax Code When Sold
27. Sell Price Inclusive
28. Sales Tax Calc. Method
29. No. Items/Sell Unit
30. Quantity Break 1
31. Quantity Break 2
32. Quantity Break 3
33. Quantity Break 4
34. Quantity Break 5
35. Price Level A, Qty Break 1
36. Price Level B, Qty Break 1
37. Price Level C, Qty Break 1
38. Price Level D, Qty Break 1
39. Price Level E, Qty Break 1
40. Price Level F, Qty Break 1
41. Price Level A, Qty Break 2
42. Price Level B, Qty Break 2
43. Price Level C, Qty Break 2
44. Price Level D, Qty Break 2
45. Price Level E, Qty Break 2
46. Price Level F, Qty Break 2
47. Price Level A, Qty Break 3
48. Price Level B, Qty Break 3
49. Price Level C, Qty Break 3
50. Price Level D, Qty Break 3
51. Price Level E, Qty Break 3
52. Price Level F, Qty Break 3
53. Price Level A, Qty Break 4
54. Price Level B, Qty Break 4
55. Price Level C, Qty Break 4
56. Price Level D, Qty Break 4
57. Price Level E, Qty Break 4
58. Price Level F, Qty Break 4
59. Price Level A, Qty Break 5
60. Price Level B, Qty Break 5
61. Price Level C, Qty Break 5
62. Price Level D, Qty Break 5
63. Price Level E, Qty Break 5
64. Price Level F, Qty Break 5
65. Inactive Item
66. Standard Cost
67. Default Ship/Sell Location
68. Default Recvd/Auto Location

## A2. Sales history and cover-order export — 52 fields

Both examples use the same header:

1. Addr 1 - Line 1
2. Co./Last Name
3. First Name
4. Addr 1 - Line 2
5. Addr 1 - Line 3
6. Addr 1 - Line 4
7. Inclusive
8. Invoice No.
9. Date
10. Customer PO
11. Ship Via
12. Delivery Status
13. Item Number
14. Quantity
15. Description
16. Price
17. Discount
18. Total
19. Job
20. Comment
21. Journal Memo
22. Salesperson Last Name
23. Salesperson First Name
24. Shipping Date
25. Referral Source
26. Tax Code
27. Tax Amount
28. Freight Amount
29. Freight Tax Code
30. Freight Tax Amount
31. Sale Status
32. Currency Code
33. Exchange Rate
34. Terms - Payment is Due
35. Terms - Discount Days
36. Terms - Balance Due Days
37. Terms - % Discount
38. Terms - % Monthly Charge
39. Amount Paid
40. Payment Method
41. Payment Notes
42. Name on Card
43. Card Number
44. Authorisation Code
45. BSB
46. Account Number
47. Drawer/Account Name
48. Cheque Number
49. Category
50. Location ID
51. Card ID
52. Record ID

## A3. Purchase export and MYOB PO import — 42 fields

1. Co./Last Name
2. First Name
3. Addr 1 - Line 1
4. Addr 1 - Line 2
5. Addr 1 - Line 3
6. Addr 1 - Line 4
7. Inclusive
8. Purchase No.
9. Date
10. Supplier Invoice No.
11. Ship Via
12. Delivery Status
13. Item Number
14. Quantity
15. Description
16. Price
17. Discount
18. Total
19. Job
20. Comment
21. Journal Memo
22. Shipping Date
23. Tax Code
24. Tax Amount
25. Freight Amount
26. Freight Tax Code
27. Freight Tax Amount
28. Purchase Status
29. Currency Code
30. Exchange Rate
31. Terms - Payment is Due
32. Terms - Discount Days
33. Terms - Balance Due Days
34. Terms - % Discount
35. Amount Paid
36. Category
37. Order
38. Received
39. Billed
40. Location ID
41. Card ID
42. Record ID

## A4. Customer master — 124 fields

1. Addr 1 - City
2. Co./Last Name
3. First Name
4. Card ID
5. Card Status
6. Addr 1 - Line 1
7. Addr 1 - Line 2
8. Addr 1 - Line 3
9. Addr 1 - Line 4
10. Addr 1 - State
11. Addr 1 - Postcode
12. Addr 1 - Country
13. Addr 1 - Phone No. 1
14. Addr 1 - Phone No. 2
15. Addr 1 - Phone No. 3
16. Addr 1 - Fax No.
17. Addr 1 - Email
18. Addr 1 - WWW
19. Addr 1 - Contact Name
20. Addr 1 - Salutation
21. Addr 2 - Line 1
22. Addr 2 - Line 2
23. Addr 2 - Line 3
24. Addr 2 - Line 4
25. Addr 2 - City
26. Addr 2 - State
27. Addr 2 - Postcode
28. Addr 2 - Country
29. Addr 2 - Phone No. 1
30. Addr 2 - Phone No. 2
31. Addr 2 - Phone No. 3
32. Addr 2 - Fax No.
33. Addr 2 - Email
34. Addr 2 - WWW
35. Addr 2 - Contact Name
36. Addr 2 - Salutation
37. Addr 3 - Line 1
38. Addr 3 - Line 2
39. Addr 3 - Line 3
40. Addr 3 - Line 4
41. Addr 3 - City
42. Addr 3 - State
43. Addr 3 - Postcode
44. Addr 3 - Country
45. Addr 3 - Phone No. 1
46. Addr 3 - Phone No. 2
47. Addr 3 - Phone No. 3
48. Addr 3 - Fax No.
49. Addr 3 - Email
50. Addr 3 - WWW
51. Addr 3 - Contact Name
52. Addr 3 - Salutation
53. Addr 4 - Line 1
54. Addr 4 - Line 2
55. Addr 4 - Line 3
56. Addr 4 - Line 4
57. Addr 4 - City
58. Addr 4 - State
59. Addr 4 - Postcode
60. Addr 4 - Country
61. Addr 4 - Phone No. 1
62. Addr 4 - Phone No. 2
63. Addr 4 - Phone No. 3
64. Addr 4 - Fax No.
65. Addr 4 - Email
66. Addr 4 - WWW
67. Addr 4 - Contact Name
68. Addr 4 - Salutation
69. Addr 5 - Line 1
70. Addr 5 - Line 2
71. Addr 5 - Line 3
72. Addr 5 - Line 4
73. Addr 5 - City
74. Addr 5 - State
75. Addr 5 - Postcode
76. Addr 5 - Country
77. Addr 5 - Phone No. 1
78. Addr 5 - Phone No. 2
79. Addr 5 - Phone No. 3
80. Addr 5 - Fax No.
81. Addr 5 - Email
82. Addr 5 - WWW
83. Addr 5 - Contact Name
84. Addr 5 - Salutation
85. Notes
86. Identifiers
87. Custom List 1
88. Custom List 2
89. Custom List 3
90. Custom Field 1
91. Custom Field 2
92. Custom Field 3
93. Billing Rate
94. Terms - Payment is Due
95. Terms - Discount Days
96. Terms - Balance Due Days
97. Terms - % Discount
98. Terms - % Monthly Charge
99. Tax Code
100. Credit Limit
101. Tax ID No.
102. Volume Discount %
103. Sales/Purchase Layout
104. Price Level
105. Payment Method
106. Payment Notes
107. Name on Card
108. Card Number
109. BSB
110. Account Number
111. Account Name
112. A.B.N.
113. A.B.N. Branch
114. Account
115. Salesperson
116. Salesperson Card ID
117. Comment
118. Shipping Method
119. Printed Form
120. Freight Tax Code
121. Use Customer's Tax Code
122. Receipt Memo
123. Invoice/Purchase Order Delivery
124. Record ID

## A5. Supplier master — 125 fields

1. Co./Last Name
2. First Name
3. Card ID
4. Card Status
5. Addr 1 - Line 1
6. Addr 1 - Line 2
7. Addr 1 - Line 3
8. Addr 1 - Line 4
9. Addr 1 - City
10. Addr 1 - State
11. Addr 1 - Postcode
12. Addr 1 - Country
13. Addr 1 - Phone No. 1
14. Addr 1 - Phone No. 2
15. Addr 1 - Phone No. 3
16. Addr 1 - Fax No.
17. Addr 1 - Email
18. Addr 1 - WWW
19. Addr 1 - Contact Name
20. Addr 1 - Salutation
21. Addr 2 - Line 1
22. Addr 2 - Line 2
23. Addr 2 - Line 3
24. Addr 2 - Line 4
25. Addr 2 - City
26. Addr 2 - State
27. Addr 2 - Postcode
28. Addr 2 - Country
29. Addr 2 - Phone No. 1
30. Addr 2 - Phone No. 2
31. Addr 2 - Phone No. 3
32. Addr 2 - Fax No.
33. Addr 2 - Email
34. Addr 2 - WWW
35. Addr 2 - Contact Name
36. Addr 2 - Salutation
37. Addr 3 - Line 1
38. Addr 3 - Line 2
39. Addr 3 - Line 3
40. Addr 3 - Line 4
41. Addr 3 - City
42. Addr 3 - State
43. Addr 3 - Postcode
44. Addr 3 - Country
45. Addr 3 - Phone No. 1
46. Addr 3 - Phone No. 2
47. Addr 3 - Phone No. 3
48. Addr 3 - Fax No.
49. Addr 3 - Email
50. Addr 3 - WWW
51. Addr 3 - Contact Name
52. Addr 3 - Salutation
53. Addr 4 - Line 1
54. Addr 4 - Line 2
55. Addr 4 - Line 3
56. Addr 4 - Line 4
57. Addr 4 - City
58. Addr 4 - State
59. Addr 4 - Postcode
60. Addr 4 - Country
61. Addr 4 - Phone No. 1
62. Addr 4 - Phone No. 2
63. Addr 4 - Phone No. 3
64. Addr 4 - Fax No.
65. Addr 4 - Email
66. Addr 4 - WWW
67. Addr 4 - Contact Name
68. Addr 4 - Salutation
69. Addr 5 - Line 1
70. Addr 5 - Line 2
71. Addr 5 - Line 3
72. Addr 5 - Line 4
73. Addr 5 - City
74. Addr 5 - State
75. Addr 5 - Postcode
76. Addr 5 - Country
77. Addr 5 - Phone No. 1
78. Addr 5 - Phone No. 2
79. Addr 5 - Phone No. 3
80. Addr 5 - Fax No.
81. Addr 5 - Email
82. Addr 5 - WWW
83. Addr 5 - Contact Name
84. Addr 5 - Salutation
85. Notes
86. Identifiers
87. Custom List 1
88. Custom List 2
89. Custom List 3
90. Custom Field 1
91. Custom Field 2
92. Custom Field 3
93. Billing Rate
94. Cost Per Hour
95. Terms - Payment is Due
96. Terms - Discount Days
97. Terms - Balance Due Days
98. Terms - % Discount
99. Tax Code
100. Credit Limit
101. Tax ID No.
102. Payment Method
103. Payment Notes
104. Name on Card
105. Card Number
106. BSB
107. Account Number
108. Account Name
109. Statement Text
110. Remittance Method
111. Remittance Address
112. A.B.N.
113. A.B.N. Branch
114. Volume Discount %
115. Sales/Purchase Layout
116. Account
117. Comment
118. Shipping Method
119. Printed Form
120. Freight Tax Code
121. Use Supplier's Tax Code
122. Report Taxable Payments
123. Payment Memo
124. Invoice/Purchase Order Delivery
125. Record ID

# Appendix B — Non-MYOB operational field inventory

## B1. Inventory workbook

- Item No.
- Item Name
- On Hand
- Committed
- On Order
- Available

## B2. Customer price workbook

Observed information classes rather than a single uniform table:

- customer group/site name
- address and state
- contact names
- telephone/fax/mobile/email
- delivery instructions
- customer item code
- Windsor/item description or code
- size
- metres/cone or pack quantity
- colour
- price
- price basis/unit
- state/site restriction
- usage/volume notes
- dated commercial notes
- customer share/usage notes
- freight account/reference

## B3. Supplier packing list

Header facts:

- exporter
- supplier invoice number
- invoice date
- consignee
- order number
- port of loading
- vessel/flight number
- shipping terms
- container number
- port of discharge
- final destination
- total packages
- total net weight
- total gross weight

Line facts:

- supplier item/description
- WT O/No (manufacture control number)
- size
- colour name
- roll/spool/package type
- metres per unit
- Labelled As / future Windsor part number
- quantity
- package count
- net weight
- gross weight
- CBM/carton dimensions

## B4. Supplier commercial invoice

Header facts are substantially the same as the packing list. Line facts:

- supplier item/description
- WT O/No (manufacture control number)
- size
- colour name
- roll/spool/package type
- metres per unit
- Labelled As / future Windsor part number
- FOB price per unit/metre
- quantity
- line amount in US dollars

## B5. Pallet reference document

- shipment/document reference
- total pallet count
- pallet number
- item/size/colour description
- carton count
- storage/breakdown notes derived by Windsor staff

## B6. Email/message evidence

- Message-ID
- References
- In-Reply-To
- subject
- sender name/address
- To/Cc/Bcc recipients
- sent/received timestamps
- message body in original and display forms
- attachment filenames/types/hashes
- booking reference
- Windsor shipment number when present
- supplier/consignee/origin/destination evidence
- schedule, cut-off, pickup, delivery, ETD and ETA evidence
- package/skid/container evidence
- cost/incoterm/duty/origin-document evidence
- import matching status and reviewer
