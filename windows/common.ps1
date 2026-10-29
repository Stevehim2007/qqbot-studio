# Shared helpers for QQ Bot Studio on Windows 10/11.
# This file is dot-sourced by install.ps1, start.ps1 and console.ps1.
# It deliberately uses Windows PowerShell 5.1-compatible syntax.

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

# The Python core emits UTF-8 diagnostics.  Force UTF-8 for redirected logs
# and helper output even on Windows installations whose legacy ANSI code page
# cannot represent Chinese characters.
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

try {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
} catch {
    # Console encoding changes are cosmetic; never fail a run over them.
}

$script:ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$script:WindowsDir = $PSScriptRoot
$script:HelperScript = Join-Path $PSScriptRoot 'studio_helper.py'
$script:DefaultBindHost = '0.0.0.0'
$script:DefaultBindPort = 8765

function Resolve-StudioDataDir {
    param([string]$DataDir = '')

    $candidate = $DataDir
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        $candidate = [Environment]::GetEnvironmentVariable('QQBOT_STUDIO_DATA_DIR')
    }
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        $localAppData = [Environment]::GetEnvironmentVariable('LOCALAPPDATA')
        if (-not [string]::IsNullOrWhiteSpace($localAppData)) {
            $candidate = Join-Path $localAppData 'QQBotStudio\data'
        } else {
            $candidate = Join-Path $HOME 'AppData\Local\QQBotStudio\data'
        }
    }

    $unresolved = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($candidate.Trim())
    return [System.IO.Path]::GetFullPath($unresolved)
}

function Ensure-Directory {
    param([string]$Path)
    New-Item -ItemType Directory -Force -Path $Path | Out-Null
}

function Write-Section {
    param([string]$Text)
    Write-Host ''
    Write-Host "== $Text ==" -ForegroundColor Cyan
}

function Test-IsAdministrator {
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object System.Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-PythonCandidateVersion {
    param(
        [string]$FilePath,
        [string[]]$PrefixArgs = @()
    )

    try {
        $arguments = @($PrefixArgs) + @(
            '-c',
            'import sys; print("%d.%d.%d" % sys.version_info[:3])'
        )
        $output = & $FilePath @arguments 2>$null
        $exitCode = $LASTEXITCODE
        if ($exitCode -ne 0) {
            return $null
        }
        $text = (@($output) | ForEach-Object { [string]$_ }) -join "`n"
        $match = [regex]::Match($text, '(\d+)\.(\d+)\.(\d+)')
        if (-not $match.Success) {
            return $null
        }
        $major = [int]$match.Groups[1].Value
        $minor = [int]$match.Groups[2].Value
        $patch = [int]$match.Groups[3].Value
        return [pscustomobject]@{
            File = $FilePath
            Prefix = @($PrefixArgs)
            Version = ('{0}.{1}.{2}' -f $major, $minor, $patch)
            Major = $major
            Minor = $minor
        }
    } catch {
        return $null
    }
}

function Find-PythonInfo {
    $candidates = New-Object System.Collections.ArrayList
    $commandNames = @('python.exe', 'python', 'py.exe', 'py')
    foreach ($name in $commandNames) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($null -eq $command) {
            continue
        }
        $filePath = [string]$command.Source
        if ([string]::IsNullOrWhiteSpace($filePath)) {
            $filePath = [string]$command.Path
        }
        if ([string]::IsNullOrWhiteSpace($filePath)) {
            continue
        }
        if ($name -eq 'py.exe' -or $name -eq 'py') {
            foreach ($selector in @('-3.14', '-3.13', '-3.12', '-3.11', '-3.10', '-3')) {
                [void]$candidates.Add([pscustomobject]@{ File = $filePath; Prefix = @($selector) })
            }
        } else {
            [void]$candidates.Add([pscustomobject]@{ File = $filePath; Prefix = @() })
        }
    }

    $localPythonRoots = @()
    $localAppData = [Environment]::GetEnvironmentVariable('LOCALAPPDATA')
    if (-not [string]::IsNullOrWhiteSpace($localAppData)) {
        $localPythonRoots += (Join-Path $localAppData 'Programs\Python')
    }
    $programFiles = [Environment]::GetEnvironmentVariable('ProgramFiles')
    if (-not [string]::IsNullOrWhiteSpace($programFiles)) {
        $localPythonRoots += $programFiles
    }
    foreach ($root in $localPythonRoots) {
        $paths = @()
        if ($root -match 'Programs\\Python$') {
            $paths = @(
                (Join-Path $root 'Python314\python.exe'),
                (Join-Path $root 'Python313\python.exe'),
                (Join-Path $root 'Python312\python.exe'),
                (Join-Path $root 'Python311\python.exe'),
                (Join-Path $root 'Python310\python.exe')
            )
        } else {
            $paths = @(
                (Join-Path $root 'Python314\python.exe'),
                (Join-Path $root 'Python313\python.exe'),
                (Join-Path $root 'Python312\python.exe'),
                (Join-Path $root 'Python311\python.exe'),
                (Join-Path $root 'Python310\python.exe')
            )
        }
        foreach ($pythonPath in $paths) {
            if (Test-Path -LiteralPath $pythonPath -PathType Leaf) {
                [void]$candidates.Add([pscustomobject]@{ File = $pythonPath; Prefix = @() })
            }
        }
    }

    $seen = @{}
    $supported = New-Object System.Collections.ArrayList
    foreach ($candidate in $candidates) {
        $key = ([string]$candidate.File) + '|' + ((@($candidate.Prefix)) -join ' ')
        if ($seen.ContainsKey($key)) {
            continue
        }
        $seen[$key] = $true
        $info = Get-PythonCandidateVersion -FilePath ([string]$candidate.File) -PrefixArgs @($candidate.Prefix)
        if ($null -ne $info -and $info.Major -eq 3 -and $info.Minor -ge 10) {
            [void]$supported.Add($info)
        }
    }

    if ($supported.Count -eq 0) {
        return $null
    }
    return ($supported | Sort-Object -Property Major, Minor -Descending | Select-Object -First 1)
}

