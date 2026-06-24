@echo off
setlocal
cd /d "%~dp0"
set PORT=5188
set KIWOOM_BRIDGE_URL=http://127.0.0.1:8765
echo [millionaire] Starting Kiwoom-only UI on http://localhost:5188
echo [millionaire] Do not use http://localhost:4173 if it shows the old dashboard.
call npm run server
pause
