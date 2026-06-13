@echo off
chcp 65001 >nul
echo ===============================================
echo   TXT查重工具 - 服务端(服务模式)打包脚本
echo ===============================================
echo.

if not exist "venv\Scripts\python.exe" (
    echo 正在创建虚拟环境...
    python -m venv venv
    echo 正在安装依赖...
    venv\Scripts\pip install -r requirements.txt
    venv\Scripts\pip install pyinstaller psutil pywin32
)

echo.
echo 正在安装必要依赖...
venv\Scripts\pip install psutil pywin32 >nul 2>&1

echo.
echo 正在打包服务端服务模式...
echo.

venv\Scripts\pyinstaller --onefile --name txt-dedup-server-service --icon=app.ico --version-file=version_service.txt --hidden-import=psutil --hidden-import=health_monitor --hidden-import=notifier --hidden-import=logging_utils --hidden-import=version --hidden-import=win32serviceutil --hidden-import=win32service --hidden-import=win32event --hidden-import=servicemanager --hidden-import=win32timezone --hidden-import=sharded_lmdb --collect-all lmdb server_service.py

if %errorlevel% equ 0 (
    echo.
    echo ===============================================
    echo    打包成功！
    echo ===============================================
    echo.
    echo 可执行文件位置: dist\txt-dedup-server-service.exe
    echo.
) else (
    echo.
    echo 打包失败！
    echo.
)

pause