@echo off
chcp 65001 >nul

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting admin privileges...
    powershell -Command "Start-Process cmd.exe -ArgumentList '/c cd /d \"%%cd%%\" && 1-install.bat' -Verb RunAs"
    exit /b
)

set SCRIPT_DIR=%~dp0
set EXE_PATH=%SCRIPT_DIR%txt-dedup-server.exe
set TASK_NAME=TXT-Dedup-Server

if not exist "%EXE_PATH%" (
    echo [ERROR] txt-dedup-server.exe not found
    echo Please ensure this bat file is in the same directory as the program
    pause
    exit /b 1
)

echo ===============================================
echo   TXT-Dedup Tool - One-click Install
echo   Author: Zhang Wenlong ^| Tel: 18053292127
echo ===============================================
echo.

echo [1/4] Cleaning up old service...
sc delete TXT-Dedup-Server >nul 2>&1
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

echo [2/4] Releasing port 5566...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":5566" ^| findstr LISTENING 2^>nul') do (
    taskkill /f /pid %%p >nul 2>&1
)

echo [3/4] Creating startup task...
schtasks /create /tn "%TASK_NAME%" /tr "\"%SCRIPT_DIR%2-start-server.bat\"" /sc onstart /ru SYSTEM /rl HIGHEST /f

if %errorlevel% equ 0 (
    echo [4/4] Starting server...
    start "" /b "%SCRIPT_DIR%2-start-server.bat"
    timeout /t 3 /nobreak >nul
    
    echo.
    echo ===============================================
    echo   Installation successful!
    echo ===============================================
    echo.
    echo   Features configured:
    echo     - Auto-start on boot (system-level, runs before login)
    echo     - Auto-restart on crash (watchdog protection)
    echo     - Server started
    echo.
    echo   Operations:
    echo     2-start-server.bat   Start server manually
    echo     3-stop-server.bat    Stop server
    echo     4-start-client.bat   Start client
    echo     5-uninstall.bat      Uninstall completely
    echo.
    echo   Web Dashboard: http://localhost:5566
    echo   LAN Access: http://192.168.167.58:5566
) else (
    echo [ERROR] Failed to create startup task
)

pause