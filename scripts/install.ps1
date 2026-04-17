param(
    [switch]$InstallTui,
    [switch]$Help
)

$ErrorActionPreference = "Stop"

$RepoUrl = if ([string]::IsNullOrWhiteSpace($env:FLOCKS_INSTALL_REPO_URL)) { "https://github.com/AgentFlocks/Flocks.git" } else { $env:FLOCKS_INSTALL_REPO_URL }
$RawInstallShUrl = if ([string]::IsNullOrWhiteSpace($env:FLOCKS_RAW_INSTALL_SH_URL)) { "https://raw.githubusercontent.com/AgentFlocks/Flocks/main/install.sh" } else { $env:FLOCKS_RAW_INSTALL_SH_URL }
$RawInstallPs1Url = if ([string]::IsNullOrWhiteSpace($env:FLOCKS_RAW_INSTALL_PS1_URL)) { "https://raw.githubusercontent.com/AgentFlocks/Flocks/main/install.ps1" } else { $env:FLOCKS_RAW_INSTALL_PS1_URL }
$RootDir = $null
$MinNodeMajor = 22
$script:InstallLanguage = if ([string]::IsNullOrWhiteSpace($env:FLOCKS_INSTALL_LANGUAGE)) { "en" } else { $env:FLOCKS_INSTALL_LANGUAGE }
$script:UvDefaultIndex = if ([string]::IsNullOrWhiteSpace($env:FLOCKS_UV_DEFAULT_INDEX)) { "https://pypi.org/simple" } else { $env:FLOCKS_UV_DEFAULT_INDEX }
$script:UvInstallPs1Url = if ([string]::IsNullOrWhiteSpace($env:FLOCKS_UV_INSTALL_PS1_URL)) { "https://astral.sh/uv/install.ps1" } else { $env:FLOCKS_UV_INSTALL_PS1_URL }
$script:UvInstallPs1FallbackUrl = if ([string]::IsNullOrWhiteSpace($env:FLOCKS_UV_INSTALL_PS1_FALLBACK_URL)) { "https://uv.agentsmirror.com/install-cn.ps1" } else { $env:FLOCKS_UV_INSTALL_PS1_FALLBACK_URL }
$script:NpmRegistry = if ([string]::IsNullOrWhiteSpace($env:FLOCKS_NPM_REGISTRY)) { "https://registry.npmjs.org/" } else { $env:FLOCKS_NPM_REGISTRY }
$script:NodejsManualDownloadUrl = if ([string]::IsNullOrWhiteSpace($env:FLOCKS_NODEJS_MANUAL_DOWNLOAD_URL)) { "https://nodejs.org/en/download" } else { $env:FLOCKS_NODEJS_MANUAL_DOWNLOAD_URL }

function Write-Info {
    param([string]$Message)
    Write-Host "[flocks] $Message"
}

function Fail {
    param([string]$Message)
    if (Test-IsZhInstall) {
        Write-Host "[flocks] 错误: $Message" -ForegroundColor Red
    }
    else {
        Write-Host "[flocks] error: $Message" -ForegroundColor Red
    }
    exit 1
}

function Test-Command {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Test-IsZhInstall {
    return $script:InstallLanguage -like "zh*" -or $script:InstallLanguage -like "cn*"
}

function Get-LocalizedText {
    param(
        [string]$English,
        [string]$Chinese
    )

    if (Test-IsZhInstall) {
        return $Chinese
    }

    return $English
}

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
    if ($env:FLOCKS_SKIP_ADMIN_CHECK -eq "1") {
        return
    }

    if (Test-IsAdministrator) {
        return
    }

    Fail (Get-LocalizedText -English "Administrator privileges are required. Reopen PowerShell as Administrator and rerun this installer." -Chinese "安装需要管理员权限。请使用“以管理员身份运行”重新打开 PowerShell 后再执行。")
}

function Get-NodejsManualDownloadHint {
    return " Manual download: $script:NodejsManualDownloadUrl"
}

function Initialize-InstallSources {
    Write-Info (Get-LocalizedText -English "Using PyPI index: $script:UvDefaultIndex" -Chinese "使用 PyPI 源: $script:UvDefaultIndex")
    Write-Info (Get-LocalizedText -English "Using npm registry: $script:NpmRegistry" -Chinese "使用 npm 源: $script:NpmRegistry")
    Write-Info (Get-LocalizedText -English "Using uv install script: $script:UvInstallPs1Url" -Chinese "使用 uv 安装脚本: $script:UvInstallPs1Url")
    if (Test-IsZhInstall) {
        Write-Info (Get-LocalizedText -English "Using uv fallback script: $script:UvInstallPs1FallbackUrl" -Chinese "使用 uv 备用安装脚本: $script:UvInstallPs1FallbackUrl")
    }
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
    if (Test-IsZhInstall) {
        Write-Host "[flocks] 当前目录未找到 Flocks 仓库源码。"
        Write-Host ""
        Write-Host "如需从源码安装，请先克隆仓库后再执行："
        Write-Host ""
        Write-Host "  git clone $RepoUrl"
        Write-Host "  cd Flocks"
        Write-Host "  .\scripts\install_zh.ps1"
        Write-Host ""
        Write-Host "或者使用一键安装脚本："
        Write-Host ""
        Write-Host "  curl -fsSL $RawInstallShUrl | bash"
        Write-Host "  iwr -useb $RawInstallPs1Url | iex"
    }
    else {
        Write-Host "[flocks] Flocks repository source was not found in the current location."
        Write-Host ""
        Write-Host "To install from source, clone the repository first and then run:"
        Write-Host ""
        Write-Host "  git clone $RepoUrl"
        Write-Host "  cd Flocks"
        Write-Host "  .\scripts\install.ps1"
        Write-Host ""
        Write-Host "Or use the one-line GitHub bootstrap installer:"
        Write-Host ""
        Write-Host "  curl -fsSL $RawInstallShUrl | bash"
        Write-Host "  iwr -useb $RawInstallPs1Url | iex"
    }
    exit 1
}

