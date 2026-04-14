# Build Tier-B staging directory: tools/ (uv, node, Chrome for Testing) + flocks/ (repository copy). No .venv — user runs bootstrap later.
# Run on Windows (PowerShell 5+). Requires: network access, Expand-Archive, robocopy (built-in).
#
# Usage:
#   .\packaging\windows\build-staging.ps1 -OutputDir C:\out\flocks-staging -RepoRoot $PWD

param(
    [Parameter(Mandatory = $true)]
    [string]$OutputDir,
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [string]$ManifestPath = (Join-Path $PSScriptRoot "versions.manifest.json")
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
    if (Test-Path $Path) {
        Remove-Item -Path $Path -Recurse -Force
    }
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
}

Write-Host "[build-staging] RepoRoot: $RepoRoot"
Write-Host "[build-staging] OutputDir: $OutputDir"

$manifest = Read-Manifest -Path $ManifestPath
$uvVersion = $manifest.uv.version
$nodeVersion = $manifest.nodejs.version
$nodeSuffix = $manifest.nodejs.windows_zip_suffix

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
$uvZip = Join-Path $env:TEMP "uv-win-$uvVersion.zip"
Write-Host "[build-staging] Downloading uv $uvVersion ..."
Invoke-WebRequest -Uri $uvUrl -OutFile $uvZip -UseBasicParsing
Expand-Archive -Path $uvZip -DestinationPath $toolsUv -Force
Remove-Item $uvZip -Force

# Node.js official zip (portable)
$nodeZipName = "node-v$nodeVersion-$nodeSuffix.zip"
$nodeUrl = "https://nodejs.org/dist/v$nodeVersion/$nodeZipName"
$nodeZip = Join-Path $env:TEMP $nodeZipName
Write-Host "[build-staging] Downloading Node $nodeVersion ..."
Invoke-WebRequest -Uri $nodeUrl -OutFile $nodeZip -UseBasicParsing
$nodeExtract = Join-Path $env:TEMP "node-extract-$nodeVersion"
if (Test-Path $nodeExtract) {
    Remove-Item $nodeExtract -Recurse -Force
}
New-Item -ItemType Directory -Path $nodeExtract -Force | Out-Null
Expand-Archive -Path $nodeZip -DestinationPath $nodeExtract -Force
Remove-Item $nodeZip -Force
$inner = Get-ChildItem -Path $nodeExtract -Directory | Select-Object -First 1
if (-not $inner) {
    throw "Unexpected Node zip layout"
}
Copy-Item -Path (Join-Path $inner.FullName "*") -Destination $toolsNode -Recurse -Force
Remove-Item $nodeExtract -Recurse -Force

# Chrome for Testing (bundled browser for agent-browser; avoids relying on end-user npx at first install)
$npxCmd = Join-Path $toolsNode "npx.cmd"
if (-not (Test-Path $npxCmd)) {
    throw "npx.cmd not found next to bundled Node: $npxCmd"
}
Write-Host "[build-staging] Installing Chrome for Testing to tools\chrome (uses npm registry for @puppeteer/browsers)..."
$prevPath = $env:Path
$env:Path = "$toolsNode;$prevPath"
$puppeteerEnv = @{}
if (-not [string]::IsNullOrWhiteSpace($env:PUPPETEER_CHROME_DOWNLOAD_BASE_URL)) {
    $puppeteerEnv["PUPPETEER_CHROME_DOWNLOAD_BASE_URL"] = $env:PUPPETEER_CHROME_DOWNLOAD_BASE_URL
}
try {
    $cfTResult = & $npxCmd @("--yes", "@puppeteer/browsers", "install", "chrome@stable", "--path", $toolsChrome) 2>&1
    $cfTResult | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) {
        throw "Chrome for Testing install exited with code $LASTEXITCODE"
    }
}
finally {
    $env:Path = $prevPath
}

$chromeExe = Get-ChildItem -Path $toolsChrome -Recurse -Filter "chrome.exe" -File -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match 'chrome-win' } |
    Select-Object -First 1
if (-not $chromeExe) {
    $chromeExe = Get-ChildItem -Path $toolsChrome -Recurse -Filter "chrome.exe" -File -ErrorAction SilentlyContinue | Select-Object -First 1
}
if (-not $chromeExe) {
    throw "chrome.exe not found under tools\chrome after @puppeteer/browsers install"
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

# Copy repo (exclude heavy / irrelevant dirs)
$exclude = @(".git", ".venv", "node_modules", ".flocks")
Write-Host "[build-staging] Copying repository..."
robocopy $RepoRoot $flocksDest /E /XD $exclude /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
if ($LASTEXITCODE -ge 8) {
    throw "robocopy failed with exit code $LASTEXITCODE"
}

$binDir = Join-Path $OutputDir "bin"
New-Item -ItemType Directory -Path $binDir -Force | Out-Null
$shim = Join-Path $PSScriptRoot "shim\flocks-start.cmd"
Copy-Item -Path $shim -Destination (Join-Path $binDir "flocks-start.cmd") -Force

Write-Host "[build-staging] Done. Next: run Inno Setup (flocks-setup.iss) or zip this folder."
Write-Host "[build-staging] On first launch use bin\flocks-start.cmd (runs bootstrap if .venv is missing)."
