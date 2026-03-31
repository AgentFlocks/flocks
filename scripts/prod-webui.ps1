$ErrorActionPreference = "Stop"

function Write-Info {
    param([string]$Message)
    Write-Host $Message -ForegroundColor Cyan
}

function Write-Success {
    param([string]$Message)
    Write-Host $Message -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host $Message -ForegroundColor Yellow
}

function Write-Fail {
    param([string]$Message)
    Write-Host $Message -ForegroundColor Red
}

function Get-EnvOrDefault {
    param(
        [string]$Name,
        [string]$DefaultValue
    )

    $value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $DefaultValue
    }

    return $value
}

function Get-AccessHost {
    param([string]$Host)

    if ($Host -in @("0.0.0.0", "::")) {
        return "127.0.0.1"
    }

    return $Host
}

function Stop-PortProcess {
    param([int]$Port)

    $connections = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
    if (-not $connections) {
        return
    }

    $processIds = $connections |
        Select-Object -ExpandProperty OwningProcess -Unique |
        Where-Object { $_ -and $_ -gt 0 }

    foreach ($processId in $processIds) {
        try {
            Stop-Process -Id $processId -Force -ErrorAction Stop
            Write-Warn "Stopped process on port $Port (PID: $processId)"
        } catch {
            Write-Warn ("Failed to stop PID {0} on port {1}: {2}" -f $processId, $Port, $_.Exception.Message)
        }
    }
}

function Test-HttpHealth {
    param(
        [string]$PythonExe,
        [string[]]$Urls
    )

    foreach ($url in $Urls) {
        try {
            $healthArgs = @(
                "-c",
                "import sys, urllib.request; url = sys.argv[1]; code = urllib.request.urlopen(url, timeout=2).getcode(); raise SystemExit(0 if 200 <= code < 300 else 1)",
                $url
            )

            $null = & $PythonExe @healthArgs 2>$null
            if ($LASTEXITCODE -eq 0) {
                return $true
            }
        } catch {
        }
    }

    return $false
}

Write-Info "Starting Flocks production environment..."

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
$webuiDir = Join-Path $projectRoot "webui"
$distDir = Join-Path $webuiDir "dist"
$logsDir = Join-Path $projectRoot "logs"
$backendStdout = Join-Path $logsDir "flocks-backend.out.log"
$backendStderr = Join-Path $logsDir "flocks-backend.err.log"
$frontendStdout = Join-Path $logsDir "webui-preview.out.log"
$frontendStderr = Join-Path $logsDir "webui-preview.err.log"
$backendPidFile = Join-Path $logsDir "backend.pid"
$frontendPidFile = Join-Path $logsDir "frontend.pid"
$backendHost = Get-EnvOrDefault -Name "BACKEND_HOST" -DefaultValue "127.0.0.1"
$backendAccessHost = Get-AccessHost -Host $backendHost
$backendPort = [int](Get-EnvOrDefault -Name "BACKEND_PORT" -DefaultValue "8000")
$frontendPort = [int](Get-EnvOrDefault -Name "FRONTEND_PORT" -DefaultValue "5173")
$backendBaseUrl = "http://{0}:{1}" -f $backendAccessHost, $backendPort
$backendWsUrl = "ws://{0}:{1}" -f $backendAccessHost, $backendPort

if (-not (Test-Path $pythonExe)) {
    Write-Fail "Python venv not found: $pythonExe"
    Write-Host "Run 'uv sync --group dev' or create the virtual environment first."
    exit 1
}

if (-not (Test-Path (Join-Path $webuiDir "package.json"))) {
    Write-Fail "WebUI directory is missing package.json: $webuiDir"
    exit 1
}

New-Item -ItemType Directory -Path $logsDir -Force | Out-Null

Write-Info "Cleaning existing processes..."
Stop-PortProcess -Port $backendPort
Stop-PortProcess -Port $frontendPort
Start-Sleep -Seconds 1

Write-Info "Building WebUI frontend..."
Push-Location $webuiDir
try {
    $originalApiBaseUrl = $env:VITE_API_BASE_URL
    $originalWsBaseUrl = $env:VITE_WS_BASE_URL
    $env:VITE_API_BASE_URL = $backendBaseUrl
    $env:VITE_WS_BASE_URL = $backendWsUrl
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
} finally {
    if ($null -eq $originalApiBaseUrl) {
        Remove-Item Env:VITE_API_BASE_URL -ErrorAction SilentlyContinue
    } else {
        $env:VITE_API_BASE_URL = $originalApiBaseUrl
    }
    if ($null -eq $originalWsBaseUrl) {
        Remove-Item Env:VITE_WS_BASE_URL -ErrorAction SilentlyContinue
    } else {
        $env:VITE_WS_BASE_URL = $originalWsBaseUrl
    }
    Pop-Location
}

if (-not (Test-Path $distDir)) {
    Write-Fail "Frontend build failed: webui/dist does not exist."
    exit 1
}

Write-Success "Frontend build completed."

Write-Info ("Starting backend service on port {0}..." -f $backendPort)
$backendArgs = @(
    "-m", "uvicorn",
    "flocks.server.app:app",
    "--host", $backendHost,
    "--port", $backendPort.ToString()
)

$backendProcess = Start-Process `
    -FilePath $pythonExe `
    -ArgumentList $backendArgs `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $backendStdout `
    -RedirectStandardError $backendStderr `
    -PassThru

Set-Content -Path $backendPidFile -Value $backendProcess.Id
Write-Warn "Backend PID: $($backendProcess.Id)"

$backendReady = $false
Write-Info "Waiting for backend startup..."
for ($attempt = 1; $attempt -le 15; $attempt++) {
    Start-Sleep -Seconds 2

    if (Test-HttpHealth -PythonExe $pythonExe -Urls @("$backendBaseUrl/api/health", "$backendBaseUrl/health")) {
        $backendReady = $true
        break
    }
}

if (-not $backendReady) {
    Write-Fail "Backend failed health check within 30 seconds."
    if (Test-Path $backendStdout) {
        Write-Host ""
        Write-Host "Backend stdout tail:"
        Get-Content -Path $backendStdout -Tail 20
    }
    if (Test-Path $backendStderr) {
        Write-Host ""
        Write-Host "Backend stderr tail:"
        Get-Content -Path $backendStderr -Tail 20
    }
    Stop-PortProcess -Port $backendPort
    exit 1
}

Write-Success "Backend started successfully."
Write-Warn "Backend stdout log: $backendStdout"
Write-Warn "Backend stderr log: $backendStderr"

Write-Info ("Starting WebUI preview on port {0}..." -f $frontendPort)
$previewCommand = ('set "VITE_API_BASE_URL={0}" && set "VITE_WS_BASE_URL={1}" && npm.cmd run preview -- --host 127.0.0.1 --port {2}' -f $backendBaseUrl, $backendWsUrl, $frontendPort)
$frontendArgs = @(
    "/c",
    $previewCommand
)

$frontendProcess = Start-Process `
    -FilePath "cmd.exe" `
    -ArgumentList $frontendArgs `
    -WorkingDirectory $webuiDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput $frontendStdout `
    -RedirectStandardError $frontendStderr `
    -PassThru

Set-Content -Path $frontendPidFile -Value $frontendProcess.Id
Write-Warn "Frontend PID: $($frontendProcess.Id)"

$frontendReady = $false
Write-Info "Waiting for frontend startup..."
for ($attempt = 1; $attempt -le 10; $attempt++) {
    Start-Sleep -Seconds 2

    if (Test-HttpHealth -PythonExe $pythonExe -Urls @("http://127.0.0.1:$frontendPort/")) {
        $frontendReady = $true
        break
    }
}

if (-not $frontendReady) {
    Write-Fail "Frontend failed health check within 20 seconds."
    if (Test-Path $frontendStdout) {
        Write-Host ""
        Write-Host "Frontend stdout tail:"
        Get-Content -Path $frontendStdout -Tail 20
    }
    if (Test-Path $frontendStderr) {
        Write-Host ""
        Write-Host "Frontend stderr tail:"
        Get-Content -Path $frontendStderr -Tail 20
    }
    Stop-PortProcess -Port $frontendPort
    Stop-PortProcess -Port $backendPort
    exit 1
}

Write-Success "Frontend started successfully."
Write-Warn "Frontend stdout log: $frontendStdout"
Write-Warn "Frontend stderr log: $frontendStderr"

Write-Success "Flocks production environment started."
Write-Warn ("Backend URL: {0}" -f $backendBaseUrl)
Write-Warn ("Frontend URL: http://127.0.0.1:{0}" -f $frontendPort)
Write-Warn ("Stop services: Stop-Process -Id {0},{1} -Force" -f $backendProcess.Id, $frontendProcess.Id)
