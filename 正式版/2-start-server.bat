@echo off
cd /d "%~dp0"

if exist stop.flag del stop.flag

echo Starting TXT-Dedup Server with watchdog...

:watchdog_loop
powershell -Command "Start-Process -FilePath 'txt-dedup-server.exe' -WorkingDirectory '%cd%' -WindowStyle Hidden -Wait"

if exist stop.flag (
    del stop.flag
    echo Stop signal detected, watchdog exiting.
    exit /b
)

timeout /t 10 /nobreak >nul

echo [%date% %time%] Server exited unexpectedly, restarting...
goto watchdog_loop