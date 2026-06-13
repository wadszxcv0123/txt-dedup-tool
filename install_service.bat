@echo off
:: ===============================================
::  TXT Dedup Server - Install as Scheduled Task
::  Run as Administrator!
::  This creates a task that runs at system startup
::  and auto-restarts on failure.
:: ===============================================

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Please run as Administrator!
    echo Right-click this file ^> Run as Administrator
    pause
    exit /b 1
)

set "TASK_NAME=TXT-Dedup-Server"
set "EXE_PATH=%~dp0txt-dedup-server.exe"
set "CONFIG_PATH=%~dp0server_config.ini"
set "WORK_DIR=%~dp0"

echo ===============================================
echo   TXT Dedup Server - Installer
echo ===============================================
echo.
echo Task Name  : %TASK_NAME%
echo Binary     : %EXE_PATH%
echo Config     : %CONFIG_PATH%
echo Work Dir   : %WORK_DIR%
echo.

:: Remove existing task
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

:: Create scheduled task (runs at system startup, highest privileges)
schtasks /create /tn "%TASK_NAME%" ^
    /tr "\"%EXE_PATH%\" -c \"%CONFIG_PATH%\"" ^
    /sc onstart ^
    /ru SYSTEM ^
    /rl HIGHEST ^
    /delay 0000:30

if %errorlevel% equ 0 (
    :: Set restart on failure
    schtasks /change /tn "%TASK_NAME%" /restartonfailure:1 /restartinterval:1

    echo.
    echo ===============================================
    echo   Install SUCCESS!
    echo ===============================================
    echo.
    echo Start now   : schtasks /run /tn "%TASK_NAME%"
    echo Stop         : schtasks /end /tn "%TASK_NAME%"
    echo Status       : schtasks /query /tn "%TASK_NAME%" /v
    echo Uninstall    : run uninstall_service.bat
    echo.
    echo Starting task now...
    schtasks /run /tn "%TASK_NAME%"
    echo.
    echo Check status in 3 seconds...
    timeout /t 3 /nobreak >nul
    schtasks /query /tn "%TASK_NAME%" 2>&1 | findstr /i "%TASK_NAME%"
) else (
    echo.
    echo [ERROR] Install failed!
    echo Make sure you run as Administrator.
)

pause