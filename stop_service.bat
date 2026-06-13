@echo off
chcp 65001 >nul
echo ===============================================
echo   TXT查重工具 - 停止 Windows 服务
echo ===============================================

echo 正在停止服务 TXT-Dedup-Server...
net stop TXT-Dedup-Server
if %errorlevel% equ 0 (
    echo.
    echo 服务已停止。
) else (
    echo.
    echo 停止失败，请检查服务是否已安装并正在运行。
)
pause
