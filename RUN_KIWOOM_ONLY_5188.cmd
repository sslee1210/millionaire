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

echo [millionaire] Starting Kiwoom-only UI on http://localhost:5188
echo [millionaire] Realtime coverage=%MAX_REALTIME_CODES% / flow alert threshold=10억 per 1m or 3m
call npm run server
pause
