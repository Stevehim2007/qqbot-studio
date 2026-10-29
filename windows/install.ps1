#requires -Version 5.1
<#
.SYNOPSIS
  Install/check QQ Bot Studio prerequisites on Windows 10/11 and start it.

.DESCRIPTION
  Checks Windows, Python 3.10+, WSL2 status, Docker Desktop, Docker Compose v2
  and the Linux Docker engine.  It can install missing Python/Docker Desktop
  with winget after confirmation.  It then creates the per-user data directory,
  persists first-run network settings in studio.json, optionally adds a
  Private-profile Windows Firewall rule, and invokes start.ps1.
#>
[CmdletBinding()]
param(
    [string]$DataDir = '',
    [string]$Ip = '',
    [int]$Port = 0,
    [string]$PublicHost = '',
    [switch]$NoStart,
    [switch]$NoOpen,
    [switch]$NonInteractive,
    [switch]$InstallMissing,
    [switch]$AddFirewallRule
)

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $PSCommandPath
. (Join-Path $scriptDir 'common.ps1')

Write-Host '============================================================' -ForegroundColor Cyan
Write-Host '  QQ Bot Studio - Windows initial port' -ForegroundColor Cyan
Write-Host '  Python console + static UI + Docker Desktop/WSL2 backend' -ForegroundColor Cyan
Write-Host '============================================================' -ForegroundColor Cyan
Write-Host 'Status: initial Windows port. Real Docker Desktop E2E has NOT been verified in the build environment.' -ForegroundColor Yellow
Write-Host 'Security: the management service defaults to 0.0.0.0. Restrict access with Windows Firewall/VPN.' -ForegroundColor Yellow
Write-Host ''

if ($env:OS -ne 'Windows_NT') {
    throw 'This script must run on Windows 10 or Windows 11.'
}
try {
    $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
    Write-Host ("Windows: {0} (build {1})" -f $os.Caption, $os.BuildNumber)
    if ([string]$os.Caption -notmatch 'Windows 10|Windows 11') {
        Write-Warning 'This initial port targets Windows 10/11. Other Windows editions may work but are unverified.'
    }
} catch {
    Write-Warning 'Could not read the Windows version via CIM; continuing.'
}

if (Test-IsAdministrator) {
    Write-Warning 'You are running in an elevated PowerShell. Run the normal QQ Bot Studio commands as your regular desktop user so LocalAppData and Docker Desktop belong to the right account.'
}

Write-Section 'WSL2 status'
if (Test-WslAvailable) {
    Write-Host 'WSL is available. Docker Desktop should use the WSL2 backend for Linux containers.' -ForegroundColor Green
} else {
    Write-Warning 'WSL2 status is not available yet.'
    Write-Host 'Docker Desktop needs WSL2 on Windows 10/11, or its supported Hyper-V backend.'
    Write-Host 'To install/update WSL2, open an elevated PowerShell and run:'
    Write-Host '  wsl --install --no-distribution'
    Write-Host '  wsl --update'
    Write-Host 'Then reboot if Windows asks. Do not run Docker Desktop inside a manual WSL distro.'
    if ($InstallMissing) {
        Write-Warning 'WSL installation is intentionally not automated here because it requires elevation and usually a reboot. Install it, reboot, and rerun install.ps1.'
    }
}

Write-Section 'Python 3.10+'
Update-ProcessPath
$python = Find-PythonInfo
if ($null -eq $python) {
    Write-Warning 'Python 3.10 or newer was not found.'
    $installPython = $InstallMissing.IsPresent
    if (-not $installPython) {
        $installPython = Confirm-YesNo -Prompt 'Install Python 3.12 with winget now?' -DefaultYes
    }
    if ($installPython) {
        Install-WingetPackage -PackageId 'Python.Python.3.12' -DisplayName 'Python 3.12'
        $python = Find-PythonInfo
    }
}
Assert-PythonInfo -PythonInfo $python
Write-Host ("Python: {0} (implementation: {1})" -f $python.Version, $python.File) -ForegroundColor Green

