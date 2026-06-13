@echo off
chcp 65001 >nul
echo ===============================================
echo    TXT查重工具 - 服务端打包脚本
echo ===============================================
echo.

if not exist "venv\Scripts\python.exe" (
    echo 正在创建虚拟环境...
    python -m venv venv
    echo 正在安装依赖...
    venv\Scripts\pip install -r requirements.txt
    venv\Scripts\pip install pyinstaller psutil
)

echo.
echo 正在打包服务端...
echo.

venv\Scripts\pyinstaller --onefile --name txt-dedup-server --icon=app.ico --version-file=version_server.txt --hidden-import=psutil --hidden-import=health_monitor --hidden-import=notifier --hidden-import=logging_utils --hidden-import=version server.py

if %errorlevel% equ 0 (
    copy /Y dist\txt-dedup-server.exe dist\txt_deduper.exe >nul 2>&1
    echo.
    echo ===============================================
    echo    打包成功！
    echo ===============================================
    echo.
    echo 可执行文件位置: dist\txt-dedup-server.exe
    echo 可执行文件别名: dist\txt_deduper.exe
    echo.
) else (
    echo.
    echo 打包失败！
    echo.
)

pause
