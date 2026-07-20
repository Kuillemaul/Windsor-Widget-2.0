# Source matching assessment

Status: **generated for business review; no database was created**.

This assessment runs only against the supplied reference exports. It creates
deterministic proposals and approval queues under `reports/source_assessment`.
It does not connect to SQL Server, create a database, or change business data.

## Assessment result

| Area | Result | Approval position |
|---|---:|---|
| Item master candidates | 8,063 | 8,007 planning items and 56 `/` or `\` control items hidden from planning views but retained for document detail. |
| Customer account candidates | 3,537 | Usable as account master candidates. |
| Proposed customer groups | 3,484 | Exact normalized proposals; 29 groups contain multiple accounts (82 accounts total). |
| Supplier candidates | 520 | Usable as supplier master candidates. |
| Item/supplier proposals from purchase history | 5,340 | All resolve to one exact supplier; no ambiguous supplier review rows in this sample. |
| Planning items without purchase-history supplier evidence | 2,667 | Remain unassigned until later evidence or a manual decision exists. |
| Current customer price-file paths | 3,833 | 1,935 exact unique matches; all other matches remain unapproved. |

## Customer price-file approval queue

The 1,898 price-file rows requiring review are separated by reason:

| Review reason | Rows | Meaning |
|---|---:|---|
| Ambiguous | 299 | Two or more near-equal customer-group candidates; a user must select one. |
| Non-exact | 926 | One suggested group exists, but the match is not exact; a user must approve or replace it. |
| Unmatched | 673 | No acceptable customer-group candidate was found; a user must assign or deliberately leave it unlinked. |

The approval columns in `customer_price_file_review.csv` are intentionally
blank. The assessment never turns a fuzzy result into an approved
relationship.

## Source rows requiring review

The source parser found sparse structural exceptions while preserving all raw
evidence:

| Source | Rows seen | Usable master/transaction rows | Rows requiring review |
|---|---:|---:|---:|
| Item master | 8,070 | 8,063 | 7 |
| Customer master | 3,565 | 3,537 | 28 |
| Supplier master | 520 | 520 | 0 |
| Purchase transactions | 27,172 | 27,158 | 14 |

`source_row_review.csv` contains one row per issue, so its 63 issue rows can
exceed the number of distinct source rows requiring review. These records must
be inspected rather than padded, shifted, or silently trusted.

## Review files

| File | Purpose |
|---|---|
| `source_match_summary.json` | Machine-readable counts, source hashes and proof that the run was review-only. |
| `source_row_review.csv` | Structural or natural-key issues requiring inspection. |
| `customer_group_multi_account.csv` | Proposed commercial groups containing more than one MYOB account. |
| `customer_price_file_review.csv` | Ambiguous, non-exact and unmatched customer price-file proposals with approval fields. |
| `item_supplier_review.csv` | Supplier exceptions requiring approval; header-only for the current supplied sample. |

Two consecutive runs over the same source files produced byte-identical
outputs. The source SHA-256 hashes are stored in `source_match_summary.json`.

## Approval gate

Before any database is created, review and approve or amend:

1. the entity and field contracts in `SOURCE_CONTRACTS.md`;
2. the 29 multi-account customer-group proposals;
3. the price-file approval queue policy and the rows needed for the first
   usable release; and
4. the rule that 2,667 planning items remain without a supplier until evidence
   or a manual assignment exists.

Applying migrations remains blocked until that review is explicitly recorded.
