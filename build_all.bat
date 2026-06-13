@echo off
chcp 65001 >nul
echo ===============================================
echo    TXT查重工具 - 完整打包脚本
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
echo 正在打包服务端...
echo.
venv\Scripts\pyinstaller --onefile --name txt-dedup-server --icon=app.ico --hidden-import=psutil --hidden-import=health_monitor --hidden-import=notifier --hidden-import=logging_utils --hidden-import=version --hidden-import=sharded_lmdb --collect-all lmdb server.py

echo.
echo 正在打包客户端...
echo.
venv\Scripts\pyinstaller --onefile --name txt-dedup-client --icon=app.ico --hidden-import=notifier --hidden-import=logging_utils --hidden-import=version client.py

if %errorlevel% equ 0 (
    echo.
    echo ===============================================
    echo    打包成功！
    echo ===============================================
    echo.
    echo 可执行文件位置:
    echo   - dist\txt-dedup-server.exe (服务端)
    echo   - dist\txt-dedup-client.exe (客户端)
    echo.
    echo 正在创建发布包...
    if not exist release mkdir release
    copy dist\txt-dedup-server.exe release\
    copy dist\txt-dedup-client.exe release\
    copy README.md release\
    echo.
    echo 发布包位置: release\
    echo.
) else (
    echo.
    echo 打包失败！
    echo.
)

pause
