@echo off
chcp 65001 >nul
title Coin11 Control - Start

echo ========================================
echo   Coin11 Control - Start All
echo ========================================
echo.

REM ------ Start Backend ------
echo [1/2] Starting backend (port 8000)...
start "Coin11-Backend" cmd /c "cd /d D:\lenovo\Documents\Code\coin11-control-backend && set PYTHONPATH=.&& .venv\Scripts\python.exe app\main.py"
echo Backend started.

timeout /t 3 /nobreak >nul

REM ------ Start Frontend ------
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

REM ==== 通过端口查找PID并杀死 ====
echo   Backend: killing process on port 8000...
for /f "skip=4 tokens=5" %%a in ('netstat -ano ^| findstr ":8000 "') do (
  if not "%%a"=="0" taskkill /f /t /pid %%a >nul 2>&1
)

echo   Frontend: killing process on port 5173...
for /f "skip=4 tokens=5" %%a in ('netstat -ano ^| findstr ":5173 "') do (
  if not "%%a"=="0" taskkill /f /t /pid %%a >nul 2>&1
)

REM 额外清理: 杀本窗口启动的孤儿进程
taskkill /f /fi "WINDOWTITLE eq Coin11-Backend" >nul 2>&1
taskkill /f /fi "WINDOWTITLE eq Coin11-Frontend" >nul 2>&1

echo Done.
