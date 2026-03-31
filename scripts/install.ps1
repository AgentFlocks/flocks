param(
    [switch]$InstallTui,
    [switch]$Help
)

$ErrorActionPreference = "Stop"

$RepoUrl = "https://github.com/AgentFlocks/Flocks.git"
$RawInstallShUrl = "https://raw.githubusercontent.com/AgentFlocks/Flocks/main/install.sh"
$RawInstallPs1Url = "https://raw.githubusercontent.com/AgentFlocks/Flocks/main/install.ps1"
$RootDir = $null
$MinNodeMajor = 22

function Write-Info {
    param([string]$Message)
    Write-Host "[flocks] $Message"
}

function Fail {
    param([string]$Message)
    Write-Host "[flocks] error: $Message" -ForegroundColor Red
    exit 1
}

function Test-Command {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Get-NodeMajorVersion {
    if (-not (Test-Command "node")) {
        return $null
    }

    try {
        $version = (& node -v 2>$null).Trim()
    }
    catch {
        return $null
    }

    if ([string]::IsNullOrWhiteSpace($version)) {
        return $null
    }

    $version = $version.TrimStart("v")
    $majorPart = ($version -split "\.")[0]
    if ($majorPart -match '^\d+$') {
        return [int]$majorPart
    }

    return $null
}

function Test-NodeVersionRequirement {
    $major = Get-NodeMajorVersion
    return ($null -ne $major -and $major -ge $MinNodeMajor)
}

function Add-PathEntry {
    param([string]$PathEntry)

    if ([string]::IsNullOrWhiteSpace($PathEntry) -or -not (Test-Path $PathEntry)) {
        return
    }

    $pathItems = $env:Path -split ";"
    if ($pathItems -contains $PathEntry) {
        return
    }

    $env:Path = "$PathEntry;$env:Path"
}

function Ensure-UserPathEntry {
    param([string]$PathEntry)

    if ([string]::IsNullOrWhiteSpace($PathEntry) -or -not (Test-Path $PathEntry)) {
        return
    }

    $userPath = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $entries = @()
    if (-not [string]::IsNullOrWhiteSpace($userPath)) {
        $entries = $userPath -split ";"
    }

    if ($entries -contains $PathEntry) {
        return
    }

    $newUserPath = if ([string]::IsNullOrWhiteSpace($userPath)) {
        $PathEntry
    }
    else {
        "$PathEntry;$userPath"
    }

    [System.Environment]::SetEnvironmentVariable("Path", $newUserPath, "User")
    Add-PathEntry $PathEntry
}

function Test-RepoRoot {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return $false
    }

    return (Test-Path (Join-Path $Path "pyproject.toml")) `
        -and (Test-Path (Join-Path $Path "flocks")) `
        -and (Test-Path (Join-Path $Path "tui")) `
        -and (Test-Path (Join-Path $Path "webui")) `
        -and (Test-Path (Join-Path $Path "scripts\install.ps1"))
}

function Resolve-RootDir {
    $candidates = @()

    if ($PSScriptRoot) {
        $candidates += (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    }

    $candidates += (Get-Location).Path

    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (Test-RepoRoot $candidate) {
            $script:RootDir = $candidate
            return $true
        }
    }

    return $false
}

function Show-CloneHintAndExit {
    Write-Host "[flocks] 当前未检测到 Flocks 仓库代码。"
    Write-Host ""
    Write-Host "如需源码安装，请先拉取代码，再执行安装："
    Write-Host ""
    Write-Host "  git clone $RepoUrl"
    Write-Host "  cd Flocks"
    Write-Host "  .\scripts\install.ps1"
    Write-Host ""
    Write-Host "或直接使用 GitHub 一键安装入口："
    Write-Host ""
    Write-Host "  curl -fsSL $RawInstallShUrl | bash"
    Write-Host "  iwr -useb $RawInstallPs1Url | iex"
    exit 1
}

