#!/bin/bash
# 用法: bash stress-memory.sh [MB数，默认1024] [持续秒数，默认120]
# 说明: 用 stress-ng 模拟内存高占用

SIZE=${1:-1024}
DURATION=${2:-120}

if ! command -v stress-ng &> /dev/null; then
    echo "[*] 安装 stress-ng..."
    apt-get install -y stress-ng > /dev/null 2>&1
fi

echo "[*] 启动内存压力测试: ${SIZE}MB, 持续 ${DURATION} 秒"
stress-ng --vm 1 --vm-bytes ${SIZE}M --timeout ${DURATION}s &

echo "[!] 内存压力测试已启动，PID: $!"
echo "[!] ${DURATION} 秒后自动停止"
echo "[!] 提前停止: kill $!"
