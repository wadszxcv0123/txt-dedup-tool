@echo off
chcp 65001 >nul
echo ===============================================
echo   TXT查重工具 - 启动 Windows 服务
echo ===============================================

echo 正在启动服务 TXT-Dedup-Server...
net start TXT-Dedup-Server
if %errorlevel% equ 0 (
    echo.
    echo 服务已启动。
) else (
    echo.
    echo 启动失败，请检查服务是否已安装，或使用 install_service.bat 安装服务。
)
pause
