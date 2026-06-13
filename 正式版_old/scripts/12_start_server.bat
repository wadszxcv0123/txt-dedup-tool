@echo off
chcp 65001 >nul 2>&1

setlocal EnableDelayedExpansion

set "BANNER="
set "BANNER=!BANNER!  ╔══════════════════════════════════════════════════════════════════════════════╗"
set "BANNER=!BANNER!  ║                    TXT 查 重 工 具 - 服 务 端                              ║"
set "BANNER=!BANNER!  ╚══════════════════════════════════════════════════════════════════════════════╝"

set "SUBTITLE=v1.0.4"

:menu
cls
color 0A
echo.
echo !BANNER!
echo.
echo                           %SUBTITLE%
echo.
echo  ╔══════════════════════════════════════════════════════════════════════════════╗
echo  ║                          主 菜 单                                           ║
echo  ╠══════════════════════════════════════════════════════════════════════════════╣
echo  ║                                                                              ║
echo  ║    [1] 启动服务端                    [2] 修改端口                             ║
echo  ║                                                                              ║
echo  ║    [3] 查看配置                      [4] 查看日志                             ║
echo  ║                                                                              ║
echo  ║    [5] 帮助信息                      [6] 退出                                 ║
echo  ║                                                                              ║
echo  ╚══════════════════════════════════════════════════════════════════════════════╝
echo.
set /p choice=" 请输入选项 [1-6]: "

if "%choice%"=="1" goto start_server
if "%choice%"=="2" goto change_port
if "%choice%"=="3" goto show_config
if "%choice%"=="4" goto show_logs
if "%choice%"=="5" goto show_help
if "%choice%"=="6" goto end

echo.
echo  [!] 输入错误，请重新选择！
timeout /t 2 >nul
goto menu

:start_server
cls
color 0E
echo.
echo !BANNER!
echo.
echo                           %SUBTITLE%
echo.
echo  ╔══════════════════════════════════════════════════════════════════════════════╗
echo  ║                           启动服务端                                         ║
echo  ╚══════════════════════════════════════════════════════════════════════════════╝
echo.

cd /d "%~dp0"

if exist "txt-dedup-server.exe" (
    start "TXT查重工具-服务端" cmd /k "title TXT查重工具-服务端 && txt-dedup-server.exe"
) else (
    if exist "venv\Scripts\python.exe" (
        start "TXT查重工具-服务端" cmd /k "title TXT查重工具-服务端 && venv\Scripts\python.exe server.py"
    ) else (
        start "TXT查重工具-服务端" cmd /k "title TXT查重工具-服务端 && python server.py"
    )
)

echo  [OK] 服务端已在新窗口中启动！
echo.
echo  [INFO] 请查看弹出的窗口了解服务端状态
echo.
timeout /t 3 >nul
goto menu

:change_port
cls
color 0B
echo.
echo !BANNER!
echo.
echo                           %SUBTITLE%
echo.
echo  ╔══════════════════════════════════════════════════════════════════════════════╗
echo  ║                           修改监听端口                                       ║
echo  ╚══════════════════════════════════════════════════════════════════════════════╝
echo.

cd /d "%~dp0"

set "current_port=8888"
if exist "server_config.ini" (
    findstr /C:"port" server_config.ini >nul 2>&1
    if !errorlevel!==0 (
        for /f "tokens=2 delims==" %%a in ('findstr /I "port" server_config.ini') do (
            set "current_port=%%a"
        )
    )
)

echo  当前端口: %current_port%
echo.
set /p new_port=" 请输入新的端口号 (留空取消): "

if not defined new_port (
    echo  [!] 已取消修改！
    timeout /t 2 >nul
    goto menu
)

echo.
echo  [INFO] 正在更新配置...

if exist "server_config.ini" (
    powershell -Command "(Get-Content server_config.ini) -replace 'port = .*', 'port = %new_port%' | Set-Content server_config.ini"
) else (
    echo [Server] > server_config.ini
    echo port = %new_port% >> server_config.ini
    echo host = 0.0.0.0 >> server_config.ini
)

echo  [OK] 端口已更新为: %new_port%
echo  [INFO] 请重新启动服务端使配置生效！
echo.
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

if exist "server_config.ini" (
    echo  配置文件: server_config.ini
    echo  ──────────────────────────────────────────────────────────────────────────────
    type server_config.ini
) else (
    echo  配置文件: 未找到 (将使用默认配置)
    echo  ──────────────────────────────────────────────────────────────────────────────
    echo    [Server]
    echo    port = 8888
    echo    host = 0.0.0.0
    echo    index_dir = .dedup_index
    echo    use_leveldb = true
)

echo.
pause
goto menu
