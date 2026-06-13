@echo off
chcp 65001 >nul
echo ===============================================
echo   TXT查重工具 - Windows 服务安装
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

echo 正在安装 TXT查重工具 服务...
echo.
echo 服务名称: TXT-Dedup-Server
echo 显示名称: TXT查重工具服务端
echo 描述: 提供哈希去重和数据溯源功能
echo 启动类型: 自动（开机自启）
echo.

txt-dedup-server-service.exe install

if %errorlevel% equ 0 (
    echo.
    echo ===============================================
    echo   服务安装成功！
    echo ===============================================
    echo.
    echo 启动服务命令:
    echo   net start TXT-Dedup-Server
    echo 或运行 start_service.bat
    echo.
    echo 停止服务命令:
    echo   net stop TXT-Dedup-Server
    echo 或运行 stop_service.bat
    echo.
    echo 重启服务命令:
    echo   restart_service.bat
    echo.
    echo 查看服务状态:
    echo   sc query TXT-Dedup-Server
    echo.
    echo 卸载服务:
    echo   运行 uninstall_service.bat
    echo.
) else (
    echo.
    echo [错误] 服务安装失败，请检查：
    echo 1. 是否以管理员身份运行
    echo 2. pywin32 是否已安装
    echo.
)

pause