function Refresh-Path {
    $userPath = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $machinePath = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $env:Path = "$userPath;$machinePath"

    $bundledUvBin = $null
    $bundledNodeBin = $null
    $installRoot = [Environment]::GetEnvironmentVariable("FLOCKS_INSTALL_ROOT")
    if (-not [string]::IsNullOrWhiteSpace($installRoot)) {
        $bundledUvCandidate = Join-Path $installRoot "tools\uv"
        if (Test-Path (Join-Path $bundledUvCandidate "uv.exe")) {
            $bundledUvBin = $bundledUvCandidate
        }

        $bundledNodeCandidate = Join-Path $installRoot "tools\node"
        if (Test-Path (Join-Path $bundledNodeCandidate "npm.cmd")) {
            $bundledNodeBin = $bundledNodeCandidate
        }
    }

    $uvBin = Join-Path $HOME ".local\bin"
    $cargoBin = Join-Path $HOME ".cargo\bin"
    $bunBin = Join-Path $HOME ".bun\bin"
    $windowsAppsBin = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps"

    foreach ($pathEntry in @($bundledUvBin, $bundledNodeBin, $uvBin, $cargoBin, $bunBin, $windowsAppsBin)) {
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
    Write-Host "  -InstallTui  Install TUI dependencies as well (bun will be installed automatically)"
    Write-Host "  -Help        Show this help message"
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

    Write-Info "Chocolatey was not found. Installing it automatically..."

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
        Write-Info "Chocolatey installation failed, so Node.js cannot be installed automatically.$(Get-NodejsManualDownloadHint)"
        return $false
    }

    Write-Info "A compatible npm installation was not found. Trying to install or upgrade Node.js 24.14.0 with Chocolatey..."
    & $chocoPath install nodejs --version="24.14.0" -y | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-Info "Chocolatey failed to install Node.js.$(Get-NodejsManualDownloadHint)"
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
        Write-Info "Detected current Node.js version v${currentMajor}. Trying to upgrade to Node.js $MinNodeMajor+..."
    }
    else {
        Write-Info "A compatible npm installation was not found. Trying to install Node.js automatically..."
    }

    if (-not (Install-NodeJsWithChocolatey)) {
        Fail "A compatible npm installation was not found, or the current Node.js version is below $MinNodeMajor, and Chocolatey could not install Node.js automatically. Install Node.js $MinNodeMajor+ manually (including npm), reopen PowerShell, and retry.$(Get-NodejsManualDownloadHint)"
    }

    if (-not (Test-Command "npm.cmd")) {
        Fail "Node.js (including npm) finished installing, but npm is still not available. Check PATH and retry.$(Get-NodejsManualDownloadHint)"
    }

    if (-not (Test-NodeVersionRequirement)) {
        Fail "Detected Node.js version is too old. This project requires Node.js $MinNodeMajor+.$(Get-NodejsManualDownloadHint)"
    }

    try {
        Write-Info "Node.js version: $((& node -v).Trim())"
        Write-Info "npm version: $((& npm.cmd -v).Trim())"
    }
    catch {
    }
}

function Install-Uv {
    if (Test-Command "uv") {
        return
    }

    Write-Info (Get-LocalizedText -English "uv was not found. Installing it automatically..." -Chinese "未检测到 uv，正在自动安装...")
    $primaryInstallError = $null
    try {
        powershell -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; irm '$script:UvInstallPs1Url' | iex"
    }
    catch {
        $primaryInstallError = $_.Exception.Message
    }
    Refresh-Path

    if (Test-Command "uv") {
        return
    }

    if (Test-IsZhInstall) {
        if ($null -ne $primaryInstallError) {
            Write-Info (Get-LocalizedText -English "Primary uv install script failed. Trying the mainland China fallback script..." -Chinese "默认 uv 安装脚本失败，正在尝试中国大陆备用源...")
            Write-Warning $primaryInstallError
        }
        else {
            Write-Info (Get-LocalizedText -English "uv is still unavailable after the primary install script. Trying the mainland China fallback script..." -Chinese "默认 uv 安装脚本执行后仍未检测到 uv，正在尝试中国大陆备用源...")
        }

        $fallbackInstallError = $null
        try {
            powershell -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; irm '$script:UvInstallPs1FallbackUrl' | iex"
        }
        catch {
            $fallbackInstallError = $_.Exception.Message
        }
        Refresh-Path

        if (Test-Command "uv") {
            return
        }

        if ($null -ne $fallbackInstallError) {
            Fail (Get-LocalizedText -English "uv installation failed with both the primary script and the mainland China fallback script. Check network access or PATH and retry." -Chinese "默认 uv 安装脚本和中国大陆备用源都执行失败。请检查网络连通性或 PATH 后重试。")
        }
    }

    if (-not (Test-Command "uv")) {
        Fail (Get-LocalizedText -English "uv finished installing, but it is still not available. Check PATH and retry." -Chinese "uv 安装已完成，但当前仍无法找到 uv。请检查 PATH 后重试。")
    }
}

