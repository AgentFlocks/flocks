# Build Tier-B staging directory: tools/ (uv, node, Chrome for Testing) + flocks/ (repository copy). No .venv — installer/bootstrap runs later.
# Run on Windows (PowerShell 5+). Requires: network access, Expand-Archive, robocopy (built-in).
#
# Usage:
#   .\packaging\windows\build-staging.ps1 -OutputDir C:\out\flocks-staging -RepoRoot $PWD

param(
    [Parameter(Mandatory = $true)]
    [string]$OutputDir,
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [string]$ManifestPath = (Join-Path $PSScriptRoot "versions.manifest.json"),
    [string]$CacheRoot = ""
)

$ErrorActionPreference = "Stop"

function Read-Manifest {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        throw "Manifest not found: $Path"
    }
    return Get-Content -Path $Path -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Ensure-EmptyDir {
    param([string]$Path)
    Remove-PathWithRetry -Path $Path
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
}

function Remove-PathWithRetry {
    param(
        [string]$Path,
        [int]$MaxAttempts = 5,
        [int]$DelaySeconds = 2
    )

    if (-not (Test-Path $Path)) {
        return
    }

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        try {
            Remove-Item -Path $Path -Recurse -Force -ErrorAction Stop
            return
        }
        catch {
            if ($attempt -eq $MaxAttempts) {
                throw
            }
            Write-Host "[build-staging] Failed to remove $Path (attempt $attempt/$MaxAttempts): $($_.Exception.Message)"
            Start-Sleep -Seconds $DelaySeconds
        }
    }
}

function Resolve-CacheRoot {
    param(
        [string]$RepoRoot,
        [string]$CacheRootOverride
    )

    if (-not [string]::IsNullOrWhiteSpace($CacheRootOverride)) {
        return $CacheRootOverride
    }
    if (-not [string]::IsNullOrWhiteSpace($env:FLOCKS_CACHE_ROOT)) {
        return $env:FLOCKS_CACHE_ROOT
    }

    $repoParent = Split-Path -Parent $RepoRoot
    if (-not [string]::IsNullOrWhiteSpace($repoParent)) {
        $workspaceCache = Join-Path $repoParent "flocks_deps"
        if (Test-Path $workspaceCache) {
            return $workspaceCache
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        return Join-Path $env:LOCALAPPDATA "flocks\cache"
    }
    if (-not [string]::IsNullOrWhiteSpace($env:XDG_CACHE_HOME)) {
        return Join-Path $env:XDG_CACHE_HOME "flocks"
    }
    return Join-Path $env:TEMP "flocks-cache"
}

function Get-OrDownloadFile {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$CachePath,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $cacheDir = Split-Path -Parent $CachePath
    if (-not (Test-Path $cacheDir)) {
        New-Item -ItemType Directory -Path $cacheDir -Force | Out-Null
    }

    if (Test-Path $CachePath) {
        $existing = Get-Item -Path $CachePath
        if ($existing.Length -gt 0) {
            Write-Host "[build-staging] Reusing cached ${Label}: $CachePath"
            return
        }
        Remove-PathWithRetry -Path $CachePath
    }

    Write-Host "[build-staging] Downloading $Label ..."
    $maxAttempts = 3
    $tmpPath = "$CachePath.download"
    for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
        if (Test-Path $tmpPath) {
            Remove-PathWithRetry -Path $tmpPath
        }
        try {
            Invoke-WebRequest -Uri $Url -OutFile $tmpPath -UseBasicParsing
            Move-Item -Path $tmpPath -Destination $CachePath -Force
            return
        }
        catch {
            if ($attempt -eq $maxAttempts) {
                throw
            }
            Write-Host "[build-staging] Download failed for $Label (attempt $attempt/$maxAttempts): $($_.Exception.Message)"
            Start-Sleep -Seconds 5
        }
    }
}

