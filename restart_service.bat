@echo off
chcp 65001 >nul
echo ===============================================
echo   TXT查重工具 - 重启 Windows 服务
echo ===============================================

echo 正在停止服务 TXT-Dedup-Server...
net stop TXT-Dedup-Server
if %errorlevel% equ 0 (
    echo 服务已停止。
) else (
    echo 注意：停止服务可能失败，请确认服务是否已安装。
)

echo.
echo 正在启动服务 TXT-Dedup-Server...
net start TXT-Dedup-Server
if %errorlevel% equ 0 (
    echo 服务已启动。
) else (
    echo 启动失败，请检查服务安装状态和日志。
)
pause
