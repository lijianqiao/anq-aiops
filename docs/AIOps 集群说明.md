# AIOps 集群说明 (Ubuntu 24.04)

> 针对 4 台虚拟机的分角色初始化方案：3 台 Ubuntu 24.04 + 1 台 Windows Server 2025。

---

## 集群规划

| 主机  | OS                  | 角色                          | 默认主机名         |
| --- | ------------------- | --------------------------- | ------------- |
| VM1 | Ubuntu 24.04 LTS    | AIOps 核心节点                  | aiops-core    |
| VM2 | Ubuntu 24.04 LTS    | Zabbix + Grafana            | aiops-monitor |
| VM3 | Ubuntu 24.04 LTS    | 被监控目标                       | aiops-target  |
| VM4 | Windows Server 2025 | AD/DHCP（仅供监控）+ Zabbix Agent | aiops-windc   |

> **AIOps 不管理 Windows 机器**：VM4 仅作为被 Zabbix 监控的对象，不参与 Ansible 控制。

---

## 文件清单

```
setup-common.sh              # 3 台 Ubuntu 都要跑（基础环境）
setup-vm1-core.sh            # 仅 VM1 跑（AIOps 主控）
setup-vm2-monitor.sh         # 仅 VM2 跑（Zabbix + Grafana）
setup-vm3-target.sh          # 仅 VM3 跑（被监控目标）
setup-vm4-windows.ps1        # 仅 VM4 跑（PowerShell, Zabbix Agent 2）
AD-DHCP-Manual-Setup.md      # VM4 的 AD/DHCP 手动配置文档
README.md                    # 本文件
```

---

## 关键技术选型

|组件|选型|说明|
|---|---|---|
|基础系统|Ubuntu 24.04 LTS|长期支持到 2029|
|Zabbix|7.0 LTS|最新 LTS，支持到 2029|
|Web 数据库|MariaDB|Zabbix 后端|
|Web 前端|Apache + PHP 8.3|Ubuntu 24.04 默认|
|可视化|Grafana (清华镜像)|国内速度快|
|工作流|Temporal (容器化)|待 AIOps 开发后部署|
|推理|LangGraph (Python 3.12)|待 AIOps 开发后部署|
|防火墙|UFW|Ubuntu 默认|
|时间同步|chrony|阿里云 NTP|
|Agent|Zabbix Agent 2|比 Agent 1 性能好|

---

## 执行顺序

### 第一步：3 台 Ubuntu 跑通用脚本

把 `setup-common.sh` 上传到每台 Ubuntu VM：

```bash
# 1. 修改脚本顶部的 IP（按你的实际 IP）
sudo vim setup-common.sh
# VM1_IP="192.168.1.10"
# VM2_IP="192.168.1.11"
# VM3_IP="192.168.1.12"
# VM4_IP="192.168.1.13"

# 2. 一键执行（中途会让你选当前 VM 的角色）
sudo bash setup-common.sh --all
```

### 第二步：按角色跑专属脚本

#### VM1（核心节点）

```bash
sudo bash setup-vm1-core.sh --all
```

完成后会显示本机公钥，下一步要分发到 VM2/VM3。

#### VM2（监控节点）

```bash
# 修改密码（脚本顶部）
sudo vim setup-vm2-monitor.sh
# ZABBIX_DB_PASS="Zabbix@2026"
# MARIADB_ROOT_PASS="Root@2026"

sudo bash setup-vm2-monitor.sh --all
```

完成后浏览器访问 `http://<VM2_IP>/zabbix` 完成 Zabbix 初始化。

> **Database host 填 `127.0.0.1`**（不要填 `localhost`，避免 socket 路径问题）

#### VM3（被监控目标）

```bash
sudo bash setup-vm3-target.sh --all
```

#### VM4（Windows Server 2025）

**第一步**：以管理员身份打开 PowerShell，执行 Zabbix Agent 安装脚本：

```powershell
# 允许执行脚本
Set-ExecutionPolicy -Scope Process Bypass -Force

# 执行
.\setup-vm4-windows.ps1 -All
```

**第二步**：按 `AD-DHCP-Manual-Setup.md` 文档手动配置 AD 与 DHCP（约 40~60 分钟）

### 第三步：在 VM1 上分发 SSH 密钥

```bash
# 在 VM1 上执行（会要求输入对应主机的 root 密码）
ssh-copy-id root@aiops-monitor   # VM2
ssh-copy-id root@aiops-target    # VM3

# 注意：VM4 (Windows) 不需要分发密钥（AIOps 不管理 Windows）

# 验证 Ansible 免密登录
ansible all -m ping
```

应该看到 2 台 Linux 主机都返回 `pong`。

---

## 命令速查

每个 Ubuntu 脚本都支持：

```bash
sudo bash <脚本名>                  # 交互式菜单
sudo bash <脚本名> --all            # 一键执行
sudo bash <脚本名> --status         # 查看状态
sudo bash <脚本名> --force --all    # 强制重新执行
sudo bash <脚本名> --help           # 帮助
```

PowerShell 脚本：

```powershell
.\setup-vm4-windows.ps1            # 交互式
.\setup-vm4-windows.ps1 -All       # 一键执行
.\setup-vm4-windows.ps1 -Status    # 查看状态
.\setup-vm4-windows.ps1 -Force     # 强制重新执行
.\setup-vm4-windows.ps1 -Help      # 帮助
```