function Assert-PythonInfo {
    param([pscustomobject]$PythonInfo)
    if ($null -eq $PythonInfo) {
        throw 'Python 3.10 or newer was not found. Run windows\install.ps1 or install Python from https://www.python.org/downloads/windows/ and enable "Add python.exe to PATH".'
    }
}

function Invoke-PythonCapture {
    param(
        [pscustomobject]$PythonInfo,
        [string[]]$Arguments,
        [string]$Operation = 'Python command'
    )

    $allArguments = @()
    if ($null -ne $PythonInfo.Prefix -and @($PythonInfo.Prefix).Count -gt 0) {
        $allArguments += @($PythonInfo.Prefix)
    }
    $allArguments += @($Arguments)

    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = & $PythonInfo.File @allArguments 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }

    $text = (@($output) | ForEach-Object { [string]$_ }) -join "`n"
    if ($exitCode -ne 0) {
        throw ("{0} failed with exit code {1}.`n{2}" -f $Operation, $exitCode, $text.Trim())
    }
    return $text.Trim()
}

function Invoke-StudioHelper {
    param(
        [pscustomobject]$PythonInfo,
        [string[]]$HelperArguments,
        [string]$Operation = 'QQ Bot Studio helper'
    )

    $arguments = @($script:HelperScript) + @($HelperArguments)
    return Invoke-PythonCapture -PythonInfo $PythonInfo -Arguments $arguments -Operation $Operation
}

function ConvertFrom-HelperJson {
    param(
        [string]$Text,
        [string]$Operation = 'QQ Bot Studio helper'
    )
    $lines = @($Text -split "`n")
    for ($index = $lines.Count - 1; $index -ge 0; $index--) {
        $line = ([string]$lines[$index]).Trim()
        if ($line.StartsWith('{') -and $line.EndsWith('}')) {
            try {
                return ($line | ConvertFrom-Json)
            } catch {
                # Keep looking: a warning line may resemble a JSON fragment.
            }
        }
    }
    throw ("{0} did not return valid JSON. Output:`n{1}" -f $Operation, $Text)
}

function Get-StudioSettingsJson {
    param(
        [pscustomobject]$PythonInfo,
        [string]$DataDir
    )

    $text = Invoke-StudioHelper -PythonInfo $PythonInfo -HelperArguments @(
        'get-settings', '--data-dir', $DataDir
    ) -Operation 'Read studio.json'
    return (ConvertFrom-HelperJson -Text $text -Operation 'Read studio.json')
}

function Get-DetectedPublicHost {
    param([pscustomobject]$PythonInfo)
    $text = Invoke-StudioHelper -PythonInfo $PythonInfo -HelperArguments @(
        'detect-public-host'
    ) -Operation 'Detect public host'
    $result = ConvertFrom-HelperJson -Text $text -Operation 'Detect public host'
    $value = ''
    if ($result.PSObject.Properties['public_host']) {
        $value = [string]$result.public_host
    }
    if ([string]::IsNullOrWhiteSpace($value)) {
        return '127.0.0.1'
    }
    return $value.Trim()
}

