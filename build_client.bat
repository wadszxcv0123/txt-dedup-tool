@echo off
chcp 65001 >nul
echo ===============================================
echo    TXT查重工具 - 客户端打包脚本
echo ===============================================
echo.

if not exist "venv\Scripts\python.exe" (
    echo 正在创建虚拟环境...
    python -m venv venv
    echo 正在安装依赖...
    venv\Scripts\pip install -r requirements.txt
    venv\Scripts\pip install pyinstaller
)

echo.
echo 正在打包客户端...
echo.

venv\Scripts\pyinstaller --onefile --name txt-dedup-client --icon=app.ico --version-file=version_client.txt --hidden-import=notifier --hidden-import=logging_utils --hidden-import=version client.py

if %errorlevel% equ 0 (
    echo.
    echo ===============================================
    echo    打包成功！
    echo ===============================================
    echo.
    echo 可执行文件位置: dist\txt-dedup-client.exe
    echo.
) else (
    echo.
    echo 打包失败！
    echo.
)

pause
