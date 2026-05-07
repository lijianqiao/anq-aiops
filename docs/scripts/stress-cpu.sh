#!/bin/bash
# 用法: bash stress-cpu.sh [核心数，默认2] [持续秒数，默认120]
# 说明: 用 stress-ng 模拟 CPU 高负载

CORES=${1:-2}
DURATION=${2:-120}

# 安装 stress-ng（如果没有）
if ! command -v stress-ng &> /dev/null; then
    echo "[*] 安装 stress-ng..."
    sudo apt-get install -y stress-ng > /dev/null 2>&1
fi

echo "[*] 启动 CPU 压力测试: ${CORES} 核心, 持续 ${DURATION} 秒"
stress-ng --cpu ${CORES} --timeout ${DURATION}s &

echo "[!] 压力测试已启动，PID: $!"
echo "[!] ${DURATION} 秒后自动停止"
echo "[!] 提前停止: kill $!"
