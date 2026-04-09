param(
    [switch]$InstallTui,
    [switch]$Help
)

$ErrorActionPreference = "Stop"
$RawInstallZhShUrl = if ([string]::IsNullOrWhiteSpace($env:FLOCKS_RAW_INSTALL_ZH_SH_URL)) { "https://gitee.com/flocks/flocks/raw/main/install_zh.sh" } else { $env:FLOCKS_RAW_INSTALL_ZH_SH_URL }
$RawInstallZhPs1Url = if ([string]::IsNullOrWhiteSpace($env:FLOCKS_RAW_INSTALL_ZH_PS1_URL)) { "https://gitee.com/flocks/flocks/raw/main/install_zh.ps1" } else { $env:FLOCKS_RAW_INSTALL_ZH_PS1_URL }

function Show-Usage {
    Write-Host "用法: .\scripts\install_zh.ps1 [-InstallTui] [-Help]"
    Write-Host ""
    Write-Host "Flocks 中国用户源码安装脚本。"
    Write-Host "该脚本会默认使用国内软件源，并转交 .\scripts\install.ps1 执行实际安装流程。"
    Write-Host ""
    Write-Host "默认软件源："
    Write-Host "  PyPI: https://mirrors.aliyun.com/pypi/simple"
    Write-Host "  npm : https://registry.npmmirror.com/"
    Write-Host ""
    Write-Host "一键安装入口："
    Write-Host "  curl -fsSL $RawInstallZhShUrl | bash"
    Write-Host '  powershell -c "irm ' + $RawInstallZhPs1Url + ' | iex"'
    Write-Host ""
    Write-Host "选项："
    Write-Host "  -InstallTui  同时安装 TUI 依赖"
    Write-Host "  -Help        显示帮助信息"
}

function Set-CnInstallerEnvironment {
    if ([string]::IsNullOrWhiteSpace($env:FLOCKS_INSTALL_REPO_URL)) {
        $env:FLOCKS_INSTALL_REPO_URL = "https://gitee.com/flocks/flocks.git"
    }
    if ([string]::IsNullOrWhiteSpace($env:FLOCKS_RAW_INSTALL_SH_URL)) {
        $env:FLOCKS_RAW_INSTALL_SH_URL = $RawInstallZhShUrl
    }
    if ([string]::IsNullOrWhiteSpace($env:FLOCKS_RAW_INSTALL_PS1_URL)) {
        $env:FLOCKS_RAW_INSTALL_PS1_URL = $RawInstallZhPs1Url
    }
    if ([string]::IsNullOrWhiteSpace($env:FLOCKS_UV_DEFAULT_INDEX)) {
        $env:FLOCKS_UV_DEFAULT_INDEX = "https://mirrors.aliyun.com/pypi/simple"
    }
    if ([string]::IsNullOrWhiteSpace($env:FLOCKS_NPM_REGISTRY)) {
        $env:FLOCKS_NPM_REGISTRY = "https://registry.npmmirror.com/"
    }
    if ([string]::IsNullOrWhiteSpace($env:PUPPETEER_CHROME_DOWNLOAD_BASE_URL)) {
        $env:PUPPETEER_CHROME_DOWNLOAD_BASE_URL = "https://cdn.npmmirror.com/binaries/chrome-for-testing"
    }
    if ([string]::IsNullOrWhiteSpace($env:FLOCKS_NODEJS_MANUAL_DOWNLOAD_URL)) {
        $env:FLOCKS_NODEJS_MANUAL_DOWNLOAD_URL = "https://nodejs.org/zh-cn/download"
    }
}

function Main {
    if ($Help) {
        Show-Usage
        return
    }

    Set-CnInstallerEnvironment

    $installerPath = Join-Path $PSScriptRoot "install.ps1"
    $installerArgs = @()
    if ($InstallTui) {
        $installerArgs += "-InstallTui"
    }

    & powershell -NoProfile -ExecutionPolicy Bypass -File $installerPath @installerArgs
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

Main
