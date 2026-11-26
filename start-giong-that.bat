@echo off
title EV Creator Studio - giong doc that
REM EV Creator Studio - cai VieNeu-TTS (giong doc that) roi chay. Bam dup file nay.
REM Lan dau mat vai phut; lan doc dau tien can Internet de tai model (vai tram MB).
cd /d "%~dp0"
where py >nul 2>nul && (py -3 run.py --with-tts %*) || (python run.py --with-tts %*)
pause
