@echo off
cd /d "%~dp0"
if not exist node_modules call npm install
if not exist server\node_modules call npm run server:install
call npm start
pause
