param(
    [ValidateSet("Verify", "FindItem", "FindCustomer", "Item", "Customer")]
    [string]$Action = "Verify",

    [string]$Config = "config\development.local.json",
    [string]$Query,
    [string]$ItemNumber,
    [string]$CustomerRecordId,
    [int]$Months = 12,
    [string]$AsOf,
    [int]$Limit = 20
)

$ErrorActionPreference = "Stop"

function Invoke-WindsorPython {
    param([string[]]$Arguments)

    & python -m windsor_widget.cli @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed with exit code $LASTEXITCODE."
    }
}

switch ($Action) {
    "Verify" {
        Invoke-WindsorPython @("verify-reporting-data", $Config)
    }
    "FindItem" {
        if ([string]::IsNullOrWhiteSpace($Query)) {
            throw "-Query is required for -Action FindItem."
        }
        Invoke-WindsorPython @(
            "find-items", $Config, $Query,
            "--limit", $Limit.ToString()
        )
    }
    "FindCustomer" {
        if ([string]::IsNullOrWhiteSpace($Query)) {
            throw "-Query is required for -Action FindCustomer."
        }
        Invoke-WindsorPython @(
            "find-customers", $Config, $Query,
            "--limit", $Limit.ToString()
        )
    }
    "Item" {
        if ([string]::IsNullOrWhiteSpace($ItemNumber)) {
            throw "-ItemNumber is required for -Action Item."
        }
        $arguments = @(
            "item-summary", $Config, $ItemNumber,
            "--months", $Months.ToString()
        )
        if (-not [string]::IsNullOrWhiteSpace($AsOf)) {
            $arguments += @("--as-of", $AsOf)
        }
        Invoke-WindsorPython $arguments
    }
    "Customer" {
        if ([string]::IsNullOrWhiteSpace($CustomerRecordId)) {
            throw "-CustomerRecordId is required for -Action Customer."
        }
        $arguments = @(
            "customer-summary", $Config, $CustomerRecordId,
            "--months", $Months.ToString()
        )
        if (-not [string]::IsNullOrWhiteSpace($AsOf)) {
            $arguments += @("--as-of", $AsOf)
        }
        Invoke-WindsorPython $arguments
    }
}
