# Tier-B / bundled-toolchain bootstrap: run after copying staging (tools\ + flocks\) to the target machine.
# Requires FLOCKS_INSTALL_ROOT (or -InstallRoot) pointing at the directory that contains tools\ and flocks\.
#
# Example (installer post-install or manual):
#   $env:FLOCKS_INSTALL_ROOT = "D:\Flocks"
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap-windows.ps1
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

$installer = Join-Path $InstallRoot "flocks\scripts\install.ps1"
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
