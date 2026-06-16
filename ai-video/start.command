#!/usr/bin/env bash
# 双击即可启动（macOS / Linux）。首次运行如被系统拦截：右键 → 打开。
cd "$(dirname "$0")" || exit 1

if ! command -v node >/dev/null 2>&1; then
  echo "============================================"
  echo "  未检测到 Node.js"
  echo "  请先到 https://nodejs.org 安装 LTS 版本，"
  echo "  装好后再次双击本文件即可。"
  echo "============================================"
  read -r -p "按回车键退出..." _
  exit 1
fi

echo "正在启动 AI 视频生成工具..."
echo "启动后请在浏览器打开 http://localhost:3000"
echo "（关闭此窗口即可停止服务）"
echo
node server.js
read -r -p "服务已停止，按回车键退出..." _
