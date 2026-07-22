param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Install", "Migrate", "CreateAdmin", "CreateUser", "ListUsers", "Run")]
    [string]$Action,

    [string]$Config = "config\development.local.json",
    [string]$Username = "brad",
    [string]$DisplayName = "Brad Mayze",
    [string]$Email = "",
    [ValidateSet("admin", "procurement", "read_only")]
    [string]$Role = "admin",
    [string]$BindAddress = "0.0.0.0",
    [int]$Port = 8080
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $ProjectRoot

function Invoke-Python {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed with exit code $LASTEXITCODE."
    }
}

function Ensure-DatabaseCredentials {
    if (-not $env:WINDSOR_WIDGET_V2_DB_USERNAME) {
        $env:WINDSOR_WIDGET_V2_DB_USERNAME = "WindsorWidgetV2_Migrator"
    }
    if (-not $env:WINDSOR_WIDGET_V2_DB_PASSWORD) {
        $securePassword = Read-Host "Enter the WindsorWidgetV2_Migrator SQL password" -AsSecureString
        $env:WINDSOR_WIDGET_V2_DB_PASSWORD = [System.Net.NetworkCredential]::new(
            "",
            $securePassword
        ).Password
    }
}

function Ensure-WebSecret {
    $secretPath = Join-Path $ProjectRoot "config\web_secret.local.txt"
    if (-not (Test-Path $secretPath)) {
        $bytes = New-Object byte[] 48
        $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        try {
            $rng.GetBytes($bytes)
        }
        finally {
            $rng.Dispose()
        }
        [Convert]::ToBase64String($bytes) | Set-Content -Path $secretPath -Encoding ascii -NoNewline
        Write-Host "Created private web session secret: $secretPath"
    }
    $env:WINDSOR_WIDGET_WEB_SECRET = (Get-Content $secretPath -Raw).Trim()
}

try {
    switch ($Action) {
        "Install" {
            Invoke-Python -Arguments @("-m", "pip", "install", "-e", ".[dev]")
            Write-Host "Web dependencies installed."
        }
        "Migrate" {
            Ensure-DatabaseCredentials
            Invoke-Python -Arguments @("-m", "windsor_widget.cli", "setup-dev-database", $Config)
        }
        "CreateAdmin" {
            Ensure-DatabaseCredentials
            $securePassword = Read-Host "Enter the new Windsor Widget web password" -AsSecureString
            $env:WINDSOR_WIDGET_INITIAL_PASSWORD = [System.Net.NetworkCredential]::new(
                "",
                $securePassword
            ).Password
            try {
                $pythonArgs = @(
                    "-m", "windsor_widget.web.manage", "create-user",
                    "--config", $Config,
                    "--username", $Username,
                    "--display-name", $DisplayName,
                    "--role", "admin"
                )
                if ($Email) { $pythonArgs += @("--email", $Email) }
                Invoke-Python -Arguments $pythonArgs
            }
            finally {
                Remove-Item Env:WINDSOR_WIDGET_INITIAL_PASSWORD -ErrorAction SilentlyContinue
            }
        }
        "CreateUser" {
            Ensure-DatabaseCredentials
            $securePassword = Read-Host "Enter the new Windsor Widget web password" -AsSecureString
            $env:WINDSOR_WIDGET_INITIAL_PASSWORD = [System.Net.NetworkCredential]::new(
                "",
                $securePassword
            ).Password
            try {
                $pythonArgs = @(
                    "-m", "windsor_widget.web.manage", "create-user",
                    "--config", $Config,
                    "--username", $Username,
                    "--display-name", $DisplayName,
                    "--role", $Role
                )
                if ($Email) { $pythonArgs += @("--email", $Email) }
                Invoke-Python -Arguments $pythonArgs
            }
            finally {
                Remove-Item Env:WINDSOR_WIDGET_INITIAL_PASSWORD -ErrorAction SilentlyContinue
            }
        }
        "ListUsers" {
            Ensure-DatabaseCredentials
            Invoke-Python -Arguments @("-m", "windsor_widget.web.manage", "list-users", "--config", $Config)
        }
        "Run" {
            Ensure-DatabaseCredentials
            Ensure-WebSecret
            $computerName = $env:COMPUTERNAME
            Write-Host ""
            Write-Host "Windsor Widget is starting..."
            Write-Host "This PC:  http://localhost:$Port"
            Write-Host "Office PCs: http://$computerName`:$Port"
            Write-Host "Press Ctrl+C to stop the development server."
            Write-Host ""
            $pythonArgs = @(
                "-m", "windsor_widget.web.server",
                "--config", $Config,
                "--host", $BindAddress,
                "--port", "$Port"
            )
            Invoke-Python -Arguments $pythonArgs
        }
    }
}
finally {
    Pop-Location
}