Write-Section 'Docker Desktop and Compose v2'
Update-ProcessPath
$desktopExe = Find-DockerDesktopExe
$dockerCommand = Get-Command docker.exe -ErrorAction SilentlyContinue
if ($null -eq $dockerCommand) {
    $dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
}
if ($null -eq $dockerCommand -and $null -eq $desktopExe) {
    Write-Warning 'Docker Desktop and the Docker CLI were not found.'
    $installDocker = $InstallMissing.IsPresent
    if (-not $installDocker) {
        $installDocker = Confirm-YesNo -Prompt 'Install Docker Desktop with winget now? Docker Desktop licensing terms apply.' -DefaultYes
    }
    if ($installDocker) {
        Install-WingetPackage -PackageId 'Docker.DockerDesktop' -DisplayName 'Docker Desktop'
        Start-Sleep -Seconds 3
        Update-ProcessPath
        $desktopExe = Find-DockerDesktopExe
    }
}
if ($null -eq $desktopExe) {
    if ($null -ne $dockerCommand) {
        Write-Warning 'Docker CLI was found but Docker Desktop was not detected. The script will test the configured Docker engine, but Docker Desktop is the supported target.'
    } else {
        throw 'Docker Desktop is still unavailable. Install Docker Desktop 4.x manually from https://www.docker.com/products/docker-desktop/, start it once, finish its first-run prompts, then rerun windows\install.ps1.'
    }
}
Ensure-DockerDesktopEngine -TimeoutSeconds 240

Write-Section 'Data directory'
$resolvedDataDir = Resolve-StudioDataDir -DataDir $DataDir
Ensure-Directory -Path $resolvedDataDir
Write-Host ("Data directory: {0}" -f $resolvedDataDir)
Write-Host 'New files are created by the Python/Compose core. Windows ACLs apply instead of Linux chmod 0600; local administrators may be able to read them.'

Write-Section 'First-run network configuration'
$settings = Initialize-StudioConfig `
    -PythonInfo $python `
    -DataDir $resolvedDataDir `
    -Ip $Ip `
    -Port $Port `
    -PublicHost $PublicHost `
    -NonInteractive:$NonInteractive
Write-Host ("studio.json    : {0}" -f $settings.SettingsPath) -ForegroundColor Green
Write-Host ("Listen address : {0}:{1}" -f $settings.BindHost, $settings.BindPort)
Write-Host ("Public host    : {0}" -f $settings.PublicHost)

if ($AddFirewallRule) {
    New-ManagementFirewallRule -Port $settings.BindPort -RemoteAddress 'LocalSubnet'
} elseif ($settings.BindHost -eq '0.0.0.0') {
    Write-Host ''
    Write-Warning 'The management service binds all interfaces, but no Windows Firewall rule was created.'
    Write-Host 'To allow only the local subnet on a Private-profile network, run in elevated PowerShell:'
    Write-Host ("  New-NetFirewallRule -DisplayName 'QQ Bot Studio Management Console (TCP {0})' -Direction Inbound -Action Allow -Protocol TCP -LocalPort {0} -Profile Private -RemoteAddress LocalSubnet" -f $settings.BindPort)
    Write-Host 'Instance WebUI/noVNC ports are selected later and are not automatically opened.'
}

if ($NoStart) {
    Write-Host ''
    Write-Host 'Install/check complete. To create the first bot instance run:' -ForegroundColor Green
    Write-Host ("  .\windows\start.ps1 -DataDir '{0}'" -f $settings.DataDir)
    return
}

Write-Section 'Starting first instance'
$startScript = Join-Path $scriptDir 'start.ps1'
& $startScript `
    -DataDir $settings.DataDir `
    -Ip $settings.BindHost `
    -Port $settings.BindPort `
    -PublicHost $settings.PublicHost `
    -NonInteractive:$NonInteractive `
    -NoOpen:$NoOpen
