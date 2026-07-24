$ErrorActionPreference = "Continue"

Write-Host "========================================"
Write-Host "  Coin11 Control - Start All"
Write-Host "========================================"
Write-Host ""

Write-Host "[1/2] Starting backend (port 8000)..."
$be = Start-Process -WindowStyle Normal -FilePath "cmd.exe" -ArgumentList @(
  "/c", "cd /d D:\lenovo\Documents\Code\coin11-control-backend && set PYTHONPATH=.&& .venv\Scripts\python.exe app\main.py"
) -PassThru
Write-Host "  Backend started"

Start-Sleep -Seconds 3

Write-Host "[2/2] Starting frontend (port 5173)..."
$fe = Start-Process -WindowStyle Normal -FilePath "cmd.exe" -ArgumentList @(
  "/c", "cd /d D:\lenovo\Documents\Code\coin11-control-frontend && npm run dev"
) -PassThru
Write-Host "  Frontend started"

Write-Host ""
Write-Host "================================================"
Write-Host "  Backend API:   http://127.0.0.1:8000"
Write-Host "  Backend Docs:  http://127.0.0.1:8000/docs"
Write-Host "  Frontend:      http://127.0.0.1:5173"
Write-Host "================================================"
Write-Host ""
Write-Host "Press any key to stop all services..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")

Write-Host ""
Write-Host "Stopping services..."

# 按端口杀进程（最可靠方式）
$ports = @{8000 = "Backend"; 5173 = "Frontend"}
$found = $false

foreach ($port in $ports.Keys) {
  $conn = netstat -ano | Select-String ":$port "
  if ($conn) {
    $pids = $conn | ForEach-Object { $_ -split '\s+' | Select-Object -Last 1 } | Select-Object -Unique
    foreach ($pid in $pids) {
      taskkill /f /t /pid $pid 2>$null | Out-Null
    }
    Write-Host "  $($ports[$port]) stopped (port $port)"
    $found = $true
  } else {
    Write-Host "  $($ports[$port]) not running"
  }
}

if (-not $found) {
  Write-Host "  No services found on ports 8000, 5173"
}

Write-Host "Done."
