@echo off
chcp 65001 >nul
echo ===============================================
echo    TXT查重工具 - 依赖安装脚本
echo ===============================================
echo.
echo 正在安装依赖...
echo.

pip install -r requirements.txt

if %errorlevel% neq 0 (
    echo.
    echo 安装失败！请检查网络连接或pip是否正常工作。
) else (
    echo.
    echo ===============================================
    echo    依赖安装完成！
    echo ===============================================
    echo.
)

pause
