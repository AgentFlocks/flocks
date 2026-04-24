# Tier-B / bundled-toolchain bootstrap: run after copying staging (tools\ + flocks\) to the target machine.
# Requires FLOCKS_INSTALL_ROOT (or -InstallRoot) pointing at the directory that contains tools\ and flocks\.
#
# Design goal: keep scripts\install.ps1 / install_zh.ps1 unaware of the bundled layout.
# This script is the only place that does the "glue" work — injecting tools\uv and tools\node
# into the User PATH and exposing tools\chrome under ~/.flocks/browser so the upstream
# installer naturally discovers them without any bundled-specific branches.
#
# Example (installer post-install or manual):
#   $env:FLOCKS_INSTALL_ROOT = "D:\Flocks"
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\windows\bootstrap-windows.ps1
#
# Optional: pass through -InstallTui to match scripts\install.ps1.

param(
    [string]$InstallRoot = $env:FLOCKS_INSTALL_ROOT,
    [switch]$InstallTui
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($InstallRoot)) {
    Write-Host "[flocks-bootstrap] error: set -InstallRoot or environment variable FLOCKS_INSTALL_ROOT to the install root (must contain tools\ and flocks\)." -ForegroundColor Red
    exit 1
}

$InstallRoot = $InstallRoot.TrimEnd('\', '/')
$env:FLOCKS_INSTALL_ROOT = $InstallRoot
$env:FLOCKS_REPO_ROOT = (Join-Path $InstallRoot "flocks")
$env:FLOCKS_NODE_HOME = (Join-Path $InstallRoot "tools\node")

# Allow install.ps1 to skip its Administrator assertion — Inno Setup installs to
# {localappdata} with PrivilegesRequired=lowest, so bootstrap runs as the regular user.
$env:FLOCKS_SKIP_ADMIN_CHECK = "1"

if ([string]::IsNullOrWhiteSpace($env:FLOCKS_INSTALL_LANGUAGE)) {
    $env:FLOCKS_INSTALL_LANGUAGE = "zh-CN"
}
if ([string]::IsNullOrWhiteSpace($env:FLOCKS_UV_DEFAULT_INDEX)) {
    $env:FLOCKS_UV_DEFAULT_INDEX = "https://mirrors.aliyun.com/pypi/simple"
}
if ([string]::IsNullOrWhiteSpace($env:FLOCKS_NPM_REGISTRY)) {
    $env:FLOCKS_NPM_REGISTRY = "https://registry.npmmirror.com/"
}

function Add-UserPathEntryIfMissing {
    param([string]$Entry)

    if ([string]::IsNullOrWhiteSpace($Entry)) { return }

    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ([string]::IsNullOrWhiteSpace($userPath)) {
        $userPath = ""
    }
    $existing = $userPath.Split(';') | Where-Object { $_ -and ($_.TrimEnd('\','/')).ToLower() -eq $Entry.TrimEnd('\','/').ToLower() }
    if (-not $existing) {
        $updated = if ([string]::IsNullOrWhiteSpace($userPath)) { $Entry } else { "$Entry;$userPath" }
        [Environment]::SetEnvironmentVariable("Path", $updated, "User")
        Write-Host "[flocks-bootstrap] added to User PATH: $Entry"
    }

    # Also make the entry available to the current process so install.ps1's
    # `Test-Command uv` / `npm.cmd` probes succeed immediately.
    $processPath = $env:Path
    if (-not ($processPath -split ';' | Where-Object { ($_.TrimEnd('\','/')).ToLower() -eq $Entry.TrimEnd('\','/').ToLower() })) {
        $env:Path = "$Entry;$processPath"
    }
}

function Resolve-ChromeExecutablePath {
    param([string]$BrowserRoot)

    if ([string]::IsNullOrWhiteSpace($BrowserRoot) -or -not (Test-Path $BrowserRoot)) {
        return $null
    }

    $preferred = @(Get-ChildItem -Path $BrowserRoot -Recurse -Filter "chrome.exe" -File -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match 'chrome-win' })
    if ($preferred) {
        return $preferred[0].FullName
    }

    $fallback = Get-ChildItem -Path $BrowserRoot -Recurse -Filter "chrome.exe" -File -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($fallback) {
        return $fallback.FullName
    }

    return $null
}

# 1) Surface bundled uv / node so install.ps1's Test-Command "uv" / "npm.cmd" are satisfied
#    without install.ps1 ever referencing FLOCKS_INSTALL_ROOT.
$bundledUv = Join-Path $InstallRoot "tools\uv"
if (Test-Path (Join-Path $bundledUv "uv.exe")) {
    Add-UserPathEntryIfMissing -Entry $bundledUv
}
else {
    Write-Host "[flocks-bootstrap] warning: bundled uv not found at $bundledUv" -ForegroundColor Yellow
}

$bundledNode = Join-Path $InstallRoot "tools\node"
if (Test-Path (Join-Path $bundledNode "npm.cmd")) {
    Add-UserPathEntryIfMissing -Entry $bundledNode
}
else {
    Write-Host "[flocks-bootstrap] warning: bundled node not found at $bundledNode" -ForegroundColor Yellow
}

# 2) Expose bundled Chrome for Testing under ~/.flocks/browser so install.ps1's
#    Resolve-ChromeForTestingPath finds it and skips the real download.
#    Prefer a directory junction (fast, no disk duplication) and fall back to copy.
$bundledChrome = Join-Path $InstallRoot "tools\chrome"
if (Test-Path $bundledChrome) {
    $browserDir = Join-Path $HOME ".flocks\browser"
    if (-not (Test-Path $browserDir)) {
        New-Item -ItemType Directory -Path $browserDir -Force | Out-Null
    }
    $target = Join-Path $browserDir "bundled"

    $needsLink = $true
    if (Test-Path $target) {
        $existing = Get-Item -Path $target -Force -ErrorAction SilentlyContinue
        if ($existing -and $existing.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            # Already a junction — leave it in place.
            $needsLink = $false
        }
        else {
            # Plain directory from an earlier run — remove and recreate as junction.
            Remove-Item -Path $target -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    if ($needsLink) {
        & cmd /c "mklink /J `"$target`" `"$bundledChrome`"" | Out-Null
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $target)) {
            Write-Host "[flocks-bootstrap] junction failed, falling back to copy for bundled Chrome" -ForegroundColor Yellow
            Copy-Item -Path $bundledChrome -Destination $target -Recurse -Force
        }
        else {
            Write-Host "[flocks-bootstrap] linked bundled Chrome: $target -> $bundledChrome"
        }
    }

    $bundledChromeExecutable = Resolve-ChromeExecutablePath -BrowserRoot $bundledChrome
    if ([string]::IsNullOrWhiteSpace($bundledChromeExecutable)) {
        Write-Host "[flocks-bootstrap] warning: chrome.exe not found under bundled Chrome at $bundledChrome" -ForegroundColor Yellow
    }
    else {
        $env:FLOCKS_BROWSER_EXECUTABLE_OVERRIDE = $bundledChromeExecutable
        Write-Host "[flocks-bootstrap] configured bundled browser override: $bundledChromeExecutable"
    }
}
else {
    Write-Host "[flocks-bootstrap] note: bundled chrome directory not present at $bundledChrome" -ForegroundColor Yellow
}

# 3) Hand off to the regular installer. install_zh.ps1 sees a standard source checkout
#    (FLOCKS_REPO_ROOT) plus uv/node already on PATH and Chrome under ~/.flocks/browser.
$installer = Join-Path $InstallRoot "flocks\scripts\install_zh.ps1"
if (-not (Test-Path $installer)) {
    Write-Host "[flocks-bootstrap] error: installer not found: $installer" -ForegroundColor Red
    exit 1
}

$installerArgs = @()
if ($InstallTui) {
    $installerArgs += "-InstallTui"
}

& powershell -NoProfile -ExecutionPolicy Bypass -File $installer @installerArgs
exit $LASTEXITCODE