function Assert-PortNumber {
    param([int]$Port)
    if ($Port -lt 1 -or $Port -gt 65535) {
        throw ("Port must be between 1 and 65535; got {0}." -f $Port)
    }
}

function Normalize-PublicHost {
    param([string]$PublicHost)
    $value = $PublicHost.Trim()
    if ([string]::IsNullOrWhiteSpace($value)) {
        return ''
    }
    if ($value -match '^[A-Za-z][A-Za-z0-9+.-]*://') {
        throw 'PublicHost must be a host name or IP address without http:// or https://.'
    }
    if ($value.Contains('/')) {
        throw 'PublicHost must not contain a path; use only a host name or IP address.'
    }
    if ($value -match '\s') {
        throw 'PublicHost must not contain whitespace.'
    }
    if ($value -eq '0.0.0.0' -or $value -eq '::') {
        throw 'PublicHost must be a reachable browser IP or DNS name, not an all-interfaces bind address.'
    }
    if ($value -match '^\[[^\]]+\]:\d+$' -or $value -match '^[^:]+:\d+$') {
        throw 'PublicHost must not include a port; the management port is supplied separately with -Port.'
    }
    return $value
}

function Get-DefaultPublicHostForBind {
    param(
        [pscustomobject]$PythonInfo,
        [string]$BindHost
    )
    $value = $BindHost.Trim().ToLowerInvariant()
    if ($value -eq '' -or $value -eq '0.0.0.0' -or $value -eq '::') {
        return (Get-DetectedPublicHost -PythonInfo $PythonInfo)
    }
    if ($value -eq 'localhost' -or $value -eq '127.0.0.1') {
        return '127.0.0.1'
    }
    if ($value -eq '::1' -or $value -eq '[::1]') {
        return '[::1]'
    }
    return $BindHost.Trim()
}

function Read-ValueOrDefault {
    param(
        [string]$Prompt,
        [string]$DefaultValue
    )
    $answer = Read-Host ("{0} [{1}]" -f $Prompt, $DefaultValue)
    if ([string]::IsNullOrWhiteSpace($answer)) {
        return $DefaultValue
    }
    return $answer.Trim()
}

function Read-PortOrDefault {
    param([int]$DefaultPort)
    while ($true) {
        $answer = Read-Host ("Management console port [{0}]" -f $DefaultPort)
        if ([string]::IsNullOrWhiteSpace($answer)) {
            return $DefaultPort
        }
        $parsed = 0
        if ([int]::TryParse($answer.Trim(), [ref]$parsed) -and $parsed -ge 1 -and $parsed -le 65535) {
            return $parsed
        }
        Write-Warning 'Port must be a number between 1 and 65535.'
    }
}

