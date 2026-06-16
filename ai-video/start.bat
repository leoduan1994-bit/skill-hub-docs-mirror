@echo off
chcp 65001 >nul
REM 双击即可启动（Windows）
cd /d "%~dp0"

where node >nul 2>nul
if errorlevel 1 (
  echo ============================================
  echo   未检测到 Node.js
  echo   请先到 https://nodejs.org 安装 LTS 版本，
  echo   装好后再次双击本文件即可。
  echo ============================================
  pause
  exit /b 1
)

echo 正在启动 AI 视频生成工具...
echo 启动后请在浏览器打开 http://localhost:3000
echo （关闭此窗口即可停止服务）
echo.
node server.js
pause