function Refresh-Path {
    $userPath = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $machinePath = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $env:Path = "$userPath;$machinePath"

    $uvBin = Join-Path $HOME ".local\bin"
    $cargoBin = Join-Path $HOME ".cargo\bin"
    $bunBin = Join-Path $HOME ".bun\bin"
    $windowsAppsBin = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps"

    foreach ($pathEntry in @($uvBin, $cargoBin, $bunBin, $windowsAppsBin)) {
        Add-PathEntry $pathEntry
    }

    if (Test-Command "npm.cmd") {
        try {
            $npmPrefix = (& npm.cmd config get prefix 2>$null).Trim()
        }
        catch {
            $npmPrefix = ""
        }
        if (-not [string]::IsNullOrWhiteSpace($npmPrefix) -and $npmPrefix -notin @("undefined", "null")) {
            Add-PathEntry $npmPrefix
            Add-PathEntry (Join-Path $npmPrefix "bin")
        }
    }
}

function Show-Usage {
    Write-Host "Usage: .\scripts\install.ps1 [-InstallTui] [-Help]"
    Write-Host ""
    Write-Host "Options:"
    Write-Host "  -InstallTui  安装 TUI 依赖（此时会自动安装 bun）"
    Write-Host "  -Help        查看帮助"
}

function Get-ChocoCommand {
    foreach ($candidate in @("choco.exe", "choco")) {
        $command = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($command) {
            return $command.Source
        }
    }

    $knownPaths = @()

    if (-not [string]::IsNullOrWhiteSpace($env:ProgramData)) {
        $knownPaths += (Join-Path $env:ProgramData "chocolatey\bin\choco.exe")
    }

    if (-not [string]::IsNullOrWhiteSpace($env:ChocolateyInstall)) {
        $knownPaths += (Join-Path $env:ChocolateyInstall "bin\choco.exe")
    }

    foreach ($path in ($knownPaths | Select-Object -Unique)) {
        if (Test-Path $path) {
            return $path
        }
    }

    return $null
}

function Ensure-ChocolateyInstalled {
    $chocoPath = Get-ChocoCommand
    if ($chocoPath) {
        return $chocoPath
    }

    Write-Info "未检测到 Chocolatey，开始自动安装..."

    try {
        & powershell -NoProfile -ExecutionPolicy Bypass -Command "Set-ExecutionPolicy Bypass -Scope Process -Force; [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072; iwr https://community.chocolatey.org/install.ps1 -UseBasicParsing | iex" | Out-Host
        if ($LASTEXITCODE -ne 0) {
            return $null
        }
        Refresh-Path
    }
    catch {
        return $null
    }

    return (Get-ChocoCommand)
}

function Install-NodeJsWithChocolatey {
    $chocoPath = Ensure-ChocolateyInstalled
    if (-not $chocoPath) {
        Write-Info "Chocolatey 安装失败，无法自动安装 Node.js。"
        return $false
    }

    Write-Info "未检测到满足要求的 npm，尝试使用 Chocolatey 安装或升级 Node.js 24.14.0..."
    & $chocoPath install nodejs --version="24.14.0" -y | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-Info "Chocolatey 安装 Node.js 失败。"
        return $false
    }

    Refresh-Path
    return ((Test-Command "npm.cmd") -and (Test-NodeVersionRequirement))
}

function Ensure-NpmInstalled {
    if ((Test-Command "npm.cmd") -and (Test-NodeVersionRequirement)) {
        return
    }

    $currentMajor = Get-NodeMajorVersion
    if ($null -ne $currentMajor) {
        Write-Info "检测到当前 Node.js 版本为 v${currentMajor}，尝试升级到 Node.js $MinNodeMajor+..."
    }
    else {
        Write-Info "未检测到满足要求的 npm，尝试自动安装 Node.js..."
    }

    if (-not (Install-NodeJsWithChocolatey)) {
        Fail "未检测到满足要求的 npm，或当前 Node.js 版本低于 $MinNodeMajor，且无法通过 Chocolatey 自动安装 Node.js。请先从 https://nodejs.org/ 手动安装 Node.js $MinNodeMajor+（确保包含 npm），安装完成后重新打开 PowerShell 再重试。"
    }

    if (-not (Test-Command "npm.cmd")) {
        Fail "Node.js（包含 npm）安装完成后仍不可用，请检查 PATH。"
    }

    if (-not (Test-NodeVersionRequirement)) {
        Fail "检测到的 Node.js 版本过低。当前项目至少需要 Node.js $MinNodeMajor+，请安装或升级后重试。"
    }

    try {
        Write-Info "Node.js 版本: $((& node -v).Trim())"
        Write-Info "npm 版本: $((& npm.cmd -v).Trim())"
    }
    catch {
    }
}

