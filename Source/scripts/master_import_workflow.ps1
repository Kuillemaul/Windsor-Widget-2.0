param(
    [string]$Config = "config/development.local.json",
    [ValidateSet("Review", "Approve", "Preview", "Commit")]
    [string]$Action = "Review",
    [string]$Username = "",
    [string]$DisplayName = ""
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
            Invoke-CheckedPython @("-m", "windsor_widget.cli", "review-master-imports", $Config)
        }
        "Approve" {
            if (-not $Username -or -not $DisplayName) {
                throw "Approve requires -Username and -DisplayName."
            }
            Invoke-CheckedPython @(
                "-m", "windsor_widget.cli", "approve-master-imports", $Config,
                "--username", $Username, "--display-name", $DisplayName
            )
        }
        "Preview" {
            Invoke-CheckedPython @("-m", "windsor_widget.cli", "promote-master-imports", $Config)
        }
        "Commit" {
            if (-not $Username -or -not $DisplayName) {
                throw "Commit requires -Username and -DisplayName."
            }
            Invoke-CheckedPython @(
                "-m", "windsor_widget.cli", "promote-master-imports", $Config,
                "--commit", "--username", $Username, "--display-name", $DisplayName
            )
        }
    }
}
finally {
    $env:PYTHONPATH = $OriginalPythonPath
    Pop-Location
}