function Get-RuntimePidFilePaths {
    $flocksRoot = [Environment]::GetEnvironmentVariable("FLOCKS_ROOT")
    if ([string]::IsNullOrWhiteSpace($flocksRoot)) {
        $flocksRoot = Join-Path $HOME ".flocks"
    }

    $runDir = Join-Path $flocksRoot "run"
    return @(
        (Join-Path $runDir "backend.pid")
        (Join-Path $runDir "webui.pid")
        (Join-Path $runDir "upgrade_server.pid")
    )
}

function Get-PidFromRuntimeFile {
    param([string]$PidFile)

    if ([string]::IsNullOrWhiteSpace($PidFile) -or -not (Test-Path $PidFile)) {
        return $null
    }

    try {
        $raw = (Get-Content -Path $PidFile -Raw -ErrorAction Stop).Trim()
    }
    catch {
        return $null
    }

    if ([string]::IsNullOrWhiteSpace($raw)) {
        return $null
    }

    if ($raw -match '^\d+$') {
        return [int]$raw
    }

    try {
        $json = $raw | ConvertFrom-Json -ErrorAction Stop
        if ($null -ne $json.pid -and "$($json.pid)" -match '^\d+$') {
            return [int]$json.pid
        }
    }
    catch {
    }

    return $null
}

function Stop-TrackedProcess {
    param(
        [int]$ProcessId,
        [string]$Reason = "tracked process"
    )

    if ($ProcessId -le 0) {
        return
    }

    try {
        Stop-Process -Id $ProcessId -Force -ErrorAction Stop
        Write-Info ("Stopped {0} (PID: {1})" -f $Reason, $ProcessId)
    }
    catch {
        try {
            & taskkill.exe /PID $ProcessId /T /F | Out-Null
            Write-Info ("Stopped {0} (PID: {1})" -f $Reason, $ProcessId)
        }
        catch {
            Write-Info ("Could not stop {0} (PID: {1}); continuing installation" -f $Reason, $ProcessId)
        }
    }
}

function Get-FlocksProcessIds {
    param([string]$ProjectRoot)

    $processIds = [System.Collections.Generic.List[int]]::new()
    $toolDir = ""
    if (Test-Command "uv") {
        try {
            $toolDir = (& uv tool dir 2>$null).Trim()
        }
        catch {
            $toolDir = ""
        }
    }

    $escapedToolDir = if ([string]::IsNullOrWhiteSpace($toolDir)) { "" } else { [Regex]::Escape($toolDir) }
    $escapedProjectRoot = if ([string]::IsNullOrWhiteSpace($ProjectRoot)) { "" } else { [Regex]::Escape($ProjectRoot) }

    try {
        $processes = Get-CimInstance Win32_Process -ErrorAction Stop
    }
    catch {
        return @()
    }

    foreach ($process in $processes) {
        if ([int]$process.ProcessId -eq $PID) {
            continue
        }
        $commandLine = $process.CommandLine
        if ([string]::IsNullOrWhiteSpace($commandLine)) {
            continue
        }

        $isMatch = [Regex]::IsMatch($commandLine, "flocks\.server\.app")
        if (-not $isMatch -and $escapedProjectRoot) {
            $escapedVenvDir = [Regex]::Escape((Join-Path $ProjectRoot ".venv"))
            $isMatch = [Regex]::IsMatch($commandLine, $escapedVenvDir) -and [Regex]::IsMatch($commandLine, "flocks")
        }
        if (-not $isMatch -and $escapedProjectRoot) {
            $isMatch = [Regex]::IsMatch($commandLine, $escapedProjectRoot) -and [Regex]::IsMatch($commandLine, "(uv tool|uv sync|npm(\.cmd)? run preview|vite preview)")
        }
        if (-not $isMatch -and $escapedToolDir) {
            $isMatch = [Regex]::IsMatch($commandLine, $escapedToolDir) -and [Regex]::IsMatch($commandLine, "flocks")
        }

        if ($isMatch) {
            $processIds.Add([int]$process.ProcessId)
        }
    }

    return $processIds | Select-Object -Unique
}

