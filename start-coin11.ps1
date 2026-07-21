$ErrorActionPreference = "Continue"

Write-Host "========================================"
Write-Host "  Coin11 Control - Start All"
Write-Host "========================================"
Write-Host ""

$pidDir = "$env:TEMP"
$bePidFile = "$pidDir\coin11-be.pid"
$fePidFile = "$pidDir\coin11-fe.pid"

# Clean old PID files
Remove-Item $bePidFile -ErrorAction SilentlyContinue
Remove-Item $fePidFile -ErrorAction SilentlyContinue

Write-Host "[1/2] Starting backend (port 8748)..."
$be = Start-Process -WindowStyle Normal -FilePath "cmd.exe" -ArgumentList @(
  "/c", "cd /d D:\lenovo\Documents\Code\coin11-control-backend && set PYTHONPATH=.&& .venv\Scripts\python.exe app\main.py"
) -PassThru
$be.Id | Out-File $bePidFile
Write-Host "  Backend started (PID: $($be.Id))"

Start-Sleep -Seconds 3

Write-Host "[2/2] Starting frontend (port 5173)..."
$fe = Start-Process -WindowStyle Normal -FilePath "cmd.exe" -ArgumentList @(
  "/c", "cd /d D:\lenovo\Documents\Code\coin11-control-frontend && npm run dev"
) -PassThru
$fe.Id | Out-File $fePidFile
Write-Host "  Frontend started (PID: $($fe.Id))"

Write-Host ""
Write-Host "================================================"
Write-Host "  Backend API:   http://127.0.0.1:8748"
Write-Host "  Backend Docs:  http://127.0.0.1:8748/docs"
Write-Host "  Frontend:      http://127.0.0.1:5173"
Write-Host "================================================"
Write-Host ""
Write-Host "Press any key to stop all services..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")

Write-Host ""
Write-Host "Stopping services..."

# Kill backend tree (taskkill /t kills child process tree)
if (Test-Path $bePidFile) {
  $pid = Get-Content $bePidFile
  taskkill /f /t /pid $pid 2>$null | Out-Null
  Write-Host "  Backend stopped"
  Remove-Item $bePidFile
} else {
  Write-Host "  [WARN] No backend PID file"
}

# Kill frontend tree
if (Test-Path $fePidFile) {
  $pid = Get-Content $fePidFile
  taskkill /f /t /pid $pid 2>$null | Out-Null
  Write-Host "  Frontend stopped"
  Remove-Item $fePidFile
} else {
  Write-Host "  [WARN] No frontend PID file"
}

Write-Host "Done."
