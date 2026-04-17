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
$env:FLOCKS_REPO_ROOT = (Join-Path $InstallRoot "flocks")
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
