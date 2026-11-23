@echo off
REM EV Creator Studio - bam dup file nay de chay (Windows)
cd /d "%~dp0"
where py >nul 2>nul && (py -3 run.py %*) || (python run.py %*)
pause
