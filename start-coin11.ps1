$ErrorActionPreference = "Continue"

Write-Host "========================================"
Write-Host "  Coin11 Control - Start All"
Write-Host "========================================"
Write-Host ""

Write-Host "[1/2] Starting backend (port 8000)..."
Start-Process -WindowStyle Normal -FilePath "cmd.exe" -ArgumentList @(
  "/c", "cd /d D:\lenovo\Documents\Code\coin11-control-backend && set PYTHONPATH=.&& .venv\Scripts\python.exe app\main.py"
)
Write-Host "  Backend started"

Start-Sleep -Seconds 3

Write-Host "[2/2] Starting frontend (port 5173)..."
Start-Process -WindowStyle Normal -FilePath "cmd.exe" -ArgumentList @(
  "/c", "cd /d D:\lenovo\Documents\Code\coin11-control-frontend && npm run dev"
)
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

# ======== 按端口杀进程 ========
$ports = @(8000, 5173)
$names = @{8000 = "Backend"; 5173 = "Frontend"}

foreach ($port in $ports) {
  $name = $names[$port]
  Write-Host "  $name`: killing port $port..."
  try {
    $conn = netstat -ano | Select-String "TCP.*:$port .*LISTENING"
    if ($conn) {
      $pids = $conn | ForEach-Object { $_ -split '\s+' | Select-Object -Last 1 } | Get-Unique
      foreach ($pid in $pids) {
        taskkill /f /t /pid $pid 2>$null | Out-Null
      }
      Write-Host "    done"
    } else {
      Write-Host "    not running"
    }
  } catch {
    Write-Host "    error: $_"
  }
}

Write-Host "Done."
