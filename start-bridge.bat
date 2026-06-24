@echo off
setlocal
cd /d %~dp0

echo [millionaire] Preparing Kiwoom OpenAPI+ bridge dependencies...
cd bridge

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if errorlevel 1 (
  echo [millionaire] Failed to install Python dependencies.
  pause
  exit /b 1
)

echo [millionaire] Starting Kiwoom OpenAPI+ bridge in Kiwoom-only sector mode...
python kiwoom_bridge_kiwoom_only.py
pause