---

## 集群验证

集群准备就绪的检查清单：

### Linux 部分

```bash
# VM1 上：
ansible all -m ping            # 2 台 Linux 都 pong
docker --version               # 可用
uv --version                   # 可用

# VM2 上：
systemctl is-active zabbix-server     # active
systemctl is-active grafana-server    # active
curl -I http://localhost/zabbix       # 200/302

# VM3 上：
systemctl is-active zabbix-agent2     # active
docker ps                              # 看到 demo-nginx, demo-redis
```

### Windows 部分（在 VM4 上 PowerShell）

```powershell
# Zabbix Agent
Get-Service "Zabbix Agent 2"   # Running

# AD 服务（手动配置后）
Get-Service NTDS, DNS, Netlogon, KDC

# DHCP
Get-Service DHCPServer

# 防火墙
Get-NetFirewallRule -DisplayName "Zabbix Agent 2 (TCP-In)"
```

### Zabbix Web 检查

打开 `http://<VM2_IP>/zabbix`：

1. 添加 VM3：`Configuration → Hosts → Create host`，关联 `Linux by Zabbix agent`
2. 添加 VM4：`Configuration → Hosts → Create host`，关联 `Windows by Zabbix agent active` + `Active Directory by Zabbix agent`
3. `Monitoring → Hosts` 应该看到 3 台主机（aiops-monitor 自身 + VM3 + VM4）状态为绿色

---

## 端到端冒烟测试

最简单的"集群通了吗"测试：

```bash
# 1. VM3 上故意填满磁盘
ssh root@aiops-target
bash /opt/demo-scripts/fill-disk.sh 800

# 2. VM2 的 Zabbix Web 上观察 VM3 是否触发磁盘告警

# 3. VM1 上用 Ansible 远程清理 VM3 的磁盘
ansible aiops-target -m shell -a "bash /opt/demo-scripts/cleanup-disk.sh"

# 4. VM2 观察告警是否自动消除
```

如果整个流程通了，AIOps 的"监控 → 告警 → 远程执行"骨架已经准备好。

### Windows 部分的测试

```powershell
# 在 VM4 上故意停 DHCP 服务
Stop-Service DHCPServer

# 1~2 分钟后在 VM2 的 Zabbix Web 上观察告警

# 恢复
Start-Service DHCPServer
```

---

## 后续开发流程

集群环境就绪后，AIOps 主程序的开发：

1. 在 VM1 的 `/opt/aiops/` 目录下开发 Python 代码
2. `/opt/aiops/docker/docker-compose.yml.template` 是预置的容器编排模板
3. 复制为 `docker-compose.yml`，按需修改后 `docker compose up -d`
4. 启动 Temporal、PostgreSQL、Redis、FastAPI 等服务

参考 AIOps 落地方案文档。

---

## 故障排查

### Ubuntu 端问题

#### Ansible ping 失败

- 检查 `/etc/hosts` 是否已写入集群映射
- 检查 SSH 密钥是否已分发（在 VM1 上 `ssh root@aiops-target` 应该免密）
- 检查目标 VM 的 UFW 是否阻挡了 22 端口

#### Zabbix Web 打不开

- 检查 Apache 状态：`systemctl status apache2`
- 检查 PHP 时区：在 `/etc/zabbix/apache.conf` 里 `php_value[date.timezone] = Asia/Shanghai`
- 检查防火墙：`ufw status` 确认 80/tcp 已开放

#### Zabbix 数据库连接失败

- Zabbix Web 向导里 **Database host 填 `127.0.0.1`**（不要填 `localhost`）
- 验证密码：`mysql -u zabbix -p'<密码>' -e "SHOW DATABASES;"`

#### Zabbix Agent 不上报

- VM3 上检查：`zabbix_agent2 -t agent.ping`
- VM3 上检查 Server 配置：`grep ^Server /etc/zabbix/zabbix_agent2.conf`
- VM2 上 Zabbix Web 的"配置 → 主机"中是否添加了 VM3

### Windows 端问题

参见 `AD-DHCP-Manual-Setup.md` 的"常见问题"章节。

### 时间不同步导致问题

- `chronyc sources` 应该看到 `^*` 标记的源
- `timedatectl` 检查时区是 Asia/Shanghai
- Windows 端：`w32tm /query /status` 检查时间源

---

## 与 CentOS 8 版本的主要差异

如果你之前用过 CentOS 8 版本的脚本，需要注意：

|维度|CentOS 8|Ubuntu 24.04|
|---|---|---|
|包管理器|dnf|apt|
|仓库源|/etc/yum.repos.d/|/etc/apt/sources.list.d/|
|防火墙|firewalld|UFW|
|服务管理|systemctl (相同)|systemctl (相同)|
|PHP|默认 7.2，需要 dnf module 切换|默认 8.3，无需切换|
|Web 服务器|httpd (Apache)|apache2 (Apache)|
|SSH 服务名|sshd|ssh|
|MariaDB 初始化|mysql_secure_installation|ALTER USER 直接设密|
|SELinux|默认 enforcing，需改 permissive|默认 AppArmor，无需操心|
|Zabbix 包|.rpm|.deb|

---

_版本: v1.0 (Ubuntu 24.04) | 2026-04_