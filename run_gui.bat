@echo off
cd /d "%~dp0"
python -m zvcard.gui
if errorlevel 1 pause