function Stop-FlocksProcesses {
    Write-Info "Checking for Flocks-related processes that may be locking the install directory..."

    $flocksCommand = Get-Command flocks -ErrorAction SilentlyContinue
    if ($flocksCommand) {
        try {
            & $flocksCommand.Source stop 2>$null | Out-Null
        }
        catch {
        }
        Start-Sleep -Seconds 2
    }

    foreach ($pidFile in Get-RuntimePidFilePaths) {
        $runtimePid = Get-PidFromRuntimeFile -PidFile $pidFile
        if ($null -ne $runtimePid) {
            Stop-TrackedProcess -ProcessId $runtimePid -Reason ("runtime process from {0}" -f $pidFile)
        }
    }

    foreach ($processId in Get-FlocksProcessIds -ProjectRoot $RootDir) {
        Stop-TrackedProcess -ProcessId $processId -Reason "Flocks related process"
    }

    Start-Sleep -Seconds 1
}

function Test-IsLockError {
    param([string]$Text)

    if ([string]::IsNullOrWhiteSpace($Text)) {
        return $false
    }

    return $Text -match '拒绝访问|Access is denied|os error 5|WinError 5|failed to remove directory|Failed to update Windows PE resources'
}

function Format-CmdArgument {
    param([AllowNull()][string]$Argument)

    if ($null -eq $Argument -or $Argument.Length -eq 0) {
        return '""'
    }

    if ($Argument -notmatch '[\s"&|<>^()]') {
        return $Argument
    }

    return '"' + $Argument.Replace('"', '""') + '"'
}

function Decode-ProcessOutputBytes {
    param([byte[]]$Bytes)

    if ($null -eq $Bytes -or $Bytes.Length -eq 0) {
        return ""
    }

    if ($Bytes.Length -ge 3 -and $Bytes[0] -eq 0xEF -and $Bytes[1] -eq 0xBB -and $Bytes[2] -eq 0xBF) {
        return [Text.Encoding]::UTF8.GetString($Bytes, 3, $Bytes.Length - 3)
    }

    if ($Bytes.Length -ge 2 -and $Bytes[0] -eq 0xFF -and $Bytes[1] -eq 0xFE) {
        return [Text.Encoding]::Unicode.GetString($Bytes, 2, $Bytes.Length - 2)
    }

    if ($Bytes.Length -ge 2 -and $Bytes[0] -eq 0xFE -and $Bytes[1] -eq 0xFF) {
        return [Text.Encoding]::BigEndianUnicode.GetString($Bytes, 2, $Bytes.Length - 2)
    }

    $encodings = [System.Collections.Generic.List[Text.Encoding]]::new()
    $seenCodePages = [System.Collections.Generic.HashSet[int]]::new()

    $strictUtf8 = [Text.UTF8Encoding]::new($false, $true)
    $encodings.Add($strictUtf8)
    $null = $seenCodePages.Add(65001)

    foreach ($encoding in @(
        [Console]::OutputEncoding,
        [Text.Encoding]::GetEncoding([Globalization.CultureInfo]::CurrentCulture.TextInfo.OEMCodePage),
        [Text.Encoding]::Default
    )) {
        if ($null -eq $encoding) {
            continue
        }

        if ($seenCodePages.Add($encoding.CodePage)) {
            $encodings.Add($encoding)
        }
    }

    foreach ($encoding in $encodings) {
        try {
            return $encoding.GetString($Bytes)
        }
        catch {
        }
    }

    return [Text.Encoding]::UTF8.GetString($Bytes)
}

function Read-ProcessOutputText {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path $Path)) {
        return ""
    }

    try {
        $bytes = [System.IO.File]::ReadAllBytes($Path)
    }
    catch {
        return ""
    }

    return Decode-ProcessOutputBytes -Bytes $bytes
}

function Write-ProcessOutputText {
    param([string]$Text)

    if ([string]::IsNullOrEmpty($Text)) {
        return
    }

    [Console]::Write($Text)
    if (-not $Text.EndsWith("`n") -and -not $Text.EndsWith("`r")) {
        [Console]::WriteLine()
    }
}

