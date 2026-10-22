@echo off
cd /d "%~dp0"
echo ==========================================
echo   Bilibili Live Karaoke Bot Starting...
echo ==========================================
"%~dp0venv\Scripts\python.exe" bot.py
pause
