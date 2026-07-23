param(
    [string]$Config = "config/development.local.json",
    [ValidateSet("Review", "Approve", "Preview", "Commit")]
    [string]$Action = "Review",
    [string]$Username = "",
    [string]$DisplayName = "",
    [ValidateSet("sales_transactions", "cover_order_snapshot", "purchase_transactions")]
    [string[]]$SourceType = @()
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$OriginalPythonPath = $env:PYTHONPATH

function Invoke-CheckedPython {
    param([string[]]$Arguments)
    & python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed with exit code $LASTEXITCODE."
    }
}

Push-Location $ProjectRoot
try {
    if (-not (Test-Path $Config)) {
        throw "Configuration file not found: $Config"
    }
    $SourcePath = Join-Path $ProjectRoot "src"
    if ($OriginalPythonPath) {
        $env:PYTHONPATH = "$SourcePath;$OriginalPythonPath"
    }
    else {
        $env:PYTHONPATH = $SourcePath
    }

    switch ($Action) {
        "Review" {
            Invoke-CheckedPython @("-m", "windsor_widget.cli", "review-transaction-imports", $Config)
        }
        "Approve" {
            if (-not $Username -or -not $DisplayName) {
                throw "Approve requires -Username and -DisplayName."
            }
            $Arguments = @(
                "-m", "windsor_widget.cli", "approve-transaction-imports", $Config,
                "--username", $Username, "--display-name", $DisplayName
            )
            foreach ($SelectedSource in $SourceType) {
                $Arguments += @("--source-type", $SelectedSource)
            }
            Invoke-CheckedPython $Arguments
        }
        "Preview" {
            $Arguments = @("-m", "windsor_widget.cli", "promote-transaction-imports", $Config)
            foreach ($SelectedSource in $SourceType) {
                $Arguments += @("--source-type", $SelectedSource)
            }
            Invoke-CheckedPython $Arguments
        }
        "Commit" {
            if (-not $Username -or -not $DisplayName) {
                throw "Commit requires -Username and -DisplayName."
            }
            $Arguments = @(
                "-m", "windsor_widget.cli", "promote-transaction-imports", $Config,
                "--commit", "--username", $Username, "--display-name", $DisplayName
            )
            foreach ($SelectedSource in $SourceType) {
                $Arguments += @("--source-type", $SelectedSource)
            }
            Invoke-CheckedPython $Arguments
        }
    }
}
finally {
    $env:PYTHONPATH = $OriginalPythonPath
    Pop-Location
}
