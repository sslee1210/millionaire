@echo off
setlocal
cd /d %~dp0

set MAX_REALTIME_CODES=220
set CANDIDATE_REFRESH_MS=90000
set CURRENT_QUOTE_POLL_MS=45000
set CURRENT_QUOTE_BATCH_LIMIT=25
set TR_DELAY_MS=900
set FLOW_WINDOWS_SEC=60,180
set FLOW_AMOUNT_THRESHOLD_MILLION=1000
set FLOW_EVENT_TTL_SEC=900

echo [millionaire] Preparing Kiwoom dashboard bridge dependencies...
cd bridge
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo [millionaire] Failed to install Python dependencies.
  pause
  exit /b 1
)

echo [millionaire] Starting dashboard bridge with sector flow board...
echo [millionaire] MAX_REALTIME_CODES=%MAX_REALTIME_CODES% / refresh=%CANDIDATE_REFRESH_MS%ms / TR batch=%CURRENT_QUOTE_BATCH_LIMIT%
python kiwoom_bridge_dashboard.py
pause
