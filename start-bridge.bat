@echo off
setlocal
cd /d %~dp0
echo [millionaire] Starting Kiwoom OpenAPI+ bridge...
cd bridge
python kiwoom_bridge.py
pause
