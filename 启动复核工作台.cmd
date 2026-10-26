@echo off
cd /d "%~dp0"
python "%~dp0launch_review.py" %*
if errorlevel 1 pause
