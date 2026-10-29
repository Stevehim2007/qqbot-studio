#requires -Version 5.1
<#
.SYNOPSIS
  Open or start the QQ Bot Studio multi-instance management console.

.DESCRIPTION
  Equivalent to ./console.sh on Linux.  This does not create a new bot
  instance.  It checks Docker Desktop, repairs known containers, ensures the
  hidden management service is running, and opens /console.
#>
[CmdletBinding()]
param(
    [string]$DataDir = '',
    [string]$Ip = '',
    [int]$Port = 0,
    [string]$PublicHost = '',
    [switch]$NoOpen,
    [switch]$NonInteractive,
    [switch]$RestartService
)

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $PSCommandPath
. (Join-Path $scriptDir 'common.ps1')

Write-Host '============================================================' -ForegroundColor Cyan
Write-Host '  QQ Bot Studio - multi-instance console (Windows)' -ForegroundColor Cyan
Write-Host '============================================================' -ForegroundColor Cyan
Write-Host 'Use windows\start.ps1 to create a new instance; this console only manages existing instances.' -ForegroundColor Yellow
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
# common.ps1 uses Start-Process -WindowStyle Hidden, PID/log files and /healthz.
Ensure-ManagementServer -PythonInfo $python -Settings $settings -ForceRestart:$RestartService

$resultText = Invoke-StudioHelper -PythonInfo $python -HelperArguments @(
    'prepare-console', '--data-dir', $settings.DataDir
) -Operation 'Prepare console'
$result = ConvertFrom-HelperJson -Text $resultText -Operation 'QQ Bot Studio helper response'

$failedRepairs = @()
if ($result.PSObject.Properties['repairs']) {
    $failedRepairs = @($result.repairs | Where-Object { $_.PSObject.Properties['ok'] -and -not [bool]$_.ok })
}
if ($failedRepairs.Count -gt 0) {
    Write-Warning 'Some existing containers could not be repaired. The console is still starting; inspect console cards and logs.'
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

Write-Section 'Console ready'
$consoleUrl = [string]$result.console_url
Write-Host ("Console : {0}" -f $consoleUrl) -ForegroundColor Green
$instanceCount = 0
if ($result.settings.PSObject.Properties['instance_count']) {
    $instanceCount = [int]$result.settings.instance_count
}
Write-Host ("Instances: {0}" -f $instanceCount)
if ($instanceCount -eq 0) {
    Write-Host 'No instances exist yet. Run windows\start.ps1 to create the first one.' -ForegroundColor Yellow
}
Show-ServiceDetails -Settings $settings

if (-not $NoOpen) {
    $null = Open-StudioUrl -Url $consoleUrl
}
