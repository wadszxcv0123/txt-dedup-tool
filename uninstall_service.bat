@echo off
chcp 65001 >nul
echo ===============================================
echo   TXT查重工具 - Windows 服务卸载
echo ===============================================
echo.

:: 以管理员身份运行检查
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 请以管理员身份运行此脚本！
    echo 右键本文件 - 以管理员身份运行
    pause
    exit /b 1
)

echo 正在停止服务...
net stop TXT-Dedup-Server >nul 2>&1

echo 正在卸载 TXT查重工具 服务...
txt-dedup-server-service.exe uninstall

if %errorlevel% equ 0 (
    echo.
    echo ===============================================
    echo   服务卸载成功！
    echo ===============================================
    echo.
    echo 如果需要重新安装，请运行 install_service.bat
    echo.
) else (
    echo.
    echo [错误] 服务卸载失败，请检查服务是否已安装
    echo.
    echo 手动卸载:
    echo   sc delete TXT-Dedup-Server
    echo.
)

pause