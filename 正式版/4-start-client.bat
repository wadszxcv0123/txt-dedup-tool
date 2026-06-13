@echo off
chcp 65001 >nul
cd /d "%~dp0"

set CONFIG_FILE=client_config.txt
set SERVER=

if exist "%CONFIG_FILE%" (
    for /f "delims=" %%a in (%CONFIG_FILE%) do set SERVER=%%a
)

if "%SERVER%"=="" set SERVER=http://localhost:5566

:menu
cls
echo ===============================================
echo   TXT-Dedup Tool - Client v1.3.0
echo ===============================================
echo   Server: %SERVER%
echo ===============================================
echo.
echo   1. Deduplicate file
echo   2. Set server address
echo   3. Help
echo   4. Exit
echo.
set /p choice="Select option (1-4): "

if "%choice%"=="1" goto dedup
if "%choice%"=="2" goto set_server
if "%choice%"=="3" goto help
if "%choice%"=="4" exit
goto menu

:dedup
echo.
set /p INPUT_FILE="TXT File Path: "
if "%INPUT_FILE%"=="" goto menu
if not exist "%INPUT_FILE%" (
    echo File not found!
    pause
    goto menu
)
echo.
echo Starting deduplication, please wait...
.\txt-dedup-client.exe "%INPUT_FILE%" -s %SERVER%
echo.
pause
goto menu

:set_server
echo.
set /p SERVER="Server Address (e.g.: http://192.168.1.100:5566): "
echo %SERVER% > "%CONFIG_FILE%"
echo Saved.
pause
goto menu

:help
echo.
echo ===============================================
echo   Help
echo ===============================================
echo.
echo 1. Make sure server is running (2-start-server.bat)
echo 2. Enter full path of TXT file to deduplicate
echo 3. Wait for completion and view results
echo.
echo Server must be on the same computer or local network
echo Default port: 5566
echo.
pause
goto menu