@echo off
setlocal
cd /d %~dp0
if not exist node_modules (
  echo [millionaire] npm install...
  npm install
)
echo [millionaire] Starting dashboard server...
npm run server
pause
