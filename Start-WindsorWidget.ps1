[CmdletBinding()]
param(
    [string]$RepoRoot = "C:\python\Widget 2.0\Windsor-Widget-2.0",
    [string]$SqlUsername = "WindsorWidgetV2_Migrator"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$SourceRoot = Join-Path $RepoRoot "Source"
$ConfigPath = Join-Path $SourceRoot "config\development.local.json"

$venvCandidates = @(
    (Join-Path $RepoRoot ".venv"),
    (Join-Path $SourceRoot ".venv")
)

$VenvRoot = $venvCandidates |
    Where-Object { Test-Path -LiteralPath (Join-Path $_ "Scripts\python.exe") -PathType Leaf } |
    Select-Object -First 1

if (-not $VenvRoot) {
    throw "Virtual environment not found. Checked: $($venvCandidates -join ', ')"
}

$VenvScripts = Join-Path $VenvRoot "Scripts"
$PythonExe = Join-Path $VenvScripts "python.exe"

if (-not (Test-Path -LiteralPath $SourceRoot -PathType Container)) {
    throw "Source folder not found: $SourceRoot"
}

if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    throw "Configuration file not found: $ConfigPath"
}

$env:VIRTUAL_ENV = $VenvRoot
$env:PYTHONHOME = $null

$pathParts = $env:PATH -split ";"
if ($pathParts -notcontains $VenvScripts) {
    $env:PATH = "$VenvScripts;$env:PATH"
}

$srcPath = Join-Path $SourceRoot "src"
$pythonPathParts = @()
if ($env:PYTHONPATH) {
    $pythonPathParts = $env:PYTHONPATH -split ";"
}
if ($pythonPathParts -notcontains $srcPath) {
    $env:PYTHONPATH = if ($env:PYTHONPATH) {
        "$srcPath;$env:PYTHONPATH"
    }
    else {
        $srcPath
    }
}

$credential = Get-Credential `
    -UserName $SqlUsername `
    -Message "Enter the Windsor Widget 2.0 SQL password"

$env:WINDSOR_WIDGET_V2_DB_USERNAME = $credential.UserName
$env:WINDSOR_WIDGET_V2_DB_PASSWORD = $credential.GetNetworkCredential().Password

Set-Location $SourceRoot

Write-Host ""
Write-Host "Windsor Widget 2.0 development session ready." -ForegroundColor Green
Write-Host "Repository : $RepoRoot"
Write-Host "Working dir: $SourceRoot"
Write-Host "Python     : $PythonExe"
Write-Host "SQL login  : $($env:WINDSOR_WIDGET_V2_DB_USERNAME)"
Write-Host "Config     : $ConfigPath"
Write-Host ""

& $PythonExe --version
& $PythonExe -m windsor_widget.cli check-config $ConfigPath

if ($LASTEXITCODE -ne 0) {
    throw "The Windsor Widget configuration check failed."
}

Write-Host ""
Write-Host "Useful commands:" -ForegroundColor Cyan
Write-Host "  python -m pytest -q"
Write-Host "  .\scripts\web_workflow.ps1 -Action Run"
Write-Host "  python -m windsor_widget.cli verify-reporting-data config\development.local.json"
