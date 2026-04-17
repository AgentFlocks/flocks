param(
    [string]$OutputDir = "",
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [string]$ManifestPath = (Join-Path $PSScriptRoot "versions.manifest.json"),
    [string]$InnoSetupCompilerPath = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    [string]$CacheRoot = "",
    [string]$AppVersion = ""
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path (Split-Path -Parent $RepoRoot) "agentflocks"
}

if (-not (Test-Path $InnoSetupCompilerPath)) {
    throw "Inno Setup compiler not found: $InnoSetupCompilerPath"
}

$buildStagingScript = Join-Path $PSScriptRoot "build-staging.ps1"
$installerScript = Join-Path $PSScriptRoot "flocks-setup.iss"

if (-not (Test-Path $buildStagingScript)) {
    throw "build-staging.ps1 not found: $buildStagingScript"
}
if (-not (Test-Path $installerScript)) {
    throw "Installer script not found: $installerScript"
}

Write-Host "[build-installer] Building staging directory..."
# When -CacheRoot is empty, do not pass it: nested `powershell -File ... -CacheRoot $empty`
# can drop the value and leave -CacheRoot without an argument (PS 5.1).
$stagingInvoke = @(
    '-NoProfile',
    '-ExecutionPolicy', 'Bypass',
    '-File', $buildStagingScript,
    '-OutputDir', $OutputDir,
    '-RepoRoot', $RepoRoot,
    '-ManifestPath', $ManifestPath
)
if (-not [string]::IsNullOrWhiteSpace($CacheRoot)) {
    $stagingInvoke += @('-CacheRoot', $CacheRoot)
}
& powershell.exe @stagingInvoke
if ($LASTEXITCODE -ne 0) {
    throw "Staging build failed with exit code $LASTEXITCODE"
}

Write-Host "[build-installer] Compiling Inno Setup installer..."
$isccArgs = @($installerScript, ("/DStagingRoot=" + $OutputDir))
if (-not [string]::IsNullOrWhiteSpace($AppVersion)) {
    $isccArgs += "/DAppVersion=$AppVersion"
}
& $InnoSetupCompilerPath @isccArgs
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup compilation failed with exit code $LASTEXITCODE"
}

Write-Host "[build-installer] Done."
