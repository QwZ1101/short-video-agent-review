@echo off
setlocal
chcp 65001 >nul
set "VERIFY_MODE="
if /I "%~1"=="--dry-run" set "VERIFY_MODE=--dry-run"
python "%~dp0verify_tutorial.py" %VERIFY_MODE%
set "RESULT=%ERRORLEVEL%"
if /I "%~1"=="--no-pause" exit /b %RESULT%
if /I "%~2"=="--no-pause" exit /b %RESULT%
pause
exit /b %RESULT%
