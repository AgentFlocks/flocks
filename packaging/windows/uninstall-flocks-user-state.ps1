# Called by Inno Setup [UninstallRun] before application files are removed.
# Cleans User PATH (any segment under {app}), global flocks.cmd wrapper, optional env vars, shortcuts, bundled Chrome junction.
# Does NOT delete %USERPROFILE%\.flocks (user data — logs, workspace, etc.).
# UTF-8 with BOM (Windows PowerShell 5.1)

param(
    [Parameter(Mandatory = $true)]
    [string]$InstallRoot
)

$ErrorActionPreference = "Stop"

function Write-UninstallLog {
    param([string]$Message)
    Write-Host "[flocks-uninstall] $Message"
}

function Remove-UserPathSegmentsUnderInstallRoot {
    param([string]$Root)

    # Removes every User PATH segment that is exactly the install root or any subdirectory
    # (e.g. ...\Flocks\bin, ...\Flocks\tools\uv, ...\Flocks\tools\node). Does not touch
    # %USERPROFILE%\.local\bin or other paths outside {app}.
    $Root = $Root.TrimEnd('\', '/')
    if ([string]::IsNullOrWhiteSpace($Root)) {
        return
    }

    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ([string]::IsNullOrWhiteSpace($userPath)) {
        return
    }

    $parts = $userPath -split ";" | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    $kept = New-Object System.Collections.Generic.List[string]
    foreach ($p in $parts) {
        $norm = $p.TrimEnd('\', '/')
        $underRoot = $false
        if ($norm.Equals($Root, [StringComparison]::OrdinalIgnoreCase)) {
            $underRoot = $true
        }
        elseif ($norm.Length -gt $Root.Length -and $norm.StartsWith($Root + '\', [StringComparison]::OrdinalIgnoreCase)) {
            $underRoot = $true
        }

        if (-not $underRoot) {
            [void]$kept.Add($p)
        }
    }

    $newPath = ($kept.ToArray()) -join ";"
    if ($userPath -eq $newPath) {
        return
    }

    if ([string]::IsNullOrEmpty($newPath)) {
        [Environment]::SetEnvironmentVariable("Path", $null, "User")
    }
    else {
        [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    }
    Write-UninstallLog 'Updated User PATH (removed all entries under the Flocks install directory).'
}

function Remove-UserEnvIfValue {
    param(
        [string]$Name,
        [string]$ExpectedValue
    )

    if ([string]::IsNullOrWhiteSpace($ExpectedValue)) {
        return
    }

    $cur = [Environment]::GetEnvironmentVariable($Name, "User")
    if ([string]::IsNullOrWhiteSpace($cur)) {
        return
    }

    if ($cur -eq $ExpectedValue) {
        [Environment]::SetEnvironmentVariable($Name, $null, "User")
        Write-UninstallLog "Removed User env: $Name"
    }
}

function Invoke-FlocksStop {
    param([string]$Root)

    $venvPy = Join-Path $Root "flocks\.venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPy) {
        Write-UninstallLog "Running flocks stop (via install directory venv)..."
        try {
            $prevEa = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            & $venvPy -m flocks.cli.main stop 2>&1 | ForEach-Object { Write-Host $_ }
            $ErrorActionPreference = $prevEa
            Write-UninstallLog "flocks stop finished (exit code: $LASTEXITCODE)."
        }
        catch {
            Write-UninstallLog "flocks stop raised: $($_.Exception.Message)"
        }
        Start-Sleep -Seconds 2
        return
    }

    $flocksCmd = Get-Command flocks -ErrorAction SilentlyContinue
    if ($flocksCmd) {
        Write-UninstallLog "Running flocks stop (via PATH: $($flocksCmd.Source))..."
        try {
            $prevEa = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            & $flocksCmd.Source stop 2>&1 | ForEach-Object { Write-Host $_ }
            $ErrorActionPreference = $prevEa
            Write-UninstallLog "flocks stop finished (exit code: $LASTEXITCODE)."
        }
        catch {
            Write-UninstallLog "flocks stop raised: $($_.Exception.Message)"
        }
        Start-Sleep -Seconds 2
    }
    else {
        Write-UninstallLog "Skipping flocks stop: no venv at $venvPy and no flocks on PATH."
    }
}

function Stop-FlocksFromRuntimePidFiles {
    $runDir = Join-Path $HOME ".flocks\run"
    foreach ($name in @("backend.pid", "webui.pid")) {
        $f = Join-Path $runDir $name
        if (-not (Test-Path -LiteralPath $f)) {
            continue
        }

        try {
            $text = Get-Content -LiteralPath $f -Raw -Encoding UTF8
            $m = [regex]::Match($text, '"pid"\s*:\s*(\d+)')
            if (-not $m.Success) {
                continue
            }

            $processId = [int]$m.Groups[1].Value
            if ($processId -le 0) {
                continue
            }

            $proc = Get-Process -Id $processId -ErrorAction SilentlyContinue
            if (-not $proc) {
                continue
            }

            Write-UninstallLog "Stopping PID $processId (from $name) and child processes..."
            & taskkill.exe /PID $processId /T /F | Out-Null
        }
        catch {
            Write-UninstallLog "Could not stop from ${name}: $($_.Exception.Message)"
        }
    }
}

function Stop-ProcessesUsingInstallRoot {
    param([string]$Root)

    $Root = $Root.TrimEnd('\', '/')
    if ([string]::IsNullOrWhiteSpace($Root)) {
        return
    }

    $escaped = [Regex]::Escape($Root)
    $parentPid = $null
    try {
        $self = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $PID) -ErrorAction SilentlyContinue
        if ($self) {
            $parentPid = [int]$self.ParentProcessId
        }
    }
    catch { }

    try {
        $procs = Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
            if ([int]$_.ProcessId -eq $PID) {
                return $false
            }
            if ($null -ne $parentPid -and [int]$_.ProcessId -eq $parentPid) {
                return $false
            }
            $name = $_.Name
            if (-not [string]::IsNullOrWhiteSpace($name) -and $name -match '^unins\d+\.exe$') {
                return $false
            }
            $cmd = $_.CommandLine
            if ([string]::IsNullOrWhiteSpace($cmd)) {
                return $false
            }
            if ($cmd -match '\\unins\d+\.exe(\s|")') {
                return $false
            }
            return [Regex]::IsMatch($cmd, $escaped, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
        }

        foreach ($p in $procs) {
            try {
                Write-UninstallLog "Force-stopping PID $($p.ProcessId) referencing install root..."
                & taskkill.exe /PID $p.ProcessId /T /F | Out-Null
            }
            catch {
                Write-UninstallLog "Could not force-stop PID $($p.ProcessId): $($_.Exception.Message)"
            }
        }
    }
    catch {
        Write-UninstallLog "Process sweep by install root failed: $($_.Exception.Message)"
    }
}

function Remove-LocalBinFlocksWrappers {
    param([string]$Root)

    $localBin = Join-Path $HOME ".local\bin"
    if (-not (Test-Path -LiteralPath $localBin)) {
        return
    }

    foreach ($fn in @("flocks.cmd", "flocks.exe", "flocks.exe.bak")) {
        $p = Join-Path $localBin $fn
        if (-not (Test-Path -LiteralPath $p)) {
            continue
        }

        try {
            $raw = Get-Content -LiteralPath $p -Raw -Encoding Default -ErrorAction Stop
            if ($raw -and $raw.IndexOf($Root, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
                Remove-Item -LiteralPath $p -Force -ErrorAction Stop
                Write-UninstallLog "Removed $p"
            }
        }
        catch {
            Write-UninstallLog "Could not remove ${fn}: $($_.Exception.Message)"
        }
    }
}

function Remove-FlocksShellShortcuts {
    # Matches [Icons] in flocks-setup.iss (user desktop + Start menu). Shell folder APIs follow OneDrive desktop redirects.
    $candidates = New-Object System.Collections.Generic.List[string]

    try {
        $desk = [Environment]::GetFolderPath('Desktop')
        if (-not [string]::IsNullOrWhiteSpace($desk)) {
            [void]$candidates.Add((Join-Path $desk 'Flocks.lnk'))
        }
    }
    catch { }

    try {
        $programs = [Environment]::GetFolderPath('Programs')
        if (-not [string]::IsNullOrWhiteSpace($programs)) {
            $flocksProg = Join-Path $programs 'Flocks'
            [void]$candidates.Add((Join-Path $flocksProg 'Start Flocks.lnk'))
            [void]$candidates.Add((Join-Path $flocksProg 'Flocks repository.lnk'))
        }
    }
    catch { }

    foreach ($p in $candidates) {
        if ([string]::IsNullOrWhiteSpace($p) -or -not (Test-Path -LiteralPath $p)) {
            continue
        }
        try {
            Remove-Item -LiteralPath $p -Force -ErrorAction Stop
            Write-UninstallLog "Removed shortcut: $p"
        }
        catch {
            Write-UninstallLog "Could not remove shortcut ${p}: $($_.Exception.Message)"
        }
    }

    try {
        $programs = [Environment]::GetFolderPath('Programs')
        if (-not [string]::IsNullOrWhiteSpace($programs)) {
            $flocksProg = Join-Path $programs 'Flocks'
            if (Test-Path -LiteralPath $flocksProg) {
                $left = @(Get-ChildItem -LiteralPath $flocksProg -Force -ErrorAction SilentlyContinue)
                if ($left.Count -eq 0) {
                    Remove-Item -LiteralPath $flocksProg -Force -ErrorAction SilentlyContinue
                    Write-UninstallLog "Removed empty Start menu folder: $flocksProg"
                }
            }
        }
    }
    catch { }
}

function Remove-BundledBrowserJunction {
    param([string]$Root)

    $bundled = Join-Path $HOME ".flocks\browser\bundled"
    if (-not (Test-Path -LiteralPath $bundled)) {
        return
    }

    try {
        $expectedChrome = (Join-Path $Root "tools\chrome").TrimEnd('\', '/')
        $out = cmd /c "fsutil reparsepoint query `"$bundled`" 2>nul" | Out-String
        if ($out -and $out.IndexOf($expectedChrome, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
            cmd /c "rmdir `"$bundled`"" | Out-Null
            Write-UninstallLog "Removed junction $bundled"
        }
    }
    catch {
        Write-UninstallLog "Bundled junction cleanup skipped: $($_.Exception.Message)"
    }
}

function Test-PathEqualsOrUnderRoot {
    param(
        [string]$PathValue,
        [string]$Root
    )

    if ([string]::IsNullOrWhiteSpace($PathValue) -or [string]::IsNullOrWhiteSpace($Root)) {
        return $false
    }

    $normPath = $PathValue.Trim().Trim('"').TrimEnd('\', '/')
    $normRoot = $Root.TrimEnd('\', '/')
    if ([string]::IsNullOrWhiteSpace($normPath) -or [string]::IsNullOrWhiteSpace($normRoot)) {
        return $false
    }

    if ($normPath.Equals($normRoot, [StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }

    if ($normPath.Length -gt $normRoot.Length -and $normPath.StartsWith($normRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }

    return $false
}

try {
    $InstallRoot = $InstallRoot.TrimEnd('\', '/')
    Write-UninstallLog "Cleaning user state for: $InstallRoot"

    Invoke-FlocksStop -Root $InstallRoot
    Stop-FlocksFromRuntimePidFiles
    Stop-ProcessesUsingInstallRoot -Root $InstallRoot

    Remove-UserPathSegmentsUnderInstallRoot -Root $InstallRoot

    $repoRoot = Join-Path $InstallRoot "flocks"
    $nodeHome = Join-Path $InstallRoot "tools\node"
    Remove-UserEnvIfValue -Name "FLOCKS_INSTALL_ROOT" -ExpectedValue $InstallRoot
    Remove-UserEnvIfValue -Name "FLOCKS_REPO_ROOT" -ExpectedValue $repoRoot
    Remove-UserEnvIfValue -Name "FLOCKS_NODE_HOME" -ExpectedValue $nodeHome

    $agent = [Environment]::GetEnvironmentVariable("AGENT_BROWSER_EXECUTABLE_PATH", "User")
    if (Test-PathEqualsOrUnderRoot -PathValue $agent -Root $InstallRoot) {
        [Environment]::SetEnvironmentVariable("AGENT_BROWSER_EXECUTABLE_PATH", $null, "User")
        Write-UninstallLog "Cleared AGENT_BROWSER_EXECUTABLE_PATH (pointed to current install root)."
    }

    Remove-LocalBinFlocksWrappers -Root $InstallRoot
    Remove-BundledBrowserJunction -Root $InstallRoot
    Remove-FlocksShellShortcuts
}
catch {
    Write-UninstallLog "ERROR: $($_.Exception.Message)"
}

# Never fail the Inno uninstaller (cleanup best-effort).
exit 0
