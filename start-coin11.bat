@echo off
chcp 65001 >nul
title Coin11 Control - Start

echo ========================================
echo   Coin11 Control - Start All
echo ========================================
echo.

REM ------ Start Backend (FastAPI) ------
echo [1/2] Starting backend (port 8000)...
start "Coin11-Backend" cmd /c "cd /d D:\lenovo\Documents\Code\coin11-control-backend && set PYTHONPATH=.&& .venv\Scripts\python.exe app\main.py"
echo Backend started.

timeout /t 3 /nobreak >nul

REM ------ Start Frontend (Vite) ------
echo [2/2] Starting frontend (port 5173)...
start "Coin11-Frontend" cmd /c "cd /d D:\lenovo\Documents\Code\coin11-control-frontend && npm run dev"
echo Frontend started.

echo.
echo ================================================
echo  Backend API:   http://127.0.0.1:8000
echo  Backend Docs:  http://127.0.0.1:8000/docs
echo  Frontend:      http://127.0.0.1:5173
echo ================================================
echo.
set /p "=Press ENTER to stop all services..." <nul
pause >nul
echo.
echo Stopping services...

REM ʹ�� PowerShell ���˿�ɱ���� (��ɿ���ʽ)
powershell -Command "
  $ports = @(8000, 5173);
  $names = @{8000="Backend"; 5173="Frontend"};
  $found = $false;
  foreach ($port in $ports) {
    $conn = netstat -ano | Select-String ":$port ";
    if ($conn) {
      $pids = $conn | ForEach-Object { $_ -split "\s+" | Select-Object -Last 1 } | Select-Object -Unique;
      foreach ($pid in $pids) {
        taskkill /f /t /pid $pid 2>$null | Out-Null;
      }
      Write-Host "  $($names[$port]) stopped (port $port)";
      $found = $true;
    } else {
      Write-Host "  $($names[$port]) not running";
    }
  }
  if (-not $found) { Write-Host "  No services found on ports 8000, 5173"; }
"

REM 安全网: 清理残留进程（仅限从本窗口启动的）
echo Done.
