param(
    [string]$Config = "config/development.local.json"
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

    Write-Host "Preparing the isolated WindsorWidgetV2_DEV database..."
    Invoke-CheckedPython @("-m", "windsor_widget.cli", "check-config", $Config)
    Invoke-CheckedPython @(
        "-m",
        "windsor_widget.cli",
        "setup-dev-database",
        $Config,
        "--alembic-config",
        "alembic.ini"
    )
}
finally {
    $env:PYTHONPATH = $OriginalPythonPath
    Pop-Location
}
