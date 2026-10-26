@echo off
setlocal
chcp 65001 >nul
set "WORKFLOW_MODE=live"
if /I "%~1"=="--replay" set "WORKFLOW_MODE=replay"
python "%~dp0run_workflow.py" --mode %WORKFLOW_MODE%
set "RESULT=%ERRORLEVEL%"
if /I "%~1"=="--no-pause" exit /b %RESULT%
if /I "%~2"=="--no-pause" exit /b %RESULT%
pause
exit /b %RESULT%
