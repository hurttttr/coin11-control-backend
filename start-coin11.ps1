$ErrorActionPreference = "Stop"

Write-Host "========================================"
Write-Host "  Coin11 Control - Start All"
Write-Host "========================================"
Write-Host ""

# Start Backend (FastAPI)
Write-Host "[1/2] Starting backend..."
$be = Start-Process -WindowStyle Normal -FilePath "cmd.exe" -ArgumentList "/c cd /d D:\lenovo\Documents\Code\coin11-control-backend && set PYTHONPATH=.&& .venv\Scripts\python.exe app\main.py" -PassThru
Start-Sleep -Seconds 3

# Start Frontend (Vite)
Write-Host "[2/2] Starting frontend..."
$fe = Start-Process -WindowStyle Normal -FilePath "cmd.exe" -ArgumentList "/c cd /d D:\lenovo\Documents\Code\coin11-control-frontend && npm run dev" -PassThru

Write-Host ""
Write-Host "================================================"
Write-Host "  Backend, Frontend started"
Write-Host "================================================"
Write-Host ""
Write-Host "Press any key to stop all services..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")

Write-Host "Stopping services..."
Stop-Process -Id $be.Id -Force -ErrorAction SilentlyContinue
Stop-Process -Id $fe.Id -Force -ErrorAction SilentlyContinue
Write-Host "Done."
