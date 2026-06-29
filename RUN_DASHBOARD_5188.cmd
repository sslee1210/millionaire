@echo off
setlocal
cd /d "%~dp0"
set PORT=5188
set KIWOOM_BRIDGE_URL=http://127.0.0.1:8765
set MAX_REALTIME_CODES=220
set CANDIDATE_REFRESH_MS=90000
set CURRENT_QUOTE_POLL_MS=45000
set CURRENT_QUOTE_BATCH_LIMIT=25
set FLOW_WINDOWS_SEC=60,180
set FLOW_AMOUNT_THRESHOLD_MILLION=1000
set SECTOR_LIMIT=16
set STOCKS_PER_SECTOR=12
set OVERVIEW_CACHE_MS=3000

echo [millionaire] Starting dashboard on http://localhost:5188
call npm run build
if errorlevel 1 exit /b 1
node server.js
pause