function Initialize-StudioConfig {
    param(
        [pscustomobject]$PythonInfo,
        [string]$DataDir,
        [string]$Ip = '',
        [int]$Port = 0,
        [string]$PublicHost = '',
        [switch]$NonInteractive
    )

    Ensure-Directory -Path $DataDir
    $settingsPath = Join-Path $DataDir 'studio.json'
    $existing = $null
    if (Test-Path -LiteralPath $settingsPath -PathType Leaf) {
        try {
            $existing = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
        } catch {
            throw ("studio.json exists but cannot be parsed: {0}. Repair or remove it after backing up." -f $settingsPath)
        }
    }

    $firstRun = $true
    if ($null -ne $existing -and $existing.PSObject.Properties['first_run_complete']) {
        $firstRun = -not [bool]$existing.first_run_complete
    }

    $bindHost = $script:DefaultBindHost
    $bindPort = $script:DefaultBindPort
    $savedPublicHost = ''
    if ($null -ne $existing) {
        if ($existing.PSObject.Properties['bind_host'] -and -not [string]::IsNullOrWhiteSpace([string]$existing.bind_host)) {
            $bindHost = [string]$existing.bind_host
        }
        if ($existing.PSObject.Properties['bind_port']) {
            $existingPort = 0
            if ([int]::TryParse([string]$existing.bind_port, [ref]$existingPort)) {
                $bindPort = $existingPort
            }
        }
        if ($existing.PSObject.Properties['public_host']) {
            $savedPublicHost = [string]$existing.public_host
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($Ip)) {
        $bindHost = $Ip.Trim()
    }
    if ($Port -ne 0) {
        Assert-PortNumber -Port $Port
        $bindPort = $Port
    }
    if (-not [string]::IsNullOrWhiteSpace($PublicHost)) {
        $savedPublicHost = Normalize-PublicHost -PublicHost $PublicHost
    }

    $publicWasEmpty = [string]::IsNullOrWhiteSpace($savedPublicHost)
    if ($firstRun -and -not $NonInteractive) {
        Write-Host ''
        Write-Host 'First-run network configuration will be saved to studio.json.' -ForegroundColor Yellow
        Write-Host '0.0.0.0 listens on all interfaces. Prefer 127.0.0.1 unless you need LAN access.' -ForegroundColor Yellow
        $bindHost = Read-ValueOrDefault -Prompt 'Management service listen IP' -DefaultValue $bindHost
        $bindPort = Read-PortOrDefault -DefaultPort $bindPort
        if ([string]::IsNullOrWhiteSpace($savedPublicHost)) {
            $savedPublicHost = Get-DefaultPublicHostForBind -PythonInfo $PythonInfo -BindHost $bindHost
        }
        $savedPublicHost = Read-ValueOrDefault -Prompt 'Public IP or DNS name used in printed links' -DefaultValue $savedPublicHost
    }

    Assert-PortNumber -Port $bindPort
    if ([string]::IsNullOrWhiteSpace($bindHost)) {
        throw 'Management listen IP cannot be empty.'
    }
    if ([string]::IsNullOrWhiteSpace($savedPublicHost)) {
        $savedPublicHost = Get-DefaultPublicHostForBind -PythonInfo $PythonInfo -BindHost $bindHost
    }
    $savedPublicHost = Normalize-PublicHost -PublicHost $savedPublicHost

    $overrideRequested = (-not [string]::IsNullOrWhiteSpace($Ip)) -or ($Port -ne 0) -or (-not [string]::IsNullOrWhiteSpace($PublicHost))
    $shouldConfigure = $firstRun -or $overrideRequested -or $publicWasEmpty -or ($null -eq $existing)
    if ($shouldConfigure) {
        $configureText = Invoke-StudioHelper -PythonInfo $PythonInfo -HelperArguments @(
            'configure',
            '--data-dir', $DataDir,
            '--bind-host', $bindHost,
            '--bind-port', [string]$bindPort,
            '--public-host', $savedPublicHost
        ) -Operation 'Persist studio.json'
        $saved = ConvertFrom-HelperJson -Text $configureText -Operation 'Persist studio.json'
    } else {
        $saved = Get-StudioSettingsJson -PythonInfo $PythonInfo -DataDir $DataDir
    }

    return [pscustomobject]@{
        DataDir = $DataDir
        SettingsPath = $settingsPath
        FirstRun = $firstRun
        BindHost = [string]$saved.bind_host
        BindPort = [int]$saved.bind_port
        PublicHost = [string]$saved.public_host
    }
}

function Get-StudioBaseUrl {
    param([pscustomobject]$Settings)
    $hostValue = [string]$Settings.PublicHost
    if ([string]::IsNullOrWhiteSpace($hostValue)) {
        $hostValue = '127.0.0.1'
    }
    if ($hostValue.Contains(':') -and -not $hostValue.StartsWith('[')) {
        $hostValue = '[' + $hostValue + ']'
    }
    return ('http://{0}:{1}' -f $hostValue, [int]$Settings.BindPort)
}

function Update-ProcessPath {
    $machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $currentPath = $env:Path
    $parts = @()
    foreach ($part in @($machinePath, $userPath, $currentPath)) {
        if (-not [string]::IsNullOrWhiteSpace($part)) {
            $parts += ($part -split ';')
        }
    }
    $dockerBin = $null
    $programFiles = [Environment]::GetEnvironmentVariable('ProgramFiles')
    if (-not [string]::IsNullOrWhiteSpace($programFiles)) {
        $candidate = Join-Path $programFiles 'Docker\Docker\resources\bin'
        if (Test-Path -LiteralPath $candidate -PathType Container) {
            $dockerBin = $candidate
        }
    }
    $localAppData = [Environment]::GetEnvironmentVariable('LOCALAPPDATA')
    if ([string]::IsNullOrWhiteSpace($dockerBin) -and -not [string]::IsNullOrWhiteSpace($localAppData)) {
        $candidate = Join-Path $localAppData 'Docker\Docker\resources\bin'
        if (Test-Path -LiteralPath $candidate -PathType Container) {
            $dockerBin = $candidate
        }
    }
    if (-not [string]::IsNullOrWhiteSpace($dockerBin)) {
        $parts = @($dockerBin) + $parts
    }
    $env:Path = (($parts | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique) -join ';')
}

function Find-DockerDesktopExe {
    $candidates = New-Object System.Collections.ArrayList
    $programFiles = [Environment]::GetEnvironmentVariable('ProgramFiles')
    if (-not [string]::IsNullOrWhiteSpace($programFiles)) {
        [void]$candidates.Add((Join-Path $programFiles 'Docker\Docker\Docker Desktop.exe'))
    }
    $programFilesX86 = [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
    if (-not [string]::IsNullOrWhiteSpace($programFilesX86)) {
        [void]$candidates.Add((Join-Path $programFilesX86 'Docker\Docker\Docker Desktop.exe'))
    }
    $localAppData = [Environment]::GetEnvironmentVariable('LOCALAPPDATA')
    if (-not [string]::IsNullOrWhiteSpace($localAppData)) {
        [void]$candidates.Add((Join-Path $localAppData 'Docker\Docker Desktop.exe'))
        [void]$candidates.Add((Join-Path $localAppData 'Docker\Docker\Docker Desktop.exe'))
        [void]$candidates.Add((Join-Path $localAppData 'Programs\Docker\Docker\Docker Desktop.exe'))
    }
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }
    return $null
}

function Test-DockerEngine {
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $null = & docker info --format '{{.ServerVersion}}' 2>&1
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $previousPreference
    }
}

