#!/bin/bash
# 说明: 停止 redis 服务模拟进程异常

echo "[*] 当前 redis 状态:"
systemctl status redis-server --no-pager | head -5

echo "[*] 正在停止 redis..."
systemctl stop redis-server

echo "[!] redis 已停止，等待 Zabbix 告警触发"
echo "[!] 恢复命令: systemctl start redis-server"
