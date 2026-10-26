@echo off
setlocal
chcp 65001 >nul
where python >nul 2>&1
if errorlevel 1 (
  echo Python was not found. Install Python 3.9 or newer and add it to PATH.
  if /I not "%~1"=="--no-pause" pause
  exit /b 2
)
python "%~dp0check_materials.py"
set "RESULT=%ERRORLEVEL%"
if /I not "%~1"=="--no-pause" pause
exit /b %RESULT%
