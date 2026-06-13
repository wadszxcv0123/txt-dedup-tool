@echo off
chcp 65001 >nul
title TXT查重工具 - 服务端
cls
echo ===============================================
echo    TXT查重工具 - 服务端
echo ===============================================
echo.

:: 默认配置
set PORT=8888
set INDEX_DIR=

:menu
echo.
echo 请选择操作：
echo   1. 启动服务 (默认端口8888)
echo   2. 自定义端口启动
echo   3. 自定义索引目录启动
echo   4. 查看帮助
echo   5. 退出
echo.
set /p choice="请输入选项 (1-5): "

if "%choice%"=="1" goto start_default
if "%choice%"=="2" goto start_custom_port
if "%choice%"=="3" goto start_custom_index
if "%choice%"=="4" goto help
if "%choice%"=="5" exit

goto menu

:start_default
echo.
echo ===============================================
echo    正在启动服务...
echo ===============================================
echo.

if exist "venv\Scripts\python.exe" (
    venv\Scripts\python.exe server.py
) else (
    python server.py
)

pause
goto menu

:start_custom_port
echo.
set /p PORT="请输入端口号 (默认8888): "
echo.
echo ===============================================
echo    正在启动服务 (端口: %PORT%)...
echo ===============================================
echo.

if exist "venv\Scripts\python.exe" (
    venv\Scripts\python.exe server.py -p %PORT%
) else (
    python server.py -p %PORT%
)

pause
goto menu

:start_custom_index
echo.
set /p INDEX_DIR="请输入索引目录路径: "
echo.
echo ===============================================
echo    正在启动服务 (索引目录: %INDEX_DIR%)...
echo ===============================================
echo.

if exist "venv\Scripts\python.exe" (
    venv\Scripts\python.exe server.py -i "%INDEX_DIR%"
) else (
    python server.py -i "%INDEX_DIR%"
)

pause
goto menu

:help
echo.
echo ===============================================
echo    使用帮助
echo ===============================================
echo.
echo 1. 默认启动
echo    server.bat
echo.
echo 2. 自定义端口
echo    server.bat -p 9999
echo.
echo 3. 自定义索引目录
echo    server.bat -i C:\data\dedup-index
echo.
echo 4. 组合使用
echo    server.bat -p 9999 -i C:\data\dedup-index
echo.
pause
goto menu