function Install-Uv {
    if (Test-Command "uv") {
        return
    }

    Write-Info "未检测到 uv，开始自动安装..."
    powershell -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; irm https://astral.sh/uv/install.ps1 | iex"
    Refresh-Path

    if (-not (Test-Command "uv")) {
        Fail "uv 安装完成后仍不可用，请检查 PATH。"
    }
}

function Install-FlocksCli {
    Write-Info "安装 flocks 全局 CLI..."

    Push-Location $RootDir
    try {
        & uv tool install --editable $RootDir --force
        if ($LASTEXITCODE -ne 0) {
            Fail "flocks 全局 CLI 安装失败。"
        }
    }
    finally {
        Pop-Location
    }

    $toolBin = (& uv tool dir --bin 2>$null).Trim()
    if (-not [string]::IsNullOrWhiteSpace($toolBin) -and (Test-Path $toolBin)) {
        Ensure-UserPathEntry $toolBin
    }

    Refresh-Path
    if (-not (Test-Command "flocks")) {
        Fail "flocks CLI 安装完成后仍不可用，请检查 PATH。"
    }
}

function Install-Bun {
    if (Test-Command "bun") {
        return
    }

    Write-Info "未检测到 bun，开始自动安装..."
    powershell -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; irm https://bun.sh/install.ps1 | iex"
    Refresh-Path

    if (-not (Test-Command "bun")) {
        Fail "bun 安装完成后仍不可用，请检查 PATH。"
    }
}

function Get-CommandPath {
    param([string]$Name)

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    return $null
}

function Find-SystemBrowserPath {
    $candidates = @(
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Chromium\Application\chrome.exe"
    )

    foreach ($path in $candidates) {
        if (-not [string]::IsNullOrWhiteSpace($path) -and (Test-Path $path)) {
            return $path
        }
    }

    foreach ($commandName in @("chrome.exe", "chromium.exe")) {
        $resolvedPath = Get-CommandPath $commandName
        if (-not [string]::IsNullOrWhiteSpace($resolvedPath)) {
            return $resolvedPath
        }
    }

    return $null
}

function Get-ChromeForTestingDir {
    return (Join-Path $HOME ".flocks\browser")
}

function Install-ChromeForTesting {
    $browserDir = Get-ChromeForTestingDir

    if (-not (Test-Command "npx.cmd")) {
        Fail "未检测到 npx，请先安装 Node.js（包含 npm）后重试。"
    }

    New-Item -ItemType Directory -Path $browserDir -Force | Out-Null
    Write-Info "未检测到系统 Chrome/Chromium，开始安装 Chrome for Testing 到: $browserDir"

    $command = 'npx.cmd --yes @puppeteer/browsers install chrome@stable --path "{0}" 2>&1' -f $browserDir.Replace('"', '""')
    $output = @(& cmd.exe /d /c $command)
    $exitCode = $LASTEXITCODE
    foreach ($entry in $output) {
        Write-Host ($entry.ToString())
    }
    if ($exitCode -ne 0) {
        Fail "Chrome for Testing 安装失败。"
    }

    $browserPath = Resolve-ChromeForTestingPath -InstallOutput $output
    if ([string]::IsNullOrWhiteSpace($browserPath)) {
        Fail "Chrome for Testing 安装成功，但未能从安装输出解析浏览器路径。"
    }

    return $browserPath
}

function Resolve-ChromeForTestingPath {
    param([object[]]$InstallOutput)

    foreach ($entry in $InstallOutput) {
        $line = $entry.ToString().Trim()
        if ([string]::IsNullOrWhiteSpace($line)) {
            continue
        }

        if ($line -like "chrome@* *" -or $line -like "chromium@* *") {
            $firstSpaceIndex = $line.IndexOf(' ')
            if ($firstSpaceIndex -lt 0) {
                continue
            }

            $candidate = $line.Substring($firstSpaceIndex + 1).Trim()
            if (-not [string]::IsNullOrWhiteSpace($candidate) -and (Test-Path $candidate)) {
                return $candidate
            }
        }
    }

    return $null
}

