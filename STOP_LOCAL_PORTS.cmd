@echo off
setlocal enabledelayedexpansion
echo [millionaire] Checking ports 4173, 5188, 8765...
for %%P in (4173 5188 8765) do (
  for /f "tokens=5" %%A in ('netstat -ano ^| findstr LISTENING ^| findstr :%%P') do (
    echo [millionaire] Killing PID %%A on port %%P
    taskkill /F /PID %%A >nul 2>nul
  )
)
echo [millionaire] Done.
pause
