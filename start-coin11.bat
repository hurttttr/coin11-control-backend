@echo off
chcp 65001 >nul
title Coin11 Control - Start

echo ========================================
echo   Coin11 Control - Start All
echo ========================================
echo.

REM ------ Start Backend (FastAPI) ------
echo [1/2] Starting backend (port 8748)...
start "Coin11-Backend" cmd /c "cd /d D:\lenovo\Documents\Code\coin11-control-backend && set PYTHONPATH=.&& .venv\Scripts\python.exe app\main.py"
echo Backend started.

timeout /t 3 /nobreak >nul

REM ------ Start Frontend (Vite) ------
echo [2/2] Starting frontend (port 5173)...
start "Coin11-Frontend" cmd /c "cd /d D:\lenovo\Documents\Code\coin11-control-frontend && npm run dev"
echo Frontend started.

echo.
echo ================================================
echo  Backend API:   http://127.0.0.1:8748
echo  Backend Docs:  http://127.0.0.1:8748/docs
echo  Frontend:      http://127.0.0.1:5173
echo ================================================
echo.
echo Press any key to stop all services...
pause >nul

echo.
echo Stopping services...

REM Step 1: Kill the cmd windows (which kills their child process trees)
taskkill /f /t /fi "WindowTitle eq Coin11-Backend" >nul 2>&1 && echo   Backend stopped
taskkill /f /t /fi "WindowTitle eq Coin11-Frontend" >nul 2>&1 && echo   Frontend stopped

REM Step 2: Also kill any orphan python/node processes that might have survived
taskkill /f /im python.exe /fi "WindowTitle eq Coin11-*" >nul 2>&1
taskkill /f /im node.exe /fi "WindowTitle eq Coin11-*" >nul 2>&1

echo Done.
