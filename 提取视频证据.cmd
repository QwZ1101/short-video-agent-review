@echo off
setlocal
chcp 65001 >nul
python "%~dp0prepare_video.py"
set "RESULT=%ERRORLEVEL%"
if /I not "%~1"=="--no-pause" pause
exit /b %RESULT%
