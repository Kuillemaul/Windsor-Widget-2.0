# Stage 1 foundation boundary

This stage establishes the safety and governance layer needed before business
data is committed.

## Import lifecycle

Every external file follows this lifecycle:

1. register the file and its SHA-256 hash as an import batch;
2. preserve parsed source rows with their raw source text and row hash;
3. validate required fields and natural keys;
4. create review issues for invalid or ambiguous rows;
5. create ranked match candidates without guessing a winner;
6. require a user decision where a unique exact match does not exist; and
7. commit approved business changes with audit events linking back to the
   batch, row and user.

Previewing a file must never change business tables. Re-importing an identical
file is detectable by its hash and must be reviewed rather than silently
duplicated.

## Locked rules represented by the foundation

- Source identifiers are evidence; internal UUIDs are the durable keys.
- Ambiguous item, customer, supplier, PO and shipment matches enter a review
  queue.
- Match resolutions store the deciding user, time and notes.
- Import rows remain available after successful processing.
- Audit events are append-only application history.
- MYOB remains the accounting and stock-on-hand system of record.
- Widget 2.0 remains the operational ordering and shipment system of record.

## Master-data proposal now available

Migration `0002_master_data` now defines the reviewed first master-data slice:

- `customer_groups`;
- `customer_accounts`;
- `customer_price_files`;
- `suppliers`;
- `items`; and
- `item_suppliers`.

The migration has been generated and validated as offline Microsoft SQL Server
SQL only. It has not been applied and no database has been created. Streaming
importers now inspect the supplied MYOB item, customer, supplier, sales and
purchase exports, calculate source and row hashes, preserve multiline fields,
and quarantine malformed rows or incomplete natural keys for review.

The measured source assessment and exact field mappings are recorded in
`docs/SOURCE_CONTRACTS.md`. Database creation remains blocked until that
assessment and the six master entities are explicitly approved.