function Assert-DockerCompose {
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = & docker compose version 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($exitCode -ne 0) {
        $text = (@($output) | ForEach-Object { [string]$_ }) -join "`n"
        throw ("Docker Compose v2 is unavailable. Start Docker Desktop and verify docker compose version.`n{0}" -f $text.Trim())
    }
}

function Assert-DockerLinuxEngine {
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = & docker info --format '{{.OSType}}' 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    $text = (@($output) | ForEach-Object { [string]$_ }) -join "`n"
    if ($exitCode -ne 0) {
        throw ("Could not read the Docker engine OS type.`n{0}" -f $text.Trim())
    }
    $osType = $text.Trim().ToLowerInvariant()
    if ($osType -ne 'linux') {
        throw ("Docker Desktop is using '{0}' containers. QQ Bot Studio requires Linux containers. Right-click the Docker tray icon and select 'Switch to Linux containers'." -f $osType)
    }
}

function Wait-DockerEngine {
    param([int]$TimeoutSeconds = 180)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-DockerEngine) {
            return $true
        }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Ensure-DockerDesktopEngine {
    param([int]$TimeoutSeconds = 180)

    Update-ProcessPath
    $dockerCommand = Get-Command docker.exe -ErrorAction SilentlyContinue
    if ($null -eq $dockerCommand) {
        $dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
    }
    $desktopExe = Find-DockerDesktopExe

    if ($null -eq $dockerCommand) {
        if ($null -eq $desktopExe) {
            throw 'Docker Desktop was not found. Run windows\install.ps1 or install Docker Desktop with WSL2 backend first.'
        }
        Write-Host 'Docker CLI is not yet on PATH; starting Docker Desktop and refreshing PATH...' -ForegroundColor Yellow
        Start-Process -FilePath $desktopExe | Out-Null
        Update-ProcessPath
        Start-Sleep -Seconds 5
    }

    if (-not (Test-DockerEngine)) {
        if ($null -ne $desktopExe) {
            $desktopProcess = Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue
            if ($null -eq $desktopProcess) {
                Write-Host 'Starting Docker Desktop (hidden start is not used; its tray UI is required)...' -ForegroundColor Yellow
                Start-Process -FilePath $desktopExe | Out-Null
            }
        }
        Write-Host ("Waiting up to {0}s for the Docker Desktop Linux engine..." -f $TimeoutSeconds) -ForegroundColor Yellow
        if (-not (Wait-DockerEngine -TimeoutSeconds $TimeoutSeconds)) {
            throw 'Docker Desktop Engine did not become ready. Open Docker Desktop, finish first-run/login/license prompts, ensure it uses Linux containers, then retry. See windows\README.md troubleshooting.'
        }
    }

    Assert-DockerLinuxEngine
    Assert-DockerCompose
    Write-Host 'Docker Desktop, Compose v2 and the Linux engine are ready.' -ForegroundColor Green
}

function Test-WslAvailable {
    $wslCommand = Get-Command wsl.exe -ErrorAction SilentlyContinue
    if ($null -eq $wslCommand) {
        return $false
    }
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $null = & wsl.exe --status 2>&1
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $previousPreference
    }
}

function Install-WingetPackage {
    param(
        [string]$PackageId,
        [string]$DisplayName
    )
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($null -eq $winget) {
        throw ("winget is not available; install {0} manually and rerun install.ps1." -f $DisplayName)
    }
    Write-Host ("Installing {0} with winget..." -f $DisplayName) -ForegroundColor Cyan
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = & $winget.Source install --id $PackageId -e --source winget --accept-package-agreements --accept-source-agreements 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($exitCode -ne 0) {
        $text = (@($output) | ForEach-Object { [string]$_ }) -join "`n"
        throw ("winget failed to install {0} (exit {1}).`n{2}" -f $DisplayName, $exitCode, $text.Trim())
    }
    Update-ProcessPath
}