function Invoke-NativeCommand {
    param(
        [string]$FilePath,
        [string[]]$ArgumentList = @(),
        [string]$WorkingDirectory = (Get-Location).Path,
        [hashtable]$Environment = @{},
        [switch]$StreamOutput
    )

    $stdoutPath = $null
    $stderrPath = $null
    if (-not $StreamOutput) {
        $stdoutPath = [System.IO.Path]::GetTempFileName()
        $stderrPath = [System.IO.Path]::GetTempFileName()
    }
    $originalEnvironment = @{}

    try {
        $resolvedFilePath = $FilePath
        $resolvedArgs = @($ArgumentList)
        $commandExtension = [System.IO.Path]::GetExtension($FilePath)

        if ($commandExtension -in @(".cmd", ".bat")) {
            $cmdParts = [System.Collections.Generic.List[string]]::new()
            $cmdParts.Add((Format-CmdArgument -Argument $FilePath))
            foreach ($argument in $ArgumentList) {
                $cmdParts.Add((Format-CmdArgument -Argument $argument))
            }

            $resolvedFilePath = "cmd.exe"
            $resolvedArgs = @("/d", "/s", "/c", ($cmdParts -join " "))
        }

        foreach ($entry in $Environment.GetEnumerator()) {
            $name = [string]$entry.Key
            if ([string]::IsNullOrWhiteSpace($name)) {
                continue
            }

            $originalEnvironment[$name] = [System.Environment]::GetEnvironmentVariable($name, "Process")
            if ($null -eq $entry.Value) {
                Remove-Item -Path ("Env:{0}" -f $name) -ErrorAction SilentlyContinue
            }
            else {
                Set-Item -Path ("Env:{0}" -f $name) -Value ([string]$entry.Value)
            }
        }

        if ($StreamOutput) {
            $outputLines = [System.Collections.Generic.List[string]]::new()
            $savedLocation = Get-Location
            $savedErrorAction = $ErrorActionPreference
            Set-Location $WorkingDirectory
            try {
                $ErrorActionPreference = "Continue"
                & $resolvedFilePath @resolvedArgs 2>&1 | ForEach-Object {
                    $lineText = "$_"
                    Write-Host $lineText
                    $outputLines.Add($lineText)
                }
            }
            finally {
                $ErrorActionPreference = $savedErrorAction
                Set-Location $savedLocation
            }
            $streamExitCode = if ($null -eq $LASTEXITCODE) { 0 } else { $LASTEXITCODE }

            return [PSCustomObject]@{
                ExitCode = $streamExitCode
                StdOut   = ($outputLines -join [Environment]::NewLine)
                StdErr   = ""
            }
        }

        $process = Start-Process `
            -FilePath $resolvedFilePath `
            -ArgumentList $resolvedArgs `
            -WorkingDirectory $WorkingDirectory `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath `
            -Wait `
            -PassThru `
            -NoNewWindow

        return [PSCustomObject]@{
            ExitCode = [int]$process.ExitCode
            StdOut = Read-ProcessOutputText -Path $stdoutPath
            StdErr = Read-ProcessOutputText -Path $stderrPath
        }
    }
    finally {
        foreach ($entry in $originalEnvironment.GetEnumerator()) {
            $name = [string]$entry.Key
            if ([string]::IsNullOrWhiteSpace($name)) {
                continue
            }

            if ($null -eq $entry.Value) {
                Remove-Item -Path ("Env:{0}" -f $name) -ErrorAction SilentlyContinue
            }
            else {
                Set-Item -Path ("Env:{0}" -f $name) -Value ([string]$entry.Value)
            }
        }

        foreach ($path in @($stdoutPath, $stderrPath)) {
            if (-not [string]::IsNullOrWhiteSpace($path) -and (Test-Path $path)) {
                Remove-Item -Path $path -Force -ErrorAction SilentlyContinue
            }
        }
    }
}

function Invoke-NativeCommandOrFail {
    param(
        [string]$Description,
        [string]$FilePath,
        [string[]]$ArgumentList = @(),
        [string]$WorkingDirectory = (Get-Location).Path,
        [hashtable]$Environment = @{},
        [switch]$StreamOutput
    )

    $result = Invoke-NativeCommand -FilePath $FilePath -ArgumentList $ArgumentList -WorkingDirectory $WorkingDirectory -Environment $Environment -StreamOutput:$StreamOutput
    if (-not $StreamOutput) {
        Write-ProcessOutputText -Text $result.StdOut
        Write-ProcessOutputText -Text $result.StdErr
    }

    if ($result.ExitCode -ne 0) {
        Fail "$Description failed."
    }

    return $result
}

function Invoke-InstallerCommandWithLockRetry {
    param(
        [string]$Description,
        [string]$FilePath,
        [string[]]$ArgumentList = @(),
        [string]$WorkingDirectory = (Get-Location).Path,
        [hashtable]$Environment = @{},
        [switch]$StreamOutput
    )

    $result = Invoke-NativeCommand -FilePath $FilePath -ArgumentList $ArgumentList -WorkingDirectory $WorkingDirectory -Environment $Environment -StreamOutput:$StreamOutput
    if (-not $StreamOutput) {
        Write-ProcessOutputText -Text $result.StdOut
        Write-ProcessOutputText -Text $result.StdErr
    }

    if ($result.ExitCode -eq 0) {
        return
    }

    $combinedOutput = @($result.StdOut, $result.StdErr) -join [Environment]::NewLine
    if (-not (Test-IsLockError -Text $combinedOutput)) {
        Fail "$Description failed."
    }

    Write-Info "$Description detected a file lock. Cleaning up leftover processes before retrying..."
    Stop-FlocksProcesses
    Start-Sleep -Seconds 3

    $retryResult = Invoke-NativeCommand -FilePath $FilePath -ArgumentList $ArgumentList -WorkingDirectory $WorkingDirectory -Environment $Environment -StreamOutput:$StreamOutput
    if (-not $StreamOutput) {
        Write-ProcessOutputText -Text $retryResult.StdOut
        Write-ProcessOutputText -Text $retryResult.StdErr
    }

    if ($retryResult.ExitCode -ne 0) {
        Fail "$Description failed."
    }
}

function Install-FlocksCli {
    Write-Info "Installing the global flocks CLI..."

    $installRoot = [Environment]::GetEnvironmentVariable("FLOCKS_INSTALL_ROOT")
    if ([string]::IsNullOrWhiteSpace($installRoot)) {
        $linkDir = Join-Path $HOME ".local\bin"
    }
    else {
        $linkDir = Join-Path $installRoot "bin"
    }

    if (Test-Command "uv") {
        $savedEA = $ErrorActionPreference
        try {
            $ErrorActionPreference = "SilentlyContinue"
            $toolList = (& uv tool list 2>&1) | Where-Object { $_ -is [string] -or $_ -isnot [System.Management.Automation.ErrorRecord] }
            if ("$toolList" -match 'flocks') {
                Write-Info "Removing legacy uv tool installation..."
                & uv tool uninstall flocks 2>&1 | Out-Null
            }
        }
        catch {
            Write-Warning "Could not clean up legacy uv tool install. Continuing anyway."
        }
        finally {
            $ErrorActionPreference = $savedEA
        }
    }
    $staleExe = Join-Path $linkDir "flocks.exe"
    if (Test-Path $staleExe) {
        Write-Info "Removing stale flocks.exe to avoid shadowing new wrapper..."
        try {
            Remove-Item -Force $staleExe -ErrorAction Stop
        }
        catch {
            $backupExe = Join-Path $linkDir "flocks.exe.bak"
            try {
                Move-Item -Force $staleExe $backupExe -ErrorAction Stop
                Write-Info "Could not delete flocks.exe (locked); renamed to flocks.exe.bak"
            }
            catch {
                Write-Warning "Could not remove or rename flocks.exe — it may shadow the new flocks.cmd wrapper. Stop any running flocks process and re-run the installer."
            }
        }
    }

    $venvPython = Join-Path $RootDir ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        Fail "Expected .venv runtime not found: $venvPython - run 'uv sync' first."
    }

    if (-not (Test-Path $linkDir)) {
        New-Item -ItemType Directory -Path $linkDir -Force | Out-Null
    }

    $wrapperPath = Join-Path $linkDir "flocks.cmd"
    if ([string]::IsNullOrWhiteSpace($installRoot)) {
        $wrapperContent = "@echo off`r`n`"$venvPython`" -m flocks.cli.main %*"
    }
    else {
        $wrapperContent = "@echo off`r`nsetlocal`r`nif `"%FLOCKS_INSTALL_ROOT%`"==`"`" pushd `"%~dp0..`" >nul 2>&1`r`nif `"%FLOCKS_INSTALL_ROOT%`"==`"`" set `"FLOCKS_INSTALL_ROOT=%CD%`"`r`nif `"%FLOCKS_INSTALL_ROOT%`"==`"`" popd >nul 2>&1`r`nif `"%FLOCKS_REPO_ROOT%`"==`"`" set `"FLOCKS_REPO_ROOT=%FLOCKS_INSTALL_ROOT%\flocks`"`r`nif `"%FLOCKS_NODE_HOME%`"==`"`" set `"FLOCKS_NODE_HOME=%FLOCKS_INSTALL_ROOT%\tools\node`"`r`nset `"PATH=%FLOCKS_NODE_HOME%;%PATH%`"`r`npushd `"%FLOCKS_REPO_ROOT%`" >nul 2>&1`r`n`"%FLOCKS_INSTALL_ROOT%\flocks\.venv\Scripts\python.exe`" -m flocks.cli.main %*`r`nset `"FLOCKS_EXIT_CODE=%ERRORLEVEL%`"`r`npopd >nul 2>&1`r`nexit /b %FLOCKS_EXIT_CODE%"
    }
    [System.IO.File]::WriteAllText($wrapperPath, $wrapperContent, [System.Text.Encoding]::Default)

    Ensure-UserPathEntry $linkDir
    Refresh-Path

    if (-not (Test-Command "flocks")) {
        Fail "The flocks CLI finished installing, but it is still not available. Check PATH and retry."
    }
}

