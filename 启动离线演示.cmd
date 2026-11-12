@echo off
cd /d "%~dp0"
python offline_demo.py
if errorlevel 1 pause
