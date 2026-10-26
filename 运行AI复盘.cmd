@echo off
setlocal
chcp 65001 >nul
set "REVIEW_MODE="
if /I "%~1"=="--dry-run" set "REVIEW_MODE=--dry-run"
python "%~dp0review_tutorial.py" %REVIEW_MODE%
set "RESULT=%ERRORLEVEL%"
if /I "%~1"=="--no-pause" exit /b %RESULT%
if /I "%~2"=="--no-pause" exit /b %RESULT%
pause
exit /b %RESULT%