function Install-Bun {
    if (Test-Command "bun") {
        return
    }

    Write-Info "bun was not found. Installing it automatically..."
    powershell -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; irm https://bun.sh/install.ps1 | iex"
    Refresh-Path

    if (-not (Test-Command "bun")) {
        Fail "bun finished installing, but it is still not available. Check PATH and retry."
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

function Resolve-ChromeForTestingPath {
    param([string]$BrowserDir)

    if ([string]::IsNullOrWhiteSpace($BrowserDir) -or -not (Test-Path $BrowserDir)) {
        return $null
    }

    $candidateNames = @("chrome.exe", "Google Chrome for Testing", "chrome")
    $candidates = @(Get-ChildItem -Path $BrowserDir -Recurse -File -ErrorAction SilentlyContinue | Where-Object {
        $candidateNames -contains $_.Name
    })

    foreach ($name in $candidateNames) {
        $match = $candidates | Where-Object { $_.Name -eq $name } | Select-Object -First 1
        if ($match) {
            return $match.FullName
        }
    }

    return $null
}

function Resolve-BundledChromePath {
    $installRoot = [Environment]::GetEnvironmentVariable("FLOCKS_INSTALL_ROOT")
    if ([string]::IsNullOrWhiteSpace($installRoot)) {
        return $null
    }

    $installRoot = $installRoot.TrimEnd('\', '/')
    $bundledChromeDir = Join-Path $installRoot "tools\chrome"
    if (-not (Test-Path $bundledChromeDir)) {
        return $null
    }

    $hintFile = Join-Path $bundledChromeDir "flocks-bundled-chrome.exe.relative.txt"
    if (Test-Path $hintFile) {
        try {
            $relativeExePath = (Get-Content -Path $hintFile -Raw -ErrorAction Stop).Trim()
            if (-not [string]::IsNullOrWhiteSpace($relativeExePath)) {
                $candidatePath = Join-Path $installRoot $relativeExePath
                if (Test-Path $candidatePath) {
                    return (Resolve-Path $candidatePath).Path
                }
            }
        }
        catch {
        }
    }

    return (Resolve-ChromeForTestingPath -BrowserDir $bundledChromeDir)
}

function Install-ChromeForTesting {
    $browserDir = Get-ChromeForTestingDir

    if (-not (Test-Command "npx.cmd")) {
        Write-Warning (Get-LocalizedText -English "npx was not found, so browser installation was skipped. This does not block Flocks startup; you can reinstall it later." -Chinese "未找到 npx，跳过浏览器安装；这不影响 Flocks 启动，可稍后重新安装。")
        return $null
    }

    New-Item -ItemType Directory -Path $browserDir -Force | Out-Null
    Write-Info "System Chrome/Chromium was not found. Installing Chrome for Testing to: $browserDir"
    Write-Info (Get-LocalizedText -English "Downloading Chrome for Testing." -Chinese "正在下载 Chrome for Testing。")
    Write-Warning (Get-LocalizedText -English "If browser installation fails, Flocks can still start and you can reinstall it later." -Chinese "如浏览器安装失败，不影响 Flocks 启动，可稍后重新安装。")

    $npxPath = Get-CommandPath "npx.cmd"
    if ([string]::IsNullOrWhiteSpace($npxPath)) {
        Fail "npx was not found. Install Node.js (including npm) and retry.$(Get-NodejsManualDownloadHint)"
    }

    $previousRegistry = $env:npm_config_registry
    $env:npm_config_registry = $script:NpmRegistry
    try {
        $process = Start-Process `
            -FilePath $npxPath `
            -ArgumentList @("-y", "@puppeteer/browsers", "install", "chrome@stable", "--path", $browserDir) `
            -WorkingDirectory $browserDir `
            -NoNewWindow `
            -Wait `
            -PassThru
        if ($process.ExitCode -ne 0) {
            Write-Warning (Get-LocalizedText -English "Chrome for Testing installation failed. This does not block Flocks startup; you can reinstall it later." -Chinese "Chrome for Testing 安装失败，不影响 Flocks 启动，可稍后重新安装。")
            return $null
        }
    }
    finally {
        if ($null -eq $previousRegistry) {
            Remove-Item Env:npm_config_registry -ErrorAction SilentlyContinue
        }
        else {
            $env:npm_config_registry = $previousRegistry
        }
    }

    $browserPath = Resolve-ChromeForTestingPath -BrowserDir $browserDir
    if ([string]::IsNullOrWhiteSpace($browserPath)) {
        Write-Warning (Get-LocalizedText -English "Chrome for Testing finished installing, but the browser executable could not be located. This does not block Flocks startup; you can reinstall it later." -Chinese "Chrome for Testing 已安装，但未能在目录中找到浏览器可执行文件；这不影响 Flocks 启动，可稍后重新安装。")
        return $null
    }

    return $browserPath
}

function Configure-AgentBrowserBrowser {
    $browserPath = Find-SystemBrowserPath

    if ([string]::IsNullOrWhiteSpace($browserPath)) {
        $browserPath = Resolve-BundledChromePath
        if (-not [string]::IsNullOrWhiteSpace($browserPath)) {
            Write-Info "Found bundled Chrome for Testing. agent-browser will use: $browserPath"
        }
    }

    if ([string]::IsNullOrWhiteSpace($browserPath)) {
        $browserPath = Resolve-ChromeForTestingPath -BrowserDir (Get-ChromeForTestingDir)
        if ([string]::IsNullOrWhiteSpace($browserPath)) {
            $browserPath = Install-ChromeForTesting
            if (-not [string]::IsNullOrWhiteSpace($browserPath)) {
                Write-Info "Installed Chrome for Testing. agent-browser will use: $browserPath"
            }
            else {
                return
            }
        }
        else {
            Write-Info "Found existing Chrome for Testing. agent-browser will use: $browserPath"
        }
    }
    else {
        Write-Info "Detected system Chrome/Chromium. agent-browser will use: $browserPath"
    }

    $env:AGENT_BROWSER_EXECUTABLE_PATH = $browserPath
    [Environment]::SetEnvironmentVariable("AGENT_BROWSER_EXECUTABLE_PATH", $browserPath, "User")
}

function Install-AgentBrowser {
    if (-not (Test-Command "agent-browser")) {
        Write-Info "Installing the agent-browser CLI..."
        $null = Invoke-NativeCommandOrFail `
            -Description "agent-browser CLI installation" `
            -FilePath "npm.cmd" `
            -ArgumentList @("install", "--global", "agent-browser") `
            -Environment @{ npm_config_registry = $script:NpmRegistry } `
            -StreamOutput
        Refresh-Path

        if (-not (Test-Command "agent-browser")) {
            Fail "agent-browser finished installing, but it is still not available. Check PATH and retry."
        }
    }
    else {
        Write-Info "agent-browser is already installed. Skipping CLI installation."
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
        Write-Info (Get-LocalizedText -English "DingTalk channel dependencies already exist. Skipping installation." -Chinese "钉钉频道依赖已存在，跳过安装。")
        return
    }

    Write-Info (Get-LocalizedText -English "Detected DingTalk channel plugin. Installing npm dependencies..." -Chinese "检测到钉钉频道插件，正在安装 npm 依赖...")

    Push-Location $connectorDir
    try {
        $null = Invoke-NativeCommandOrFail `
            -Description "DingTalk channel npm dependency installation" `
            -FilePath "npm.cmd" `
            -ArgumentList @("install") `
            -WorkingDirectory $connectorDir `
            -Environment @{ npm_config_registry = $script:NpmRegistry } `
            -StreamOutput
    }
    finally {
        Pop-Location
    }

    Write-Info (Get-LocalizedText -English "DingTalk channel dependencies installed." -Chinese "钉钉频道依赖安装完成。")
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

    Assert-Administrator

    Refresh-Path

    if (-not (Resolve-RootDir)) {
        Show-CloneHintAndExit
    }

    Write-Info (Get-LocalizedText -English "Project directory: $RootDir" -Chinese "项目目录: $RootDir")
    Install-Uv
    Ensure-NpmInstalled
    Initialize-InstallSources

    Write-Info (Get-LocalizedText -English "Installing Python backend dependencies (including tests and lint tools) with uv sync --group dev..." -Chinese "正在使用 uv sync --group dev 安装 Python 后端依赖（含测试与 lint 工具）...")
    Push-Location $RootDir
    try {
        Invoke-InstallerCommandWithLockRetry `
            -Description "Python backend dependency installation" `
            -FilePath "uv" `
            -ArgumentList @("sync", "--group", "dev", "--default-index", $script:UvDefaultIndex) `
            -WorkingDirectory $RootDir `
            -StreamOutput
    }
    finally {
        Pop-Location
    }

    Install-FlocksCli

    Write-Info (Get-LocalizedText -English "Installing WebUI dependencies..." -Chinese "正在安装 WebUI 依赖...")
    Push-Location (Join-Path $RootDir "webui")
    try {
        $null = Invoke-NativeCommandOrFail `
            -Description "WebUI dependency installation" `
            -FilePath "npm.cmd" `
            -ArgumentList @("install") `
            -WorkingDirectory (Join-Path $RootDir "webui") `
            -Environment @{ npm_config_registry = $script:NpmRegistry } `
            -StreamOutput
    }
    finally {
        Pop-Location
    }

    Install-DingtalkChannelDeps

    if ($InstallTui) {
        Install-Bun
        Write-Info (Get-LocalizedText -English "Installing TUI dependencies..." -Chinese "正在安装 TUI 依赖...")
        Push-Location (Join-Path $RootDir "tui")
        try {
            $null = Invoke-NativeCommandOrFail `
                -Description "TUI dependency installation" `
                -FilePath "bun" `
                -ArgumentList @("install") `
                -WorkingDirectory (Join-Path $RootDir "tui") `
                -StreamOutput
        }
        finally {
            Pop-Location
        }
    }

    Install-AgentBrowser

    Write-Host ""
    if (Test-IsZhInstall) {
        Write-Host "[flocks] 安装完成。"
        Write-Host ""
        Write-Host "请打开新的终端会话，以加载更新后的环境变量并启用新安装的命令。"
        Write-Host ""
        Write-Host "接下来可以执行："
        Write-Host "  1. 以守护进程模式启动后端和 WebUI"
        Write-Host "     flocks start"
        Write-Host ""
        Write-Host "  2. 查看命令帮助"
        Write-Host "     flocks --help"
        Write-Host ""
        if ($InstallTui) {
            Write-Host "  3. 启动 TUI"
            Write-Host "     flocks tui"
        }
    }
    else {
        Write-Host "[flocks] Installation complete."
        Write-Host ""
        Write-Host "Start a new terminal session to load the updated environment and enable the installed commands."
        Write-Host ""
        Write-Host "Next commands:"
        Write-Host "  1. Start the backend and WebUI in daemon mode"
        Write-Host "     flocks start"
        Write-Host ""
        Write-Host "  2. Show command help"
        Write-Host "     flocks --help"
        Write-Host ""
        if ($InstallTui) {
            Write-Host "  3. Launch the TUI"
            Write-Host "     flocks tui"
        }
    }
}

Main