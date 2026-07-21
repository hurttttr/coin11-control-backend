@echo off
chcp 65001 >nul
title Coin11 Control - Start
echo ========================================
echo   Coin11 Control - Start All
echo ========================================
echo.

REM ------ Start Backend (FastAPI) ------
echo [1/2] Starting backend...
start "Coin11-Backend" cmd /c "cd /d D:\lenovo\Documents\Code\coin11-control-backend && set PYTHONPATH=. && .venv\Scripts\python.exe app\main.py"

timeout /t 3 /nobreak >nul

REM ------ Start Frontend (Vite) ------
echo [2/2] Starting frontend...
start "Coin11-Frontend" cmd /c "cd /d D:\lenovo\Documents\Code\coin11-control-frontend && npm run dev"

echo.
echo ================================================
echo  Backend API:   http://127.0.0.1:xxxx  (see .env)
echo  Backend Docs:  http://127.0.0.1:xxxx/docs
echo  Frontend:      http://127.0.0.1:5173
echo ================================================
echo.
echo Press any key to stop all services...
pause >nul

echo Stopping services...
taskkill /f /fi "WindowTitle eq Coin11-Backend" >nul 2>&1
taskkill /f /fi "WindowTitle eq Coin11-Frontend" >nul 2>&1
echo Done.