function Configure-AgentBrowserBrowser {
    $browserPath = Find-SystemBrowserPath

    if ([string]::IsNullOrWhiteSpace($browserPath)) {
        $browserPath = Install-ChromeForTesting
        Write-Info "已安装 Chrome for Testing，agent-browser 将默认使用: $browserPath"
    }
    else {
        Write-Info "检测到系统 Chrome/Chromium，agent-browser 将默认使用: $browserPath"
    }

    $env:AGENT_BROWSER_EXECUTABLE_PATH = $browserPath
    [Environment]::SetEnvironmentVariable("AGENT_BROWSER_EXECUTABLE_PATH", $browserPath, "User")
}

function Install-AgentBrowser {
    if (-not (Test-Command "agent-browser")) {
        Write-Info "安装 agent-browser CLI..."
        & npm.cmd install --global agent-browser
        if ($LASTEXITCODE -ne 0) {
            Fail "agent-browser CLI 安装失败。"
        }
        Refresh-Path

        if (-not (Test-Command "agent-browser")) {
            Fail "agent-browser 安装完成后仍不可用，请检查 PATH。"
        }
    }
    else {
        Write-Info "检测到 agent-browser，跳过 CLI 安装。"
    }

    Configure-AgentBrowserBrowser
}

function Install-DingtalkChannelDeps {
    $connectorDir = Join-Path $RootDir ".flocks\plugins\channels\dingtalk\dingtalk-openclaw-connector"
    $packageJson  = Join-Path $connectorDir "package.json"

    if (-not (Test-Path $packageJson)) {
        return
    }

    $nodeModulesDir = Join-Path $connectorDir "node_modules"
    if (Test-Path $nodeModulesDir) {
        Write-Info "钉钉 channel 依赖已存在，跳过安装。"
        return
    }

    Write-Info "检测到钉钉 channel 插件，安装 npm 依赖..."

    Push-Location $connectorDir
    try {
        & npm.cmd install
        if ($LASTEXITCODE -ne 0) {
            Fail "钉钉 channel npm 依赖安装失败。"
        }
    }
    finally {
        Pop-Location
    }

    Write-Info "钉钉 channel 依赖安装完成。"
}

function Write-RunCommandHint {
    param([string]$Action)

    $runScriptPath = Join-Path $RootDir "scripts\run.ps1"
    Write-Host ('     & "{0}" {1}' -f $runScriptPath, $Action)
}

function Main {
    if ($Help) {
        Show-Usage
        return
    }

    Refresh-Path

    if (-not (Resolve-RootDir)) {
        Show-CloneHintAndExit
    }

    Write-Info "项目目录: $RootDir"
    Install-Uv
    Ensure-NpmInstalled

    Write-Info "使用 uv sync --group dev 安装 Python 后端依赖（含测试与 lint）..."
    Push-Location $RootDir
    try {
        & uv sync --group dev
        if ($LASTEXITCODE -ne 0) {
            Fail "Python 后端依赖安装失败。"
        }
    }
    finally {
        Pop-Location
    }

    Install-FlocksCli

    Write-Info "安装 WebUI 依赖..."
    Push-Location (Join-Path $RootDir "webui")
    try {
        & npm.cmd install
        if ($LASTEXITCODE -ne 0) {
            Fail "WebUI 依赖安装失败。"
        }
    }
    finally {
        Pop-Location
    }

    Install-DingtalkChannelDeps

    if ($InstallTui) {
        Install-Bun
        Write-Info "安装 TUI 依赖..."
        Push-Location (Join-Path $RootDir "tui")
        try {
            & bun install
            if ($LASTEXITCODE -ne 0) {
                Fail "TUI 依赖安装失败。"
            }
        }
        finally {
            Pop-Location
        }
    }
    else {
        Write-Info "跳过 TUI 依赖安装。如需安装，请重新执行 .\scripts\install.ps1 -InstallTui"
    }

    Install-AgentBrowser

    Write-Host ""
    Write-Host "[flocks] 安装完成。"
    Write-Host ""
    Write-Host "请启动新的终端会话，以加载环境变量并启用相关命令"
    Write-Host ""
    Write-Host "后续可用命令："
    Write-Host "  1. 以 daemon 模式启动后端 + WebUI"
    Write-Host "     flocks start"
    Write-Host ""
    Write-Host "  2. 查看更多命令帮助"
    Write-Host "     flocks --help"
    Write-Host ""
    if ($InstallTui) {
        Write-Host "  3. 启动 TUI"
        Write-Host "     flocks tui"
    }
}

Main
