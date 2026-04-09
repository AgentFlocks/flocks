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
$script:UvDefaultIndex = if ([string]::IsNullOrWhiteSpace($env:FLOCKS_UV_DEFAULT_INDEX)) { "https://pypi.org/simple" } else { $env:FLOCKS_UV_DEFAULT_INDEX }
$script:NpmRegistry = if ([string]::IsNullOrWhiteSpace($env:FLOCKS_NPM_REGISTRY)) { "https://registry.npmjs.org/" } else { $env:FLOCKS_NPM_REGISTRY }

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

function Initialize-InstallSources {
    Write-Info "Using PyPI index: $script:UvDefaultIndex"
    Write-Info "Using npm registry: $script:NpmRegistry"
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
        Write-Info "Chocolatey installation failed, so Node.js cannot be installed automatically."
        return $false
    }

    Write-Info "A compatible npm installation was not found. Trying to install or upgrade Node.js 24.14.0 with Chocolatey..."
    & $chocoPath install nodejs --version="24.14.0" -y | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-Info "Chocolatey failed to install Node.js."
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
        Fail "A compatible npm installation was not found, or the current Node.js version is below $MinNodeMajor, and Chocolatey could not install Node.js automatically. Install Node.js $MinNodeMajor+ manually from https://nodejs.org/ (including npm), reopen PowerShell, and retry."
    }

    if (-not (Test-Command "npm.cmd")) {
        Fail "Node.js (including npm) finished installing, but npm is still not available. Check PATH and retry."
    }

    if (-not (Test-NodeVersionRequirement)) {
        Fail "Detected Node.js version is too old. This project requires Node.js $MinNodeMajor+."
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

    Write-Info "uv was not found. Installing it automatically..."
    powershell -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; irm https://astral.sh/uv/install.ps1 | iex"
    Refresh-Path

    if (-not (Test-Command "uv")) {
        Fail "uv finished installing, but it is still not available. Check PATH and retry."
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
            Set-Location $WorkingDirectory
            try {
                & $resolvedFilePath @resolvedArgs 2>&1 | ForEach-Object {
                    $lineText = "$_"
                    Write-Host $lineText
                    $outputLines.Add($lineText)
                }
            }
            finally {
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

    Push-Location $RootDir
    try {
        Invoke-InstallerCommandWithLockRetry `
            -Description "Global flocks CLI installation" `
            -FilePath "uv" `
            -ArgumentList @("tool", "install", "--editable", $RootDir, "--force", "--default-index", $script:UvDefaultIndex) `
            -WorkingDirectory $RootDir `
            -StreamOutput
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

function Install-ChromeForTesting {
    $browserDir = Get-ChromeForTestingDir

    if (-not (Test-Command "npx.cmd")) {
        Fail "npx was not found. Install Node.js (including npm) and retry."
    }

    New-Item -ItemType Directory -Path $browserDir -Force | Out-Null
    Write-Info "System Chrome/Chromium was not found. Installing Chrome for Testing to: $browserDir"

    $result = Invoke-NativeCommandOrFail `
        -Description "Chrome for Testing installation" `
        -FilePath "npx.cmd" `
        -ArgumentList @("--yes", "@puppeteer/browsers", "install", "chrome@stable", "--path", $browserDir) `
        -WorkingDirectory $browserDir `
        -Environment @{ npm_config_registry = $script:NpmRegistry } `
        -StreamOutput

    $browserPath = Resolve-ChromeForTestingPath -InstallOutputText (@($result.StdOut, $result.StdErr) -join [Environment]::NewLine)
    if ([string]::IsNullOrWhiteSpace($browserPath)) {
        Fail "Chrome for Testing finished installing, but the browser path could not be parsed from the installer output."
    }

    return $browserPath
}

function Resolve-ChromeForTestingPath {
    param([string]$InstallOutputText)

    foreach ($line in ($InstallOutputText -split "\r?\n")) {
        $line = $line.Trim()
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
        Write-Info "Installed Chrome for Testing. agent-browser will use: $browserPath"
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
        Write-Info "DingTalk channel dependencies already exist. Skipping installation."
        return
    }

    Write-Info "Detected DingTalk channel plugin. Installing npm dependencies..."

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

    Write-Info "DingTalk channel dependencies installed."
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

    Write-Info "Project directory: $RootDir"
    Install-Uv
    Ensure-NpmInstalled
    Initialize-InstallSources

    Write-Info "Installing Python backend dependencies (including tests and lint tools) with uv sync --group dev..."
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

    Write-Info "Installing WebUI dependencies..."
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
        Write-Info "Installing TUI dependencies..."
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
    else {
        Write-Info "Skipping TUI dependency installation. Re-run .\scripts\install.ps1 -InstallTui to install them."
    }

    Install-AgentBrowser

    Write-Host ""
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

Main