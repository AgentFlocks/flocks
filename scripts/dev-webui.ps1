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

function Test-BackendHealth {
    param(
        [string]$PythonExe,
        [string]$BackendBaseUrl
    )

    $healthUrls = @(
        "$BackendBaseUrl/api/health",
        "$BackendBaseUrl/health"
    )

    foreach ($url in $healthUrls) {
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

Write-Info "Starting Flocks development environment..."

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
$webuiDir = Join-Path $projectRoot "webui"
$logsDir = Join-Path $projectRoot "logs"
$backendStdout = Join-Path $logsDir "flocks-backend.out.log"
$backendStderr = Join-Path $logsDir "flocks-backend.err.log"
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
Start-Sleep -Seconds 2

Write-Info ("Starting backend service on port {0}..." -f $backendPort)
$backendArgs = @(
    "-m", "uvicorn",
    "flocks.server.app:app",
    "--host", $backendHost,
    "--port", $backendPort.ToString(),
    "--reload",
    "--reload-dir", "flocks",
    "--timeout-graceful-shutdown", "3"
)

$backendProcess = Start-Process `
    -FilePath $pythonExe `
    -ArgumentList $backendArgs `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $backendStdout `
    -RedirectStandardError $backendStderr `
    -PassThru

Write-Warn "Backend PID: $($backendProcess.Id)"

$backendReady = $false
Write-Info "Waiting for backend health check..."
for ($attempt = 1; $attempt -le 30; $attempt++) {
    Start-Sleep -Seconds 2

    if (Test-BackendHealth -PythonExe $pythonExe -BackendBaseUrl $backendBaseUrl) {
        $backendReady = $true
        break
    }
}

if (-not $backendReady) {
    Write-Fail "Backend failed health check within 60 seconds."
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

Write-Info ("Starting WebUI frontend on port {0}..." -f $frontendPort)

Push-Location $webuiDir
try {
    $originalApiBaseUrl = $env:VITE_API_BASE_URL
    $originalWsBaseUrl = $env:VITE_WS_BASE_URL
    $env:VITE_API_BASE_URL = $backendBaseUrl
    $env:VITE_WS_BASE_URL = $backendWsUrl
    & npm.cmd run dev -- --host 127.0.0.1 --port $frontendPort
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
    Write-Warn "Stopping backend service..."
    Stop-PortProcess -Port $backendPort
}