function Confirm-YesNo {
    param(
        [string]$Prompt,
        [switch]$DefaultYes
    )
    if (-not [Environment]::UserInteractive) {
        return $false
    }
    $suffix = '[y/N]'
    if ($DefaultYes) {
        $suffix = '[Y/n]'
    }
    $answer = Read-Host ("{0} {1}" -f $Prompt, $suffix)
    if ([string]::IsNullOrWhiteSpace($answer)) {
        return [bool]$DefaultYes
    }
    return ($answer.Trim() -match '^(?i:y|yes)$')
}

function New-ManagementFirewallRule {
    param(
        [int]$Port,
        [string]$RemoteAddress = 'LocalSubnet'
    )
    if (-not (Test-IsAdministrator)) {
        throw 'Creating a Windows Firewall rule requires an elevated PowerShell window.'
    }
    $displayName = ("QQ Bot Studio Management Console (TCP {0})" -f $Port)
    Get-NetFirewallRule -DisplayName $displayName -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
    New-NetFirewallRule -DisplayName $displayName -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -Profile Private -RemoteAddress $RemoteAddress | Out-Null
    Write-Host ("Added Private-profile firewall rule for TCP {0} from {1}." -f $Port, $RemoteAddress) -ForegroundColor Green
}

function Get-ServerPidPath {
    param([string]$DataDir)
    return (Join-Path $DataDir 'server.pid')
}

function Get-RuntimeInfo {
    param([string]$DataDir)
    $runtimePath = Join-Path $DataDir 'server-runtime.json'
    if (-not (Test-Path -LiteralPath $runtimePath -PathType Leaf)) {
        return $null
    }
    try {
        return (Get-Content -LiteralPath $runtimePath -Raw | ConvertFrom-Json)
    } catch {
        return $null
    }
}

function Get-ManagedServerProcess {
    param([string]$DataDir)
    $pidPath = Get-ServerPidPath -DataDir $DataDir
    if (-not (Test-Path -LiteralPath $pidPath -PathType Leaf)) {
        return $null
    }
    $rawPid = ''
    try {
        $rawPid = (Get-Content -LiteralPath $pidPath -Raw).Trim()
    } catch {
        return $null
    }
    $serverPid = 0
    if (-not [int]::TryParse($rawPid, [ref]$serverPid)) {
        Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
        return $null
    }
    $process = Get-Process -Id $serverPid -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
        return $null
    }

    $commandLine = ''
    try {
        $cim = Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $serverPid) -ErrorAction Stop
        $commandLine = [string]$cim.CommandLine
    } catch {
        $commandLine = ''
    }
    if (-not [string]::IsNullOrWhiteSpace($commandLine) -and $commandLine -match 'qqbot_studio') {
        return $process
    }
    Write-Warning ("PID file {0} points to PID {1}, but its command line could not be verified as QQ Bot Studio. It will not be killed automatically." -f $pidPath, $serverPid)
    return $null
}

function Test-ManagementHealth {
    param([pscustomobject]$Settings)
    $probeHost = '127.0.0.1'
    $bindHost = [string]$Settings.BindHost
    if (-not [string]::IsNullOrWhiteSpace($bindHost) -and $bindHost -ne '0.0.0.0' -and $bindHost -ne '::' -and $bindHost -ne '') {
        $probeHost = $bindHost
        if ($probeHost.Contains(':') -and -not $probeHost.StartsWith('[')) {
            $probeHost = '[' + $probeHost + ']'
        }
    } elseif ($bindHost -eq '::') {
        $probeHost = '[::1]'
    }
    $url = ('http://{0}:{1}/healthz' -f $probeHost, [int]$Settings.BindPort)
    try {
        $result = Invoke-RestMethod -Uri $url -TimeoutSec 2 -UseBasicParsing
        if ($null -eq $result) {
            return $false
        }
        $hasOk = @($result.PSObject.Properties.Name) -contains 'ok'
        return ($hasOk -and [bool]$result.ok)
    } catch {
        return $false
    }
}

