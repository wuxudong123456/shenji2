@echo off
chcp 65001 >nul
title 审计实务工坊 桌面版

echo.
echo ╔══════════════════════════════════════╗
echo ║   审计实务工坊 桌面版 v1.0.0         ║
echo ║   AI多智能体审计分析平台              ║
echo ╚══════════════════════════════════════╝
echo.

:: 检查网关是否运行
echo [1/3] 检查网关服务...
curl -s http://192.168.3.164:18791/health >nul 2>&1
if %errorlevel% neq 0 (
    echo [警告] 网关服务 (192.168.3.164:18791) 未响应!
    echo 请先启动服务器上的 OpenSquilla 网关。
    echo.
    pause
    exit /b 1
)
echo       网关连接正常 ✓

:: 检查 Node.js
echo [2/3] 检查运行环境...
where node >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Node.js，请先安装 Node.js 20+
    echo 下载: https://nodejs.org/
    pause
    exit /b 1
)

:: 安装依赖（首次）
if not exist "node_modules" (
    echo [3/3] 首次运行，安装依赖...
    call npm install
) else (
    echo [3/3] 依赖已就绪 ✓
)

echo.
echo 启动桌面应用...
call npm start