function Get-OrDownloadFileFromCandidates {
    param(
        [Parameter(Mandatory = $true)][string[]]$Urls,
        [Parameter(Mandatory = $true)][string]$CachePath,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if ($Urls.Count -eq 0) {
        throw "No download URL candidates provided for $Label"
    }

    $lastError = $null
    foreach ($url in $Urls) {
        try {
            Write-Host "[build-staging] Attempting $Label from: $url"
            Get-OrDownloadFile -Url $url -CachePath $CachePath -Label $Label
            return
        }
        catch {
            $lastError = $_
            Write-Host "[build-staging] Candidate failed for ${Label}: $($_.Exception.Message)"
        }
    }

    if ($lastError) {
        throw $lastError
    }
    throw "Failed to download $Label"
}

Write-Host "[build-staging] RepoRoot: $RepoRoot"
Write-Host "[build-staging] OutputDir: $OutputDir"

$manifest = Read-Manifest -Path $ManifestPath
$uvVersion = $manifest.uv.version
$nodeVersion = $manifest.nodejs.version
$nodeSuffix = $manifest.nodejs.windows_zip_suffix
$cacheRoot = Resolve-CacheRoot -RepoRoot $RepoRoot -CacheRootOverride $CacheRoot
Write-Host "[build-staging] CacheRoot: $cacheRoot"

Ensure-EmptyDir -Path $OutputDir

$toolsUv = Join-Path $OutputDir "tools\uv"
$toolsNode = Join-Path $OutputDir "tools\node"
$toolsChrome = Join-Path $OutputDir "tools\chrome"
$flocksDest = Join-Path $OutputDir "flocks"

New-Item -ItemType Directory -Path $toolsUv -Force | Out-Null
New-Item -ItemType Directory -Path $toolsNode -Force | Out-Null
New-Item -ItemType Directory -Path $toolsChrome -Force | Out-Null

# uv (standalone zip from GitHub releases)
$uvZipName = "uv-x86_64-pc-windows-msvc.zip"
$uvUrl = "https://github.com/astral-sh/uv/releases/download/$uvVersion/$uvZipName"
$uvZip = Join-Path $cacheRoot "downloads\uv-$uvVersion-$uvZipName"
Get-OrDownloadFile -Url $uvUrl -CachePath $uvZip -Label "uv $uvVersion"
Expand-Archive -Path $uvZip -DestinationPath $toolsUv -Force

# Node.js official zip (portable)
$nodeZipName = "node-v$nodeVersion-$nodeSuffix.zip"
$nodeUrl = "https://nodejs.org/dist/v$nodeVersion/$nodeZipName"
$nodeZip = Join-Path $cacheRoot "downloads\$nodeZipName"
Get-OrDownloadFile -Url $nodeUrl -CachePath $nodeZip -Label "Node $nodeVersion"
$nodeExtract = Join-Path $env:TEMP "node-extract-$nodeVersion"
Remove-PathWithRetry -Path $nodeExtract
New-Item -ItemType Directory -Path $nodeExtract -Force | Out-Null
Expand-Archive -Path $nodeZip -DestinationPath $nodeExtract -Force
$inner = Get-ChildItem -Path $nodeExtract -Directory | Select-Object -First 1
if (-not $inner) {
    throw "Unexpected Node zip layout"
}
Copy-Item -Path (Join-Path $inner.FullName "*") -Destination $toolsNode -Recurse -Force
Remove-PathWithRetry -Path $nodeExtract

# Chrome for Testing (bundled browser for agent-browser; prefer cached zip over npm-mediated install)
# Use the pinned version from the manifest when available (reproducible builds); fall back to LKGR.
Write-Host "[build-staging] Installing Chrome for Testing to tools\chrome (prefers cached direct download)..."
$pinnedCftVersion = $manifest.chrome_for_testing.version
$cftMirrorBase = $env:FLOCKS_CFT_MIRROR_BASE_URL
$cftUrls = @()
if (-not [string]::IsNullOrWhiteSpace($pinnedCftVersion)) {
    Write-Host "[build-staging] Using pinned Chrome for Testing version: $pinnedCftVersion"
    $cftVersion = $pinnedCftVersion
    if (-not [string]::IsNullOrWhiteSpace($cftMirrorBase)) {
        $mirrorBase = $cftMirrorBase.TrimEnd('/')
        $cftUrls += "$mirrorBase/$cftVersion/win64/chrome-win64.zip"
        Write-Host "[build-staging] Added mirror candidate from FLOCKS_CFT_MIRROR_BASE_URL"
    }
    $cftUrls += "https://storage.googleapis.com/chrome-for-testing-public/$cftVersion/win64/chrome-win64.zip"
}
else {
    Write-Host "[build-staging] No pinned Chrome version in manifest — resolving via LKGR..."
    $lkgrUrl = "https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json"
    $lkgr = Invoke-WebRequest -Uri $lkgrUrl -UseBasicParsing | Select-Object -ExpandProperty Content | ConvertFrom-Json
    $stable = $lkgr.channels.Stable
    if (-not $stable) {
        throw "Failed to resolve Stable channel from Chrome for Testing metadata"
    }
    $stableChrome = $stable.downloads.chrome | Where-Object { $_.platform -eq "win64" } | Select-Object -First 1
    if (-not $stableChrome) {
        throw "Failed to resolve win64 download URL from Chrome for Testing metadata"
    }
    $cftVersion = $stable.version
    if (-not [string]::IsNullOrWhiteSpace($cftMirrorBase)) {
        $mirrorBase = $cftMirrorBase.TrimEnd('/')
        $cftUrls += "$mirrorBase/$cftVersion/win64/chrome-win64.zip"
        Write-Host "[build-staging] Added mirror candidate from FLOCKS_CFT_MIRROR_BASE_URL"
    }
    $cftUrls += $stableChrome.url
}
# Canonical cache name; some mirrors or manual saves use "-stable-" in the filename — reuse if present.
$dlDir = Join-Path $cacheRoot "downloads"
$cftZipPrimary = Join-Path $dlDir ("chrome-for-testing-win64-" + $cftVersion + ".zip")
$cftZipAltStable = Join-Path $dlDir ("chrome-for-testing-win64-stable-" + $cftVersion + ".zip")
if (Test-Path -LiteralPath $cftZipPrimary -PathType Leaf) {
    $cftZip = $cftZipPrimary
}
elseif (Test-Path -LiteralPath $cftZipAltStable -PathType Leaf) {
    $cftZip = $cftZipAltStable
    Write-Host "[build-staging] Reusing cached Chrome zip (alternate filename): $cftZip"
}
else {
    $cftZip = $cftZipPrimary
}
Get-OrDownloadFileFromCandidates -Urls $cftUrls -CachePath $cftZip -Label ("Chrome for Testing " + $cftVersion)

$cftExtract = Join-Path $env:TEMP ("cft-extract-" + $cftVersion)
Remove-PathWithRetry -Path $cftExtract
New-Item -ItemType Directory -Path $cftExtract -Force | Out-Null
Expand-Archive -Path $cftZip -DestinationPath $cftExtract -Force
robocopy $cftExtract $toolsChrome /E /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
if ($LASTEXITCODE -ge 8) {
    throw "robocopy failed while copying Chrome for Testing with exit code $LASTEXITCODE"
}
$global:LASTEXITCODE = 0
Remove-PathWithRetry -Path $cftExtract

$chromeExe = Get-ChildItem -Path $toolsChrome -Recurse -Filter "chrome.exe" -File -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match 'chrome-win' } |
    Select-Object -First 1