function Test-TcpPort {
    param(
        [string]$TargetHost,
        [int]$TargetPort
    )
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $task = $client.ConnectAsync($TargetHost, $TargetPort)
        if ($task.Wait(1000) -and $client.Connected) {
            return $true
        }
        return $false
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Get-ProbeHostForSettings {
    param([pscustomobject]$Settings)
    $bindHost = [string]$Settings.BindHost
    if ([string]::IsNullOrWhiteSpace($bindHost) -or $bindHost -eq '0.0.0.0' -or $bindHost -eq '::') {
        return '127.0.0.1'
    }
    return $bindHost
}

function Get-ServerLogTail {
    param(
        [string]$DataDir,
        [int]$Lines = 30
    )
    $parts = New-Object System.Collections.ArrayList
    foreach ($name in @('studio-server.log', 'studio-server.err.log')) {
        $path = Join-Path $DataDir $name
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            [void]$parts.Add(("--- {0} ---" -f $name))
            try {
                $tail = Get-Content -LiteralPath $path -Tail $Lines -Encoding UTF8 -ErrorAction Stop
                [void]$parts.Add(((@($tail) | ForEach-Object { [string]$_ }) -join "`n"))
            } catch {
                [void]$parts.Add('(log file is temporarily locked or unreadable)')
            }
        }
    }
    return ((@($parts) | ForEach-Object { [string]$_ }) -join "`n")
}

function Stop-ManagementServer {
    param([string]$DataDir)
    $process = Get-ManagedServerProcess -DataDir $DataDir
    $pidPath = Get-ServerPidPath -DataDir $DataDir
    if ($null -ne $process) {
        Write-Host ("Stopping QQ Bot Studio management service (PID {0})..." -f $process.Id) -ForegroundColor Yellow
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        $deadline = (Get-Date).AddSeconds(10)
        while ((Get-Date) -lt $deadline) {
            if ($null -eq (Get-Process -Id $process.Id -ErrorAction SilentlyContinue)) {
                break
            }
            Start-Sleep -Milliseconds 250
        }
    }
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
}

function ConvertTo-ProcessArgument {
    param([string]$Value)
    if ($null -eq $Value) {
        return '""'
    }
    if ($Value -notmatch '[\s"]' -and -not $Value.EndsWith('\')) {
        return $Value
    }
    $escaped = $Value -replace '(\\*)"', '$1$1\"'
    $escaped = $escaped -replace '(\\+)$', '$1$1'
    return ('"{0}"' -f $escaped)
}

function Start-ManagementServer {
    param(
        [pscustomobject]$PythonInfo,
        [pscustomobject]$Settings,
        [string]$DataDir
    )

    $stdoutLog = Join-Path $DataDir 'studio-server.log'
    $stderrLog = Join-Path $DataDir 'studio-server.err.log'
    foreach ($logPath in @($stdoutLog, $stderrLog)) {
        if (Test-Path -LiteralPath $logPath -PathType Leaf) {
            Move-Item -LiteralPath $logPath -Destination ($logPath + '.1') -Force
        }
    }

    $arguments = @()
    if ($null -ne $PythonInfo.Prefix -and @($PythonInfo.Prefix).Count -gt 0) {
        $arguments += @($PythonInfo.Prefix)
    }
    $arguments += @(
        '-u', '-m', 'qqbot_studio',
        '--host', [string]$Settings.BindHost,
        '--port', [string]$Settings.BindPort,
        '--data-dir', $DataDir,
        '--no-browser'
    )
    $argumentString = ((@($arguments) | ForEach-Object { ConvertTo-ProcessArgument -Value ([string]$_) }) -join ' ')

    Write-Host 'Starting management service with a hidden PowerShell child process...' -ForegroundColor Cyan
    $process = Start-Process -FilePath $PythonInfo.File `
        -ArgumentList $argumentString `
        -WorkingDirectory $script:ProjectRoot `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog `
        -WindowStyle Hidden `
        -PassThru

    $pidPath = Get-ServerPidPath -DataDir $DataDir
    Set-Content -LiteralPath $pidPath -Value ([string]$process.Id) -Encoding Ascii

    $healthy = $false
    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $deadline) {
        if (Test-ManagementHealth -Settings $Settings) {
            $healthy = $true
            break
        }
        if ($process.HasExited) {
            break
        }
        Start-Sleep -Milliseconds 500
    }

    if (-not $healthy) {
        $tail = Get-ServerLogTail -DataDir $DataDir -Lines 40
        if (-not $process.HasExited) {
            & taskkill.exe /PID $process.Id /T /F 2>$null | Out-Null
        }
        Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
        throw ("Management service did not become healthy. Logs: {0}`n{1}" -f $stdoutLog, $tail)
    }

    # If Python was launched through py.exe, the server writes the real Python
    # PID to server-runtime.json.  Prefer that value for future restarts.
    $runtime = Get-RuntimeInfo -DataDir $DataDir
    if ($null -ne $runtime -and $null -ne $runtime.PSObject.Properties['pid']) {
        $runtimePid = 0
        if ([int]::TryParse([string]$runtime.pid, [ref]$runtimePid) -and $runtimePid -gt 0) {
            Set-Content -LiteralPath $pidPath -Value ([string]$runtimePid) -Encoding Ascii
        }
    }

    Write-Host ("Management service is healthy (PID {0})." -f (Get-Content -LiteralPath $pidPath -Raw).Trim()) -ForegroundColor Green
}

