@echo off
cd /d "%~dp0"
if not "%~1"=="" goto args
python "%~dp0create_sample.py" --interactive
pause
exit /b
:args
python "%~dp0create_sample.py" %*
