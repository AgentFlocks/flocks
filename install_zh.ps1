# Flocks 中国用户安装脚本
# 用法: powershell -c "irm https://gitee.com/flocks/flocks/raw/main/install_zh.ps1 | iex"
#       powershell -c "& ([scriptblock]::Create((irm https://gitee.com/flocks/flocks/raw/main/install_zh.ps1))) -Version main -InstallTui"

param(
    [switch]$InstallTui,
    [string]$Version = $env:VERSION,
    [switch]$Help
)

$ErrorActionPreference = "Stop"

$RepoSlug = if ([string]::IsNullOrWhiteSpace($env:FLOCKS_REPO_SLUG)) { "flocks/flocks" } else { $env:FLOCKS_REPO_SLUG }
$DefaultBranch = if ([string]::IsNullOrWhiteSpace($env:FLOCKS_DEFAULT_BRANCH)) { "main" } else { $env:FLOCKS_DEFAULT_BRANCH }
$DefaultInstallDir = Join-Path (Get-Location) "flocks"
$InstallDir = if ([string]::IsNullOrWhiteSpace($env:FLOCKS_INSTALL_DIR)) { $DefaultInstallDir } else { $env:FLOCKS_INSTALL_DIR }
$RawInstallZhShUrl = if ([string]::IsNullOrWhiteSpace($env:FLOCKS_RAW_INSTALL_ZH_SH_URL)) { "https://gitee.com/flocks/flocks/raw/main/install_zh.sh" } else { $env:FLOCKS_RAW_INSTALL_ZH_SH_URL }
$RawInstallZhPs1Url = if ([string]::IsNullOrWhiteSpace($env:FLOCKS_RAW_INSTALL_ZH_PS1_URL)) { "https://gitee.com/flocks/flocks/raw/main/install_zh.ps1" } else { $env:FLOCKS_RAW_INSTALL_ZH_PS1_URL }

function Write-Info {
    param([string]$Message)
    Write-Host "[flocks-bootstrap-zh] $Message"
}

function Fail {
    param([string]$Message)
    Write-Host "[flocks-bootstrap-zh] 错误: $Message" -ForegroundColor Red
    exit 1
}

if ([string]::IsNullOrWhiteSpace($Version)) {
    $Version = $DefaultBranch
}

function Show-Usage {
    Write-Host "用法: install_zh.ps1 [-InstallTui] [-Version <标签或分支>] [-Help]"
    Write-Host ""
    Write-Host "Flocks 中国用户一键安装脚本。"
    Write-Host "该脚本会从 Gitee 下载仓库源码压缩包到临时目录，复制到持久化安装目录后，转交 scripts/install_zh.ps1。"
    Write-Host "默认会在当前目录下创建 'flocks' 子目录。"
    Write-Host ""
    Write-Host "远程使用："
    Write-Host "  curl -fsSL $RawInstallZhShUrl | bash"
    Write-Host '  powershell -c "irm ' + $RawInstallZhPs1Url + ' | iex"'
    Write-Host '  powershell -c "& ([scriptblock]::Create((irm ' + $RawInstallZhPs1Url + '))) -Version main -InstallTui"'
    Write-Host ""
    Write-Host "选项："
    Write-Host "  -InstallTui          同时安装 TUI 依赖。"
    Write-Host "  -Version <value>     指定标签或分支。默认值: $DefaultBranch"
    Write-Host "  -Help                显示帮助信息。"
    Write-Host ""
    Write-Host "环境变量："
    Write-Host "  VERSION                等同于 -Version。"
    Write-Host "  FLOCKS_INSTALL_DIR     指定持久化安装目录。默认值: $InstallDir"
    Write-Host "  FLOCKS_REPO_SLUG       指定 Gitee 仓库，例如 owner/repo。"
    Write-Host "  FLOCKS_DEFAULT_BRANCH  指定默认分支。默认值: $DefaultBranch"
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
        Fail "安装脚本路径为空。"
    }

    & powershell -NoProfile -ExecutionPolicy Bypass -File $InstallerPath @InstallerArgs
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

function Get-ArchiveCandidateUrls {
    return @("https://gitee.com/$RepoSlug/archive/refs/tags/$Version.zip")
}

function Download-Archive {
    param([string]$ArchivePath)

    $lastErrorMessage = $null

    foreach ($url in Get-ArchiveCandidateUrls) {
        Write-Info "尝试下载源码压缩包: $url"
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $ArchivePath
            return $url
        }
        catch {
            $lastErrorMessage = $_.Exception.Message
        }
    }

    Fail "无法从 Gitee 下载版本 '$Version' 的源码压缩包。最后错误: $lastErrorMessage"
}

function Resolve-ProjectRoot {
    param([string]$TempDir)

    $candidate = Get-ChildItem -Path $TempDir -Directory | Where-Object {
        Test-Path (Join-Path $_.FullName "scripts\install_zh.ps1")
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

    $tempDir = New-TemporaryDirectory
    $archivePath = Join-Path $tempDir "flocks.zip"

    try {
        Write-Info "仓库: $RepoSlug"
        Write-Info "版本: $Version"
        Write-Info "临时目录: $tempDir"

        $downloadUrl = Download-Archive -ArchivePath $archivePath

        Write-Info "正在解压源码压缩包..."
        Expand-Archive -Path $archivePath -DestinationPath $tempDir -Force

        $projectRoot = Resolve-ProjectRoot -TempDir $tempDir
        if ([string]::IsNullOrWhiteSpace($projectRoot)) {
            Fail "压缩包已解压，但未找到 scripts\install_zh.ps1。"
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

        $installerPath = Join-Path $InstallDir "scripts\install_zh.ps1"
        Write-Info "下载来源: $downloadUrl"
        Write-Info "安装目录: $InstallDir"
        Write-Info "开始执行: $installerPath"

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