function Ensure-ManagementServer {
    param(
        [pscustomobject]$PythonInfo,
        [pscustomobject]$Settings,
        [switch]$ForceRestart
    )

    $dataDir = [string]$Settings.DataDir
    if ($ForceRestart) {
        Stop-ManagementServer -DataDir $dataDir
    }

    if (-not $ForceRestart -and (Test-ManagementHealth -Settings $Settings)) {
        $runtime = Get-RuntimeInfo -DataDir $dataDir
        $runtimeMatches = $false
        if ($null -ne $runtime) {
            $runtimeHost = ''
            $runtimePort = 0
            $runtimeDataDir = ''
            if ($runtime.PSObject.Properties['host']) {
                $runtimeHost = [string]$runtime.host
            }
            if ($runtime.PSObject.Properties['port']) {
                $null = [int]::TryParse([string]$runtime.port, [ref]$runtimePort)
            }
            if ($runtime.PSObject.Properties['data_dir']) {
                $runtimeDataDir = [string]$runtime.data_dir
            }
            if ($runtimeHost -eq [string]$Settings.BindHost -and $runtimePort -eq [int]$Settings.BindPort -and $runtimeDataDir -eq $dataDir) {
                $runtimeMatches = $true
            }
        }
        if ($runtimeMatches) {
            Write-Host 'Management service is already healthy with matching settings.' -ForegroundColor Green
            return
        }
        $managed = Get-ManagedServerProcess -DataDir $dataDir
        if ($null -eq $managed) {
            throw ("Port {0} answers /healthz, but it is not verifiably managed by this data directory. Stop the other process or choose another port." -f $Settings.BindPort)
        }
        Stop-ManagementServer -DataDir $dataDir
    }

    $managed = Get-ManagedServerProcess -DataDir $dataDir
    if ($null -ne $managed) {
        # A managed process exists but is not healthy. Restart it so a crashed
        # or stale server does not leave the UI permanently unavailable.
        Stop-ManagementServer -DataDir $dataDir
    }

    $probeHost = Get-ProbeHostForSettings -Settings $Settings
    if (Test-TcpPort -TargetHost $probeHost -TargetPort ([int]$Settings.BindPort)) {
        throw ("Port {0} is already in use by another process. Stop that process or run start.ps1 -Port <free-port>." -f $Settings.BindPort)
    }

    Start-ManagementServer -PythonInfo $PythonInfo -Settings $Settings -DataDir $dataDir
}

function Show-ServiceDetails {
    param([pscustomobject]$Settings)
    $baseUrl = Get-StudioBaseUrl -Settings $Settings
    $pidPath = Get-ServerPidPath -DataDir $Settings.DataDir
    Write-Host ''
    Write-Host ("Management URL : {0}/console" -f $baseUrl) -ForegroundColor Green
    Write-Host ("Wizard URL     : {0}/wizard" -f $baseUrl) -ForegroundColor Green
    $probeHost = Get-ProbeHostForSettings -Settings $Settings
    if ($probeHost.Contains(':') -and -not $probeHost.StartsWith('[')) {
        $probeHost = '[' + $probeHost + ']'
    }
    Write-Host ("Health check   : http://{0}:{1}/healthz" -f $probeHost, $Settings.BindPort)
    Write-Host ("Listen address : {0}:{1}" -f $Settings.BindHost, $Settings.BindPort)
    Write-Host ("Data directory : {0}" -f $Settings.DataDir)
    Write-Host ("PID file       : {0}" -f $pidPath)
    Write-Host ("Logs           : {0}\studio-server.log" -f $Settings.DataDir)
    Write-Host ("                 {0}\studio-server.err.log" -f $Settings.DataDir)
    if ($Settings.BindHost -eq '0.0.0.0') {
        Write-Warning 'The management service listens on all interfaces. Use Windows Firewall rules, a VPN, or bind to 127.0.0.1.'
    }
}

function Open-StudioUrl {
    param([string]$Url)
    try {
        Start-Process -FilePath $Url | Out-Null
        return $true
    } catch {
        Write-Warning ("Could not open the default browser automatically. Open this URL manually: {0}" -f $Url)
        return $false
    }
}
