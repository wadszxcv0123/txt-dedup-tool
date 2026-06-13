@echo off
chcp 65001 >nul

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting admin privileges...
    powershell -Command "Start-Process cmd.exe -ArgumentList '/c cd /d \"%%cd%%\" && 0-config-firewall.bat' -Verb RunAs"
    exit /b
)

echo ===============================================
echo   TXT-Dedup Tool - Firewall Configuration
echo ===============================================
echo.

echo [1/2] Adding firewall rule (Allow port 5566)...
netsh advfirewall firewall delete rule name="TXT-Dedup" >nul 2>&1
netsh advfirewall firewall add rule name="TXT-Dedup" dir=in action=allow protocol=TCP localport=5566

if %errorlevel% equ 0 (
    echo [SUCCESS] Firewall rule added
) else (
    echo [WARNING] Failed to add firewall rule, please configure manually
)

echo.
echo [2/2] Verifying rule...
netsh advfirewall firewall show rule name="TXT-Dedup"

echo.
echo ===============================================
echo   Firewall configuration complete!
echo ===============================================
echo.
echo   How to verify:
echo     Access from another computer: http://192.168.167.200:5566/api/health
echo.
echo   If using cloud server, please also open port 5566
echo   in your cloud platform's security group!
echo.

pause