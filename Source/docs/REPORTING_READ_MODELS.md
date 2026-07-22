# Reporting read models

This phase introduces the read-only service layer between SQLAlchemy and the future PySide6 interface.

## Included services

- committed foundation count verification;
- item and customer lookup searches;
- Item Summary totals for sales, current cover orders and purchases;
- Customer Summary totals for sales and current cover orders;
- zero-filled monthly sales series for item and customer trend charts;
- inclusive calendar-month windows with an explicit as-of date.

The services do not write to the database. The current cover-order calculations use only the snapshot marked `is_current`, include all lines in that snapshot, and expose its capture timestamp for freshness checks.

## PowerShell workflow

Verify the reporting foundation:

```powershell
.\scripts\reporting_service_workflow.ps1 -Action Verify
```

Find an item:

```powershell
.\scripts\reporting_service_workflow.ps1 `
  -Action FindItem `
  -Query "MTS36"
```

Show an Item Summary:

```powershell
.\scripts\reporting_service_workflow.ps1 `
  -Action Item `
  -ItemNumber "MTS36TURQT6984" `
  -Months 12 `
  -AsOf "2026-07-22"
```

Find a customer:

```powershell
.\scripts\reporting_service_workflow.ps1 `
  -Action FindCustomer `
  -Query "Comfort Sleep"
```

Show a Customer Summary using the MYOB Record ID returned by the search:

```powershell
.\scripts\reporting_service_workflow.ps1 `
  -Action Customer `
  -CustomerRecordId "150" `
  -Months 12 `
  -AsOf "2026-07-22"
```

## Period definition

A 12-month period ending 22 July 2026 starts on 1 August 2025. It includes the current partial month and the previous eleven calendar months.

## Next application phase

The PySide6 screens should bind to these service dataclasses rather than issue SQL directly. The next layer will add order-planning inputs and calculations after the source for quantity-on-hand and in-transit stock is confirmed.
