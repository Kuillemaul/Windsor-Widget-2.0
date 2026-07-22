param(
    [ValidateSet(
        "InventoryPreview",
        "InventoryCommit",
        "InventoryStatus",
        "Readiness",
        "Item",
        "OrderAnalysis"
    )]
    [string]$Action = "Readiness",

    [string]$Config = "config\development.local.json",
    [string]$SourceFile,
    [string]$CapturedAt,
    [string]$Username,
    [string]$DisplayName,
    [string]$ItemNumber,
    [int]$Months = 12,
    [int]$LeadWeeks = 14,
    [ValidateSet("3v3", "6v6", "yoy")]
    [string]$Trend = "3v3",
    [string]$AsOf,
    [int]$Limit = 50,
    [switch]$IncludeOk
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
    "InventoryPreview" {
        if ([string]::IsNullOrWhiteSpace($SourceFile)) {
            throw "-SourceFile is required for -Action InventoryPreview."
        }
        $arguments = @("preview-inventory-snapshot", $Config, $SourceFile)
        if (-not [string]::IsNullOrWhiteSpace($CapturedAt)) {
            $arguments += @("--captured-at", $CapturedAt)
        }
        Invoke-WindsorPython $arguments
    }
    "InventoryCommit" {
        if ([string]::IsNullOrWhiteSpace($SourceFile)) {
            throw "-SourceFile is required for -Action InventoryCommit."
        }
        if ([string]::IsNullOrWhiteSpace($Username)) {
            throw "-Username is required for -Action InventoryCommit."
        }
        if ([string]::IsNullOrWhiteSpace($DisplayName)) {
            throw "-DisplayName is required for -Action InventoryCommit."
        }
        $arguments = @(
            "commit-inventory-snapshot", $Config, $SourceFile,
            "--username", $Username,
            "--display-name", $DisplayName
        )
        if (-not [string]::IsNullOrWhiteSpace($CapturedAt)) {
            $arguments += @("--captured-at", $CapturedAt)
        }
        Invoke-WindsorPython $arguments
    }
    "InventoryStatus" {
        Invoke-WindsorPython @("inventory-snapshot-status", $Config)
    }
    "Readiness" {
        Invoke-WindsorPython @("planning-readiness", $Config)
    }
    "Item" {
        if ([string]::IsNullOrWhiteSpace($ItemNumber)) {
            throw "-ItemNumber is required for -Action Item."
        }
        $arguments = @(
            "item-planning", $Config, $ItemNumber,
            "--months", $Months.ToString(),
            "--lead-weeks", $LeadWeeks.ToString(),
            "--trend", $Trend
        )
        if (-not [string]::IsNullOrWhiteSpace($AsOf)) {
            $arguments += @("--as-of", $AsOf)
        }
        Invoke-WindsorPython $arguments
    }
    "OrderAnalysis" {
        $arguments = @(
            "order-analysis", $Config,
            "--months", $Months.ToString(),
            "--lead-weeks", $LeadWeeks.ToString(),
            "--trend", $Trend,
            "--limit", $Limit.ToString()
        )
        if (-not [string]::IsNullOrWhiteSpace($AsOf)) {
            $arguments += @("--as-of", $AsOf)
        }
        if ($IncludeOk) {
            $arguments += "--include-ok"
        }
        Invoke-WindsorPython $arguments
    }
}
