# Flocks Installer for Windows
# Usage: powershell -c "irm https://raw.githubusercontent.com/AgentFlocks/Flocks/main/install.ps1 | iex"
#        powershell -c "& ([scriptblock]::Create((irm https://raw.githubusercontent.com/AgentFlocks/Flocks/main/install.ps1))) -Version main -InstallTui"

param(
    [switch]$InstallTui,
    [string]$Version = $env:VERSION,
    [switch]$Help
)

$ErrorActionPreference = "Stop"

$RepoSlug = if ([string]::IsNullOrWhiteSpace($env:FLOCKS_REPO_SLUG)) { "AgentFlocks/Flocks" } else { $env:FLOCKS_REPO_SLUG }
$DefaultBranch = if ([string]::IsNullOrWhiteSpace($env:FLOCKS_DEFAULT_BRANCH)) { "main" } else { $env:FLOCKS_DEFAULT_BRANCH }
$DefaultInstallDir = Join-Path (Get-Location) "flocks"
$InstallDir = if ([string]::IsNullOrWhiteSpace($env:FLOCKS_INSTALL_DIR)) { $DefaultInstallDir } else { $env:FLOCKS_INSTALL_DIR }

function Test-IsWindowsPlatform {
    return [System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT
}

function Test-IsAdministrator {
    if (-not (Test-IsWindowsPlatform)) {
        return $true
    }

    try {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = New-Object Security.Principal.WindowsPrincipal($identity)
        return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    }
    catch {
        return $false
    }
}

function Assert-Administrator {
    if (Test-IsAdministrator) {
        return
    }

    Fail "Administrator privileges are required. Reopen PowerShell as Administrator and rerun this installer."
}

function Write-Info {
    param([string]$Message)
    Write-Host "[flocks-bootstrap] $Message"
}

function Fail {
    param([string]$Message)
    Write-Host "[flocks-bootstrap] error: $Message" -ForegroundColor Red
    exit 1
}

if ([string]::IsNullOrWhiteSpace($Version)) {
    $Version = $DefaultBranch
}

function Show-Usage {
    Write-Host "Usage: install.ps1 [-InstallTui] [-Version <tag-or-branch>] [-Help]"
    Write-Host ""
    Write-Host "Bootstrap installer for Flocks."
    Write-Host "This script downloads the GitHub source archive to a temporary directory,"
    Write-Host "copies it to a persistent install location, and delegates to scripts/install.ps1."
    Write-Host "By default it creates a 'flocks' subdirectory under the current directory."
    Write-Host "Run this installer in a PowerShell window opened as Administrator."
    Write-Host ""
    Write-Host "Remote usage:"
    Write-Host '  powershell -c "irm https://raw.githubusercontent.com/AgentFlocks/Flocks/main/install.ps1 | iex"'
    Write-Host '  powershell -c "& ([scriptblock]::Create((irm https://raw.githubusercontent.com/AgentFlocks/Flocks/main/install.ps1))) -Version main -InstallTui"'
    Write-Host ""
    Write-Host "Options:"
    Write-Host "  -InstallTui          Also install TUI dependencies."
    Write-Host "  -Version <value>     Install from a Git tag or branch. Defaults to: $DefaultBranch"
    Write-Host "  -Help                Show this help message."
    Write-Host ""
    Write-Host "Environment variables:"
    Write-Host "  VERSION                Same as -Version."
Write-Host "  FLOCKS_INSTALL_DIR     Override persistent install location. Defaults to: $InstallDir"
    Write-Host "  FLOCKS_REPO_SLUG       Override GitHub repo, e.g. owner/repo."
    Write-Host "  FLOCKS_DEFAULT_BRANCH  Override default branch. Defaults to: $DefaultBranch"
}

function New-TemporaryDirectory {
    $basePath = [System.IO.Path]::GetTempPath()
    $name = [System.IO.Path]::GetRandomFileName()
    $path = Join-Path $basePath $name
    New-Item -ItemType Directory -Path $path -Force | Out-Null
    return $path
}

function Unblock-InstallFiles {
    param([string]$TargetDir)

    if ([string]::IsNullOrWhiteSpace($TargetDir) -or -not (Test-Path $TargetDir)) {
        return
    }

    try {
        Get-ChildItem -Path $TargetDir -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue
    }
    catch {
    }
}

function Invoke-WorkspaceInstaller {
    param(
        [string]$InstallerPath,
        [string[]]$InstallerArgs = @()
    )

    if ([string]::IsNullOrWhiteSpace($InstallerPath)) {
        Fail "Installer path is empty."
    }

    & powershell -NoProfile -ExecutionPolicy Bypass -File $InstallerPath @InstallerArgs
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

function Get-ArchiveCandidateUrls {
    $baseUrl = "https://github.com/$RepoSlug/archive/refs"

    if ($Version -eq $DefaultBranch) {
        return @("$baseUrl/heads/$Version.zip")
    }

    return @(
        "$baseUrl/tags/$Version.zip",
        "$baseUrl/heads/$Version.zip"
    )
}

function Download-Archive {
    param([string]$ArchivePath)

    $lastErrorMessage = $null

    foreach ($url in Get-ArchiveCandidateUrls) {
        Write-Info "Trying source archive URL: $url"
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $ArchivePath
            return $url
        }
        catch {
            $lastErrorMessage = $_.Exception.Message
        }
    }

    Fail "Failed to download source archive for version '$Version' from GitHub. Last error: $lastErrorMessage"
}

function Resolve-ProjectRoot {
    param([string]$TempDir)

    $candidate = Get-ChildItem -Path $TempDir -Directory | Where-Object {
        Test-Path (Join-Path $_.FullName "scripts\install.ps1")
    } | Select-Object -First 1

    if ($candidate) {
        return $candidate.FullName
    }

    return $null
}

function Main {
    if ($Help) {
        Show-Usage
        return
    }

    Assert-Administrator

    $tempDir = New-TemporaryDirectory
    $archivePath = Join-Path $tempDir "flocks.zip"

    try {
        Write-Info "Repository: $RepoSlug"
        Write-Info "Version: $Version"
        Write-Info "Temporary directory: $tempDir"

        $downloadUrl = Download-Archive -ArchivePath $archivePath

        Write-Info "Extracting source archive..."
        Expand-Archive -Path $archivePath -DestinationPath $tempDir -Force

        $projectRoot = Resolve-ProjectRoot -TempDir $tempDir
        if ([string]::IsNullOrWhiteSpace($projectRoot)) {
            Fail "Archive extracted, but scripts\install.ps1 was not found."
        }

        $installParent = Split-Path -Parent $InstallDir
        if (-not [string]::IsNullOrWhiteSpace($installParent)) {
            New-Item -ItemType Directory -Path $installParent -Force | Out-Null
        }
        if (Test-Path $InstallDir) {
            Remove-Item -Path $InstallDir -Recurse -Force
        }
        Copy-Item -Path $projectRoot -Destination $InstallDir -Recurse -Force
        Unblock-InstallFiles -TargetDir $InstallDir

        $installerPath = Join-Path $InstallDir "scripts\install.ps1"
        Write-Info "Downloaded from: $downloadUrl"
        Write-Info "Install directory: $InstallDir"
        Write-Info "Delegating to installer: $installerPath"

        $installerArgs = @()
        if ($InstallTui) {
            $installerArgs += "-InstallTui"
        }

        Invoke-WorkspaceInstaller -InstallerPath $installerPath -InstallerArgs $installerArgs
    }
    finally {
        if (-not [string]::IsNullOrWhiteSpace($tempDir) -and (Test-Path $tempDir)) {
            Remove-Item -Path $tempDir -Recurse -Force
        }
    }
}

Main
