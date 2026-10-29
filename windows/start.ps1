#requires -Version 5.1
<#
.SYNOPSIS
  Create a new QQ Bot Studio instance and start the hidden background service.

.DESCRIPTION
  Equivalent to ./start.sh on Linux.  Every run creates a brand-new isolated
  bot instance.  The management service uses a hidden PowerShell/Start-Process
  child, a PID file, two log files and an HTTP /healthz check.  It does not use
  screen, systemd or sudo.
#>
[CmdletBinding()]
param(
    [string]$DataDir = '',
    [string]$Ip = '',
    [int]$Port = 0,
    [string]$PublicHost = '',
    [string]$Name = '',
    [switch]$NoOpen,
    [switch]$NonInteractive,
    [switch]$RestartService
)

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $PSCommandPath
. (Join-Path $scriptDir 'common.ps1')

Write-Host '============================================================' -ForegroundColor Cyan
Write-Host '  QQ Bot Studio - start a new bot instance (Windows)' -ForegroundColor Cyan
Write-Host '============================================================' -ForegroundColor Cyan
Write-Host 'Every run creates a NEW isolated instance. Existing bots are managed with windows\console.ps1.' -ForegroundColor Yellow
Write-Host ''

$python = Find-PythonInfo
Assert-PythonInfo -PythonInfo $python
Write-Host ("Using {0} (Python {1})" -f $python.File, $python.Version) -ForegroundColor Green

$settings = Initialize-StudioConfig `
    -PythonInfo $python `
    -DataDir (Resolve-StudioDataDir -DataDir $DataDir) `
    -Ip $Ip `
    -Port $Port `
    -PublicHost $PublicHost `
    -NonInteractive:$NonInteractive

Ensure-DockerDesktopEngine -TimeoutSeconds 180
# Ensure-ManagementServer (common.ps1) starts the service through
# Start-Process -WindowStyle Hidden, records server.pid, redirects both logs
# and waits for the /healthz endpoint before returning.
Ensure-ManagementServer -PythonInfo $python -Settings $settings -ForceRestart:$RestartService

$helperArguments = @('create-instance', '--data-dir', $settings.DataDir)
if (-not [string]::IsNullOrWhiteSpace($Name)) {
    $helperArguments += @('--name', $Name)
}
$resultText = Invoke-StudioHelper -PythonInfo $python -HelperArguments $helperArguments -Operation 'Create bot instance'
$result = ConvertFrom-HelperJson -Text $resultText -Operation 'QQ Bot Studio helper response'

$failedRepairs = @()
if ($result.PSObject.Properties['repairs']) {
    $failedRepairs = @($result.repairs | Where-Object { $_.PSObject.Properties['ok'] -and -not [bool]$_.ok })
}
if ($failedRepairs.Count -gt 0) {
    Write-Warning 'Some existing containers could not be repaired. The new instance was still created; inspect the console/logs.'
    foreach ($repair in $failedRepairs) {
        $repairName = ''
        if ($repair.PSObject.Properties['name']) {
            $repairName = [string]$repair.name
        }
        $repairAction = ''
        if ($repair.PSObject.Properties['action']) {
            $repairAction = [string]$repair.action
        }
        $repairMessage = ''
        if ($repair.PSObject.Properties['message']) {
            $repairMessage = [string]$repair.message
        }
        Write-Warning ("[{0}] {1}: {2}" -f $repairAction, $repairName, $repairMessage)
    }
}

Write-Section 'New bot instance created'
Write-Host ("Instance : {0} ({1})" -f $result.instance.name, $result.instance.id) -ForegroundColor Green
Write-Host ("Wizard   : {0}" -f $result.wizard_url) -ForegroundColor Green
Write-Host ("Console  : {0}" -f $result.console_url) -ForegroundColor Green

Write-Host ''
Write-Host 'IMPORTANT - first login for every new instance:' -ForegroundColor Yellow
Write-Host '1. Open the wizard URL and complete the deployment form.' -ForegroundColor Yellow
Write-Host '2. Open the noVNC desktop from the wizard when deployment finishes.' -ForegroundColor Yellow
Write-Host '3. Scan the official QQ login QR code in that noVNC desktop.' -ForegroundColor Yellow
Write-Host '4. Complete device/email/SMS/slider verification manually in the QQ login window.' -ForegroundColor Yellow
Write-Host 'The tool never asks for or stores your QQ password. Reusing an instance may keep the saved login; a new instance does not.' -ForegroundColor Yellow

Show-ServiceDetails -Settings $settings

if (-not $NoOpen) {
    $null = Open-StudioUrl -Url ([string]$result.wizard_url)
}
