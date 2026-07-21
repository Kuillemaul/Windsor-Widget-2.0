# Master Import Approval and Promotion

This workflow applies only to the staged MYOB item, customer and supplier masters.
Sales, open sales orders and purchase transactions remain in staging until their
operational transaction schemas are implemented.

## Safety rules

- Exactly one uncommitted batch must exist for each master source.
- Every batch must have zero issues and its stored row count must match its declared count.
- Approval is explicit and is recorded as an audit event.
- Promotion matches items by exact MYOB item number and customer/supplier cards by exact MYOB Record ID.
- Card-ID collisions stop the entire promotion. Names are never used to guess an identity.
- Source-owned fields are updated, while user-owned planning, lead-time, grouping, payment and freight settings are preserved.
- Item-to-supplier links are not created in this slice because supplier-name matching requires a separate reviewed workflow.

## Commands

```powershell
.\scripts\master_import_workflow.ps1 -Action Review

.\scripts\master_import_workflow.ps1 `
  -Action Approve `
  -Username "brad" `
  -DisplayName "Brad Mayze"

.\scripts\master_import_workflow.ps1 -Action Preview

.\scripts\master_import_workflow.ps1 `
  -Action Commit `
  -Username "brad" `
  -DisplayName "Brad Mayze"
```

The preview command performs no writes. The commit command promotes all three
approved master batches in one transaction and marks their rows and batches as
committed only after successful exact-key processing.
