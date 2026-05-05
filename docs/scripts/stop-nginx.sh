#!/bin/bash
# 说明: 停止 nginx 服务模拟进程异常

echo "[*] 当前 nginx 状态:"
systemctl status nginx --no-pager | head -5

echo "[*] 正在停止 nginx..."
systemctl stop nginx

echo "[!] nginx 已停止，等待 Zabbix 告警触发"
echo "[!] 恢复命令: systemctl start nginx"
