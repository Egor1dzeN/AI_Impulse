@echo off
cd /d "%~dp0"
call npm run dev > "..\frontend_run.log" 2>&1
