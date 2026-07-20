param(
    [string]$Config = "config/development.local.json",
    [string]$Manifest = "config/myob_sources.local.json",
    [string]$Report = "",
    [int]$ChunkSize = 1000,
    [switch]$Commit
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
    if (-not (Test-Path $Manifest)) {
        throw "MYOB source manifest not found: $Manifest"
    }

    $SourcePath = Join-Path $ProjectRoot "src"
    if ($OriginalPythonPath) {
        $env:PYTHONPATH = "$SourcePath;$OriginalPythonPath"
    }
    else {
        $env:PYTHONPATH = $SourcePath
    }

    $Arguments = @(
        "-m",
        "windsor_widget.cli",
        "stage-myob-exports",
        $Config,
        "--manifest",
        $Manifest,
        "--chunk-size",
        $ChunkSize
    )
    if ($Report) {
        $Arguments += @("--report", $Report)
    }
    if ($Commit) {
        $Arguments += "--commit"
        Write-Host "Staging declared MYOB exports for review..."
    }
    else {
        Write-Host "Inspecting declared MYOB exports (dry run only)..."
    }

    Invoke-CheckedPython $Arguments
}
finally {
    $env:PYTHONPATH = $OriginalPythonPath
    Pop-Location
}
