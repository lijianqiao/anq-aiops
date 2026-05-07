#!/bin/bash
# 用法: bash fill-disk.sh [MB数，默认500]
# 说明: 在 /tmp 下创建大文件模拟磁盘满
#
# 注意: 文件 mtime 强制设为 30 天前，
# 这样 disk_cleanup runbook 默认 min_age_days=7 能正常匹配并清理掉。

SIZE=${1:-500}
TARGET="/tmp/aiops-test-fill"

echo "[*] 正在创建 ${SIZE}MB 文件到 ${TARGET}..."
dd if=/dev/zero of=${TARGET} bs=1M count=${SIZE} 2>/dev/null

# 把 mtime 改成 30 天前，让 ansible find age=7d 能匹配上
touch -d "30 days ago" ${TARGET}

echo "[*] 当前磁盘使用率:"
df -h /tmp | tail -1

echo "[*] 文件年龄："
stat -c '%y' ${TARGET}

echo "[!] 已创建 ${TARGET}（mtime=30天前），等待 Zabbix 告警触发"
echo "[!] 手动清理命令: rm -f ${TARGET}"
