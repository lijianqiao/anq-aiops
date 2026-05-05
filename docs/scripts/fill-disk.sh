#!/bin/bash
# 用法: bash fill-disk.sh [MB数，默认500]
# 说明: 在 /tmp 下创建大文件模拟磁盘满

SIZE=${1:-500}
TARGET="/tmp/aiops-test-fill"

echo "[*] 正在创建 ${SIZE}MB 文件到 ${TARGET}..."
dd if=/dev/zero of=${TARGET} bs=1M count=${SIZE} 2>/dev/null

echo "[*] 当前磁盘使用率:"
df -h /tmp | tail -1

echo "[!] 已创建 ${TARGET}，等待 Zabbix 告警触发"
echo "[!] 清理命令: rm -f ${TARGET}"
