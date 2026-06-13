@echo off
cd /d "%~dp0"

echo Stopping server...
type nul > stop.flag
taskkill /f /im txt-dedup-server.exe >nul 2>&1

if %errorlevel% equ 0 (
    echo Server stopped successfully
) else (
    echo Server was not running
)
pause