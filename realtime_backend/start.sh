#!/bin/bash
# 启动/重启「声桥 VoiceHub」后端（后台常驻）。具体用法见 start.py 顶部注释：
#   ./start.sh              后台启动（已在运行则复用）
#   ./start.sh --restart    重启
#   ./start.sh --stop       停止
#   ./start.sh --status     查看状态
#   ./start.sh --foreground 前台运行
set -e
cd "$(dirname "$0")"
exec /usr/bin/env python3 start.py "$@"
