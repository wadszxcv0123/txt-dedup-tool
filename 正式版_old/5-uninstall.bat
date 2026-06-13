@echo off
chcp 65001 >nul

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting admin privileges...
    powershell -Command "Start-Process cmd.exe -ArgumentList '/c cd /d \"%%cd%%\" && 5-uninstall.bat' -Verb RunAs"
    exit /b
)

echo ===============================================
echo   TXT-Dedup Tool - Uninstall
echo ===============================================
echo.

echo [1/3] Stopping server...
type nul > stop.flag
taskkill /f /im txt-dedup-server.exe >nul 2>&1

echo [2/3] Deleting startup task...
schtasks /delete /tn "TXT-Dedup-Server" /f >nul 2>&1

echo [3/3] Cleaning up service...
sc delete TXT-Dedup-Server >nul 2>&1

echo.
echo ===============================================
echo   Uninstall completed!
echo ===============================================
echo.
echo   To reinstall, run: 1-install.bat
echo.

pause