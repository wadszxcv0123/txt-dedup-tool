@echo off
chcp 65001 >nul
title TXT查重工具 - 客户端
cls
echo ===============================================
echo    TXT查重工具 - 客户端
echo ===============================================
echo.

:: 默认服务端地址
set SERVER=http://localhost:8888
set OUTPUT=
set DUPLICATES=
set CHECK_ONLY=

:: 检查是否有拖拽的文件
if "%~1"=="" goto interactive
goto run

:interactive
echo.
echo 请选择操作：
echo   1. 输入文件路径查重
echo   2. 拖拽文件到窗口
echo   3. 设置服务端地址
echo   4. 查看帮助
echo   5. 退出
echo.
set /p choice="请输入选项 (1-5): "

if "%choice%"=="1" goto input_file
if "%choice%"=="2" echo 请将TXT文件拖拽到此窗口... & pause & goto interactive
if "%choice%"=="3" goto set_server
if "%choice%"=="4" goto help
if "%choice%"=="5" exit

goto interactive

:input_file
echo.
set /p INPUT_FILE="请输入要查重的TXT文件路径: "
if "%INPUT_FILE%"=="" goto interactive

:: 检查文件是否存在
if not exist "%INPUT_FILE%" (
    echo 文件不存在！
    pause
    goto interactive
)

:: 询问是否需要输出文件
echo.
set /p OUTPUT_OPT="是否生成唯一数据文件？(y/n): "
if /i "%OUTPUT_OPT%"=="y" (
    set /p OUTPUT="请输入唯一数据文件路径 (直接回车使用默认): "
)

echo.
set /p DUP_OPT="是否生成重复数据文件？(y/n): "
if /i "%DUP_OPT%"=="y" (
    set /p DUPLICATES="请输入重复数据文件路径 (直接回车使用默认): "
)

echo.
set /p CHECK_ONLY_OPT="是否仅检查重复而不更新索引？(y/n): "
if /i "%CHECK_ONLY_OPT%"=="y" (
    set CHECK_ONLY=--check-only
)

:run
echo.
echo ===============================================
echo    开始查重...
echo ===============================================
echo 服务端地址: %SERVER%
echo 文件: %* %INPUT_FILE%
echo.

if exist "venv\Scripts\python.exe" (
    venv\Scripts\python.exe client.py %* %INPUT_FILE% -s %SERVER% %OUTPUT% %DUPLICATES% %CHECK_ONLY%
) else (
    python client.py %* %INPUT_FILE% -s %SERVER% %OUTPUT% %DUPLICATES% %CHECK_ONLY%
)

echo.
pause
goto interactive

:set_server
echo.
set /p SERVER="请输入服务端地址 (例如 http://192.168.1.100:8888): "
echo 服务端地址已设置为: %SERVER%
pause
goto interactive

:help
echo.
echo ===============================================
echo    使用帮助
echo ===============================================
echo.
echo 1. 单文件查重
echo    client.bat input.txt -s http://server-ip:8888
echo.
echo 2. 指定输出文件
echo    client.bat input.txt -s http://server-ip:8888 -o unique.txt -d duplicates.txt
echo.
echo 3. 仅检查重复
echo    client.bat input.txt -s http://server-ip:8888 --check-only
echo.
echo 4. 批量查重
echo    client.bat *.txt -s http://server-ip:8888
echo.
pause
goto interactive
