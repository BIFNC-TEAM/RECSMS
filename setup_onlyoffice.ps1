# Run in an Administrator PowerShell for first-time WSL installation. Never reboots automatically.
[CmdletBinding()]
param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
function Test-SetupNativeCommand {
    param([string]$FilePath, [string[]]$Arguments)
    # Windows PowerShell 5.1 converts redirected native stderr into ErrorRecords.
    # These probes expect failure while WSL / Docker Engine is not installed or ready.
    $ErrorActionPreference = 'Continue'
    & $FilePath @Arguments *> $null
    return $LASTEXITCODE -eq 0
}
$rootPath = $PSScriptRoot
$adminMode = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$dockerCandidates = @(
    "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe",
    "$env:ProgramFiles\Docker\Docker\resources\bin\docker.exe"
)
$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
$dockerPath = if ($dockerCommand) { $dockerCommand.Source } else { $dockerCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1 }
$wslInstalled = Test-SetupNativeCommand -FilePath 'wsl.exe' -Arguments @('--version')
if ($CheckOnly) {
    [pscustomobject]@{Administrator = $adminMode; WSLInstalled = $wslInstalled; DockerCLI = $dockerPath; LocalConfig = (Test-Path -LiteralPath "$rootPath\data\onlyoffice.env")} | Format-List | Out-Host
    exit 0
}
if (-not $wslInstalled) {
    if (-not $adminMode) {
        throw 'WSL is not installed. Open PowerShell as Administrator and run this script again. This script never restarts Windows automatically.'
    }
    Write-Host 'Enabling WSL and Virtual Machine Platform; Windows may require a restart.'
    $wslFeature = Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -All -NoRestart
    $vmFeature = Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -All -NoRestart
    & winget.exe install --id Microsoft.WSL --exact --silent --accept-source-agreements --accept-package-agreements --disable-interactivity
    if ($LASTEXITCODE -notin @(0, 3010, -1978335189)) { throw "WSL installation failed, code $LASTEXITCODE. Re-run after restarting if Windows features are pending." }
    if ($wslFeature.RestartNeeded -or $vmFeature.RestartNeeded) {
        Write-Host 'Windows restart required. Save your work, restart manually, then run this same script again.'
        exit 3010
    }
}
if (-not $dockerPath) {
    Write-Host 'Installing Docker Desktop for the current user.'
    & winget.exe install --id Docker.DockerDesktop --exact --silent --accept-source-agreements --accept-package-agreements --disable-interactivity --override 'install --user --quiet --accept-license --backend=wsl-2'
    if ($LASTEXITCODE -notin @(0, 3010, -1978335189)) { throw "Docker Desktop installation failed, code $LASTEXITCODE." }
    $dockerPath = $dockerCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $dockerPath) { throw 'Docker CLI was not found after installation. Restart Windows, then run this script again.' }
}
$desktopCandidates = @("$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe", "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe")
$desktopPath = $desktopCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not (Get-Process 'Docker Desktop' -ErrorAction SilentlyContinue) -and $desktopPath) {
    Start-Process -FilePath $desktopPath -WindowStyle Hidden | Out-Null
}
Write-Host 'Waiting for Docker Engine (up to four minutes).'
$engineReady = $false
for ($i = 0; $i -lt 48; $i++) {
    if (Test-SetupNativeCommand -FilePath $dockerPath -Arguments @('info', '--format', '{{.ServerVersion}}')) { $engineReady = $true; break }
    Start-Sleep -Seconds 5
}
if (-not $engineReady) { throw 'Docker Engine is unavailable. Open Docker Desktop to inspect its status; a Windows restart or first-run action may be required.' }
$pythonPath = Join-Path (Split-Path $rootPath -Parent) '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { $pythonPath = (Get-Command python -ErrorAction Stop).Source }
Push-Location $rootPath
try {
    & $pythonPath -u configure_local_office.py
    if ($LASTEXITCODE -ne 0) { throw 'Platform connection configuration failed.' }
    # Bind the existing native server so the private Docker gateway can reach its callbacks.
    $listener = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($listener -and $listener.LocalAddress -eq '127.0.0.1') {
        $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
        $asset = Invoke-WebRequest 'http://127.0.0.1:8765/office.js' -UseBasicParsing
        $expected = [IO.File]::ReadAllText((Join-Path $rootPath 'web\office.js'))
        if ($owner.CommandLine -notmatch 'server\.py' -or $asset.Content -ne $expected) { throw 'Port 8765 belongs to an unverified process; refusing to restart it.' }
        Stop-Process -Id $listener.OwningProcess -ErrorAction Stop
        $listener = $null
    }
    if (-not $listener) {
        Start-Process -FilePath $pythonPath -ArgumentList '-u', 'server.py', '--host', '0.0.0.0', '--port', '8765' -WorkingDirectory $rootPath -WindowStyle Hidden -RedirectStandardOutput "$rootPath\data\server.stdout.log" -RedirectStandardError "$rootPath\data\server.stderr.log" | Out-Null
    }
    & $dockerPath compose --project-name standards-office --env-file data/onlyoffice.env -f compose.onlyoffice.local.yaml up -d
    if ($LASTEXITCODE -ne 0) { throw 'ONLYOFFICE container startup failed. Configuration and existing platform data have been retained.' }
    Write-Host 'Waiting for Document Server health check (up to five minutes).'
    $officeReady = $false
    for ($i = 0; $i -lt 60; $i++) {
        try { if ((Invoke-WebRequest 'http://127.0.0.1:8080/healthcheck' -UseBasicParsing -TimeoutSec 3).Content.Trim() -eq 'true') { $officeReady = $true; break } } catch {}
        Start-Sleep -Seconds 5
    }
    if (-not $officeReady) { throw 'Document Server is not healthy yet. Inspect with docker compose --env-file data/onlyoffice.env -f compose.onlyoffice.local.yaml logs.' }
    Write-Host 'ONLYOFFICE is healthy. Open http://127.0.0.1:8765 and use Word / ONLYOFFICE. Verify a real edit and final save before relying on the integration.'
} finally { Pop-Location }