if (-not $chromeExe) {
    $chromeExe = Get-ChildItem -Path $toolsChrome -Recurse -Filter "chrome.exe" -File -ErrorAction SilentlyContinue | Select-Object -First 1
}
if (-not $chromeExe) {
    throw "chrome.exe not found under tools\chrome after extracting bundled Chrome for Testing"
}
$rootResolved = (Resolve-Path $OutputDir).Path
$fullChrome = $chromeExe.FullName
if (-not $fullChrome.StartsWith($rootResolved, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Resolved chrome.exe path is not under OutputDir"
}
$relChrome = $fullChrome.Substring($rootResolved.Length).TrimStart('\')
$hintPath = Join-Path $toolsChrome "flocks-bundled-chrome.exe.relative.txt"
Set-Content -Path $hintPath -Value $relChrome -Encoding utf8
Write-Host "[build-staging] Recorded bundled Chrome path hint: $relChrome"

# Copy repo (exclude heavy / irrelevant dirs, but keep project-level .flocks plugins)
$exclude = @(".git", ".venv", "node_modules")
Write-Host "[build-staging] Copying repository..."
robocopy $RepoRoot $flocksDest /E /XD $exclude /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
if ($LASTEXITCODE -ge 8) {
    throw "robocopy failed with exit code $LASTEXITCODE"
}
# robocopy uses 0-7 as success states; normalize process exit code for callers.
$global:LASTEXITCODE = 0

# Note: the CLI wrapper ({HOME}\.local\bin\flocks.cmd) is created by
# scripts\install.ps1 during the post-install bootstrap. We deliberately do
# not pre-create an {app}\bin directory here, so the install layout stays in
# sync with the Inno shortcut targets.

Write-Host "[build-staging] Done. Next: compile installer with flocks-setup.iss, or use build-installer.ps1 for one-step packaging."
