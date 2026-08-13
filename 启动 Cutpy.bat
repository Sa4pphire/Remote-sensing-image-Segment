@echo off
cd /d "%~dp0"
python "%~dp0app.py"
if errorlevel 1 pause
