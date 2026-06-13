@echo off
chcp 65001 >nul 2>&1

setlocal EnableDelayedExpansion

set "BANNER="
set "BANNER=!BANNER!  ╔══════════════════════════════════════════════════════════════════════════════╗"
set "BANNER=!BANNER!  ║                    TXT 查 重 工 具 - 客 户 端                              ║"
set "BANNER=!BANNER!  ╚══════════════════════════════════════════════════════════════════════════════╝"

set "SUBTITLE=v1.0.4"

:menu
cls
color 0B
echo.
echo !BANNER!
echo.
echo                           %SUBTITLE%
echo.
echo  ╔══════════════════════════════════════════════════════════════════════════════╗
echo  ║                          主 菜 单                                           ║
echo  ╠══════════════════════════════════════════════════════════════════════════════╣
echo  ║                                                                              ║
echo  ║    [1] 输入文件路径查重              [2] 设置服务端地址                       ║
echo  ║                                                                              ║
echo  ║    [3] 查看配置                      [4] 测试邮件                            ║
echo  ║                                                                              ║
echo  ║    [5] 帮助信息                      [6] 退出                                 ║
echo  ║                                                                              ║
echo  ╚══════════════════════════════════════════════════════════════════════════════╝
echo.
echo  [快捷方式] 直接将TXT文件拖放到 txt-dedup-client.exe 上即可开始查重
echo.
set /p choice=" 请输入选项 [1-6]: "

if "%choice%"=="1" goto file_input
if "%choice%"=="2" goto set_server
if "%choice%"=="3" goto show_config
if "%choice%"=="4" goto test_email
if "%choice%"=="5" goto show_help
if "%choice%"=="6" goto end

echo.
echo  [!] 输入错误，请重新选择！
timeout /t 2 >nul
goto menu

:file_input
cls
color 0E
echo.
echo !BANNER!
echo.
echo                           %SUBTITLE%
echo.
echo  ╔══════════════════════════════════════════════════════════════════════════════╗
echo  ║                           文件路径查重                                       ║
echo  ╚══════════════════════════════════════════════════════════════════════════════╝
echo.

set /p filepath=" 请输入TXT文件路径: "
if not defined filepath (
    echo  [!] 未输入文件路径！
    timeout /t 2 >nul
    goto menu
)

cd /d "%~dp0"
echo.
echo  [INFO] 正在处理: %filepath%
echo.

if exist "txt-dedup-client.exe" (
    start /wait cmd /k "title TXT查重 && txt-dedup-client.exe ""%filepath%"""
) else (
    if exist "venv\Scripts\python.exe" (
        start /wait cmd /k "title TXT查重 && venv\Scripts\python.exe client.py ""%filepath%"""
    ) else (
        start /wait cmd /k "title TXT查重 && python client.py ""%filepath%"""
    )
)

echo.
echo  [OK] 操作完成！
pause
goto menu

:set_server
cls
color 0A
echo.
echo !BANNER!
echo.
echo                           %SUBTITLE%
echo.
echo  ╔══════════════════════════════════════════════════════════════════════════════╗
echo  ║                           设置服务端地址                                     ║
echo  ╚══════════════════════════════════════════════════════════════════════════════╝
echo.

cd /d "%~dp0"

set "current_server=http://localhost:8888"
if exist "config.ini" (
    for /f "tokens=2 delims==" %%a in ('findstr /I "address" config.ini') do (
        set "current_server=%%a"
    )
)

echo  当前服务端地址: %current_server%
echo.
set /p new_server=" 请输入新的服务端地址 (例: http://192.168.1.100:8888): "

if not defined new_server (
    echo  [!] 未输入地址，取消设置！
    timeout /t 2 >nul
    goto menu
)

echo.
echo  [INFO] 正在更新配置...

if exist "txt-dedup-client.exe" (
    txt-dedup-client.exe --set-server "%new_server%"
) else (
    if exist "venv\Scripts\python.exe" (
        venv\Scripts\python.exe client.py --set-server "%new_server%"
    ) else (
        python client.py --set-server "%new_server%"
    )
)

echo.
echo  [OK] 服务端地址已更新为: %new_server%
pause
goto menu

:show_config
cls
color 0D
echo.
echo !BANNER!
echo.
echo                           %SUBTITLE%
echo.
echo  ╔══════════════════════════════════════════════════════════════════════════════╗
echo  ║                           当前配置信息                                       ║
echo  ╚══════════════════════════════════════════════════════════════════════════════╝
echo.

cd /d "%~dp0"

if exist "config.ini" (
    echo  配置文件: config.ini
    echo  ──────────────────────────────────────────────────────────────────────────────
    type config.ini
) else (
    echo  [!] 配置文件不存在
    echo  ──────────────────────────────────────────────────────────────────────────────
    echo    [Server]
    echo    address = http://localhost:8888
    echo.
    echo    [Client]
    echo    save_unique = false
    echo    save_duplicates = false
)

echo.
pause
goto menu

:test_email
cls
color 0C
echo.
echo !BANNER!
echo.
echo                           %SUBTITLE%
echo.
echo  ╔══════════════════════════════════════════════════════════════════════════════╗
echo  ║                           测试邮件配置                                       ║
echo  ╚══════════════════════════════════════════════════════════════════════════════╝
echo.

cd /d "%~dp0"

echo  [INFO] 正在测试邮件配置，请稍候...
echo.

if exist "txt-dedup-client.exe" (
    txt-dedup-client.exe --test-email
) else (
    if exist "venv\Scripts\python.exe" (
        venv\Scripts\python.exe client.py --test-email
    ) else (
        python client.py --test-email
    )
)

echo.
pause
goto menu

:show_help
cls
color 0F
echo.
echo !BANNER!
echo.
echo                           %SUBTITLE%
echo.
echo  ╔══════════════════════════════════════════════════════════════════════════════╗
echo  ║                           使用帮助                                           ║
echo  ╚══════════════════════════════════════════════════════════════════════════════╝
echo.
echo  [1] 输入文件路径查重
echo      - 输入TXT文件的完整路径
echo      - 自动连接服务端进行查重
echo.
echo  [2] 设置服务端地址
echo      - 输入格式: http://IP:端口
echo      - 例如: http://192.168.1.100:8888
echo.
echo  [3] 查看配置
echo      - 显示当前配置文件内容
echo.
echo  [4] 测试邮件
echo      - 测试邮件通知功能是否正常
echo.
echo  [5] 快捷方式
echo      - 将TXT文件直接拖放到 txt-dedup-client.exe 上
echo      - 自动开始查重，无需打开菜单
echo.
echo  [6] 日志文件
echo      - 位于 logs 目录下
echo      - 按日期和文件名命名
echo.
echo  ──────────────────────────────────────────────────────────────────────────────
echo  配置文件: config.ini - 在其中设置服务端地址和邮件通知
echo.
pause
goto menu

:end
cls
color 07
echo.
echo  ╔══════════════════════════════════════════════════════════════════════════════╗
echo  ║                    感谢使用 TXT 查 重 工 具                                  ║
echo  ╚══════════════════════════════════════════════════════════════════════════════╝
echo.
timeout /t 1 >nul
exit /b 0