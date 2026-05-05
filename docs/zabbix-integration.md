# Zabbix 对接配置指南（Zabbix 7.0 LTS）

> 将 Zabbix 告警接入 AIOps 智能处置平台。基于 Zabbix 7.0 LTS + Ubuntu 24.04 + Windows Server 2025 编写。

---

## 一、前提条件

| 条件 | 状态 |
|------|------|
| AIOps 服务已启动 (VM1) | `docker compose ps` 看到 5 个容器 |
| Zabbix Server 运行中 (VM2) | `systemctl is-active zabbix-server` |
| VM3/VM4 已加入 Zabbix 监控 | Zabbix Web 主机列表绿色 |
| 飞书机器人 Webhook 已配置 | `.env` 中 `FEISHU_WEBHOOK_URL` 已填写 |

### 网络连通性验证

```bash
# 在 VM2 (Zabbix Server) 上测试 AIOps API 是否可达
curl -s http://<VM1_IP>:8000/health
# 期望返回: {"status": "ok"}
```

### 集群角色说明

| 主机 | 角色 | AIOps 可自动修复 |
|------|------|:-:|
| VM1 (aiops-core) | AIOps 主控 | - |
| VM2 (aiops-monitor) | Zabbix + Grafana | - |
| VM3 (aiops-target) | 被监控目标（Linux） | 是 |
| VM4 (aiops-windc) | AD/DHCP 域控（Windows） | 否（仅监控通知） |

> **重要**：AIOps 不管理 Windows 机器。VM4 的告警会推送到飞书，但不会自动执行修复操作。

---

## 二、Zabbix Media Type 配置（Zabbix 7.0）

### 2.1 创建 Webhook Media Type

1. 打开 Zabbix Web：`http://<VM2_IP>/zabbix`
2. 导航：**Alerts → Media types → Create media type**
3. 填写：

| 字段 | 值 |
|------|-----|
| Name | `AIOps Webhook` |
| Type | `Webhook` |
| Parameters | 见下方表格 |

**Parameters（在 Parameters 标签页添加）：**

删除默认的 `URL`、`HTTPProxy`、`To`、`Subject`、`Message` 参数，替换为以下自定义参数：

| Name | Value |
|------|-------|
| `event_id` | `{EVENT.ID}` |
| `event_name` | `{EVENT.NAME}` |
| `severity` | `{EVENT.SEVERITY}` |
| `hostname` | `{HOST.NAME}` |
| `host_ip` | `{HOST.IP}` |
| `trigger_id` | `{TRIGGER.ID}` |
| `message` | `{EVENT.OPDATA}` |
| `timestamp` | `{EVENT.DATE}T{EVENT.TIME}Z` |
| `status` | `{EVENT.STATUS}` |

> **宏说明（Zabbix 7.0）**：
> - `{EVENT.SEVERITY}` 返回数字 0~5（0=not classified, 1=information, 2=warning, 3=average, 4=high, 5=disaster）
> - `{EVENT.STATUS}` 返回数字（0=recovery, 1=problem）
> - `{HOST.IP}` 返回 Agent 配置的 IP 地址（即 `zabbix_agent2.conf` 中 `Server` 或 `ListenIP` 对应的地址）
> - `{EVENT.OPDATA}` 返回触发器的附加数据（如 "Disk usage is 95%"）

### 2.2 配置 Message Templates

在 Media Type 的 **Message templates** 标签页，**必须添加**以下模板（Zabbix 7.0 要求）：

| Message type | Subject | Message |
|---|---|---|
| Message | `AIOps alert` | `AIOps alert` |
| Recovery | `AIOps recovery` | `AIOps recovery` |
| Update | `AIOps update` | `AIOps update` |

> Subject 和 Message 的内容不重要（我们用自定义 Parameters 传递数据），但必须定义，否则 Zabbix 不会触发 Webhook。

### 2.3 编写 Webhook Script

点击 **Script** 标签页的编辑按钮，粘贴以下 JavaScript：

```javascript
var params = JSON.parse(value);

// Zabbix 7.0 severity 映射（数字 → 字符串）
var severity_map = {
    '0': 'not_classified',
    '1': 'info',
    '2': 'warning',
    '3': 'average',
    '4': 'high',
    '5': 'disaster'
};

// Zabbix 7.0 status 映射（0=recovery, 1=problem）
var status_str = params.status === '0' ? 'recovery' : 'problem';

var payload = {
    event_id: params.event_id,
    event_name: params.event_name,
    severity: severity_map[params.severity] || 'not_classified',
    hostname: params.hostname,
    host_ip: params.host_ip,
    trigger_id: params.trigger_id,
    message: params.message || '',
    timestamp: params.timestamp,
    status: status_str
};

var request = new HttpRequest();
request.addHeader('Content-Type: application/json');

var response = request.post(
    'http://<VM1_IP>:8000/webhook/zabbix',
    JSON.stringify(payload)
);

// 解析 AIOps 响应
try {
    var resp = JSON.parse(response);
    if (resp.status === 'accepted' || resp.status === 'duplicate') {
        return 'OK';
    }
    return 'FAIL: ' + response;
} catch (e) {
    return 'FAIL: ' + response;
}
```

> **重要**：将 `http://<VM1_IP>:8000/webhook/zabbix` 替换为实际的 AIOps 服务地址（如 `http://192.168.1.10:8000/webhook/zabbix`）。

### 2.4 配置 Options

在 **Options** 标签页：

| 设置 | 值 |
|------|-----|
| Timeout | `30s` |
| Attempts | `3` |

### 2.5 测试 Media Type

Zabbix 7.0 支持在 Web 界面直接测试 Webhook：

1. 在 Media types 列表中，找到 `AIOps Webhook`
2. 点击右侧 **Test** 按钮
3. 在弹出窗口中填写测试参数：

| 参数 | 测试值 |
|------|--------|
| event_id | `99999` |
| event_name | `Test alert from Zabbix` |
| severity | `4` |
| hostname | `aiops-target` |
| host_ip | `192.168.1.12` |
| trigger_id | `99999` |
| message | `This is a test alert` |
| timestamp | `2026-05-05T10:00:00Z` |
| status | `1` |

4. 点击 **Test**
5. 看到 "Media type test successful." 表示配置正确

---

## 三、Zabbix Action 配置（Zabbix 7.0）

### 3.1 创建触发动作

1. 导航：**Alerts → Actions → Trigger actions → Create action**
2. **Action 标签页**：

| 字段 | 值 |
|------|-----|
| Name | `Send to AIOps` |

**Conditions（添加以下条件）：**

| Condition | Operator | Value |
|-----------|----------|-------|
| Trigger severity | >= | Warning |
| Host group | = | Linux servers |

> 如果希望 VM4 的告警也接入 AIOps，将 Host group 改为包含所有主机组，或添加第二个条件 `Host group = Windows servers`。

3. **Operations 标签页**：

点击 **Operations** → **New**：

| 字段 | 值 |
|------|-----|
| Send to Media types | `AIOps Webhook` |
| Send to Users | 选择 Admin（或已配置 Media 的用户） |

4. 点击 **Add** 保存

### 3.2 创建恢复动作（推荐）

恢复事件能让 AIOps 知道问题已自行恢复，避免重复处理。

1. 导航：**Alerts → Actions → Recovery actions → Create action**
2. **Action 标签页**：Name = `AIOps Recovery`
3. **Operations 标签页**：同样配置 `AIOps Webhook`
4. **Add** 保存

---

## 四、Webhook API 参考

### 4.1 告警接收接口

```
POST http://<VM1_IP>:8000/webhook/zabbix
Content-Type: application/json
```

**请求体（Alert 模型）：**

```json
{
    "event_id": "12345",
    "event_name": "Disk usage > 90%",
    "severity": "high",
    "hostname": "aiops-target",
    "host_ip": "192.168.1.12",
    "trigger_id": "10001",
    "message": "Disk usage is 95% on /tmp",
    "timestamp": "2026-05-01T10:00:00Z",
    "status": "problem"
}
```

**字段说明：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `event_id` | string | 是 | Zabbix 事件 ID |
| `event_name` | string | 是 | 触发器名称 |
| `severity` | string | 是 | `not_classified` / `info` / `warning` / `average` / `high` / `disaster` |
| `hostname` | string | 是 | 主机名 |
| `host_ip` | string | 是 | 主机 IP |
| `trigger_id` | string | 是 | 触发器 ID |
| `message` | string | 是 | 告警详情 |
| `timestamp` | datetime | 是 | ISO 8601 格式 |
| `status` | string | 是 | `problem` 或 `recovery` |

**响应：**

```json
// 成功
{"status": "accepted", "event_id": "12345", "stream_id": "1234567890-0"}

// 重复告警（相同 event_id）
{"status": "duplicate", "event_id": "12345"}
```

### 4.2 飞书审批回调接口

```
POST http://<VM1_IP>:8000/webhook/feishu
Content-Type: application/json
```

飞书按钮点击后自动回调，无需手动调用。

---

## 五、手动测试（curl）

在 VM1 或任何能访问 AIOps 的机器上执行：

### 5.1 VM3 磁盘告警测试

```bash
curl -X POST http://localhost:8000/webhook/zabbix \
  -H "Content-Type: application/json" \
  -d '{
    "event_id": "test-disk-001",
    "event_name": "Disk usage > 90%",
    "severity": "high",
    "hostname": "aiops-target",
    "host_ip": "192.168.1.12",
    "trigger_id": "10001",
    "message": "Disk usage is 95% on /tmp",
    "timestamp": "2026-05-05T10:00:00Z",
    "status": "problem"
  }'
```

### 5.2 VM3 服务异常测试

```bash
curl -X POST http://localhost:8000/webhook/zabbix \
  -H "Content-Type: application/json" \
  -d '{
    "event_id": "test-nginx-001",
    "event_name": "nginx process is not running",
    "severity": "high",
    "hostname": "aiops-target",
    "host_ip": "192.168.1.12",
    "trigger_id": "10002",
    "message": "nginx service is down on aiops-target",
    "timestamp": "2026-05-05T10:05:00Z",
    "status": "problem"
  }'
```

### 5.3 VM4 AD 域控告警测试

```bash
curl -X POST http://localhost:8000/webhook/zabbix \
  -H "Content-Type: application/json" \
  -d '{
    "event_id": "test-ad-001",
    "event_name": "AD Domain Controller service is down",
    "severity": "disaster",
    "hostname": "aiops-windc",
    "host_ip": "192.168.1.13",
    "trigger_id": "20001",
    "message": "NTDS service is not running on aiops-windc",
    "timestamp": "2026-05-05T10:10:00Z",
    "status": "problem"
  }'
```

> VM4 的告警只推送到飞书通知，不会自动执行修复。

### 5.4 验证流程

```bash
# 1. 检查 AIOps 日志
docker compose -f /opt/aiops/docker-compose.yml logs aiops --tail 20

# 2. 检查 Redis Stream 中是否有告警
docker compose -f /opt/aiops/docker-compose.yml exec redis redis-cli XLEN aiops:alerts

# 3. 检查 Temporal UI
# 浏览器打开 http://<VM1_IP>:8080 查看 workflow 列表

# 4. 检查飞书是否收到消息
```

---

## 六、故障注入脚本

### 6.1 VM3 脚本（Ubuntu 24.04）

以下脚本在 VM3 (aiops-target) 上执行。

#### 磁盘填满

```bash
#!/bin/bash
# 文件: /opt/demo-scripts/fill-disk.sh
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
```

**恢复：** `rm -f /tmp/aiops-test-fill`

#### nginx 服务停止

```bash
#!/bin/bash
# 文件: /opt/demo-scripts/stop-nginx.sh
# 说明: 停止 nginx 服务模拟进程异常

echo "[*] 当前 nginx 状态:"
systemctl status nginx --no-pager | head -5

echo "[*] 正在停止 nginx..."
systemctl stop nginx

echo "[!] nginx 已停止，等待 Zabbix 告警触发"
echo "[!] 恢复命令: systemctl start nginx"
```

**恢复：** `systemctl start nginx`

#### redis 服务停止

```bash
#!/bin/bash
# 文件: /opt/demo-scripts/stop-redis.sh
# 说明: 停止 redis 服务模拟进程异常
# 注意: Ubuntu 24.04 上 redis 服务名是 redis-server

echo "[*] 当前 redis 状态:"
systemctl status redis-server --no-pager | head -5

echo "[*] 正在停止 redis..."
systemctl stop redis-server

echo "[!] redis 已停止，等待 Zabbix 告警触发"
echo "[!] 恢复命令: systemctl start redis-server"
```

**恢复：** `systemctl start redis-server`

#### CPU 压力测试

```bash
#!/bin/bash
# 文件: /opt/demo-scripts/stress-cpu.sh
# 用法: bash stress-cpu.sh [核心数，默认2] [持续秒数，默认120]
# 说明: 用 stress-ng 模拟 CPU 高负载

CORES=${1:-2}
DURATION=${2:-120}

# 安装 stress-ng（如果没有）
if ! command -v stress-ng &> /dev/null; then
    echo "[*] 安装 stress-ng..."
    apt-get install -y stress-ng > /dev/null 2>&1
fi

echo "[*] 启动 CPU 压力测试: ${CORES} 核心, 持续 ${DURATION} 秒"
stress-ng --cpu ${CORES} --timeout ${DURATION}s &

echo "[!] 压力测试已启动，PID: $!"
echo "[!] ${DURATION} 秒后自动停止"
echo "[!] 提前停止: kill $!"
```

**恢复：** `pkill stress-ng`（或等待自动停止）

#### 内存压力测试

```bash
#!/bin/bash
# 文件: /opt/demo-scripts/stress-memory.sh
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
```

**恢复：** `pkill stress-ng`（或等待自动停止）

### 6.2 VM4 脚本（Windows Server 2025）

以下脚本在 VM4 (aiops-windc) 上以管理员身份执行。

#### 停止关键服务

```powershell
# 文件: C:\demo-scripts\stop-services.ps1
# 用法: .\stop-services.ps1 -Service DHCP
# 说明: 在 VM4 上停止 AD/DHCP/DNS 服务模拟故障
# 需要: 以管理员身份运行 PowerShell

param(
    [ValidateSet("DHCP", "DNS", "AD")]
    [string]$Service = "DHCP"
)

$service_map = @{
    "DHCP" = "DHCPServer"
    "DNS"  = "DNS"
    "AD"   = "NTDS"
}

$svc_name = $service_map[$Service]

Write-Host "[*] 当前 $Service ($svc_name) 状态:" -ForegroundColor Cyan
Get-Service $svc_name | Format-Table Name, Status -AutoSize

Write-Host "[*] 正在停止 $Service..." -ForegroundColor Yellow
Stop-Service $svc_name -Force

Write-Host "[!] $Service 已停止，等待 Zabbix 告警触发" -ForegroundColor Red
Write-Host "[!] 恢复命令: Start-Service $svc_name" -ForegroundColor Green
```

**恢复方法：**
```powershell
Start-Service DHCPServer   # DHCP
Start-Service DNS          # DNS
Start-Service NTDS         # AD 域控
```

> **注意**：停止 NTDS（AD 域控）会影响域内所有认证，仅在测试环境使用。停止 DHCPServer 会导致 DHCP 客户端无法获取 IP。

#### 故障注入脚本（定时触发）

```powershell
# 文件: C:\demo-scripts\inject-faults.ps1
# 说明: 循环停止/恢复服务，模拟间歇性故障
# 用法: .\inject-faults.ps1 -Service DHCP -Interval 300

param(
    [ValidateSet("DHCP", "DNS", "AD")]
    [string]$Service = "DHCP",
    [int]$Interval = 300  # 秒
)

$service_map = @{
    "DHCP" = "DHCPServer"
    "DNS"  = "DNS"
    "AD"   = "NTDS"
}

$svc_name = $service_map[$Service]

while ($true) {
    Write-Host "[$(Get-Date)] 停止 $Service..." -ForegroundColor Yellow
    Stop-Service $svc_name -Force

    Start-Sleep -Seconds 60

    Write-Host "[$(Get-Date)] 恢复 $Service..." -ForegroundColor Green
    Start-Service $svc_name

    Write-Host "[$(Get-Date)] 等待 ${Interval} 秒后再次触发..." -ForegroundColor Cyan
    Start-Sleep -Seconds $Interval
}
```

---

## 七、一键部署脚本

### 7.1 部署到 VM3（Linux）

在 VM1 上执行：

```bash
# 创建目录
ssh root@aiops-target "mkdir -p /opt/demo-scripts"

# 上传脚本
scp docs/scripts/*.sh root@aiops-target:/opt/demo-scripts/

# 设置权限
ssh root@aiops-target "chmod +x /opt/demo-scripts/*.sh"

# 验证
ssh root@aiops-target "ls -la /opt/demo-scripts/"
```

### 7.2 部署到 VM4（Windows）

在 VM4 上以管理员身份打开 PowerShell：

```powershell
# 创建目录
New-Item -ItemType Directory -Path "C:\demo-scripts" -Force

# 如果 VM1 上有 SMB 共享，可以直接复制
# 否则手动将 stop-services.ps1 和 inject-faults.ps1 保存到 C:\demo-scripts\

# 设置执行策略
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

---

## 八、端到端测试流程

### 测试 1：VM3 磁盘告警 → AI 分析 → 飞书审批 → 自动清理

```bash
# 步骤 1: VM3 上制造磁盘满
ssh root@aiops-target "bash /opt/demo-scripts/fill-disk.sh 800"

# 步骤 2: 等待 1~3 分钟，观察 Zabbix Web 是否触发告警
# Zabbix Web → Monitoring → Problems

# 步骤 3: 检查飞书是否收到 AI 分析卡片（根因、置信度、推荐 Runbook）

# 步骤 4: 在飞书上点击"按建议执行"

# 步骤 5: 验证 VM3 磁盘是否被清理
ssh root@aiops-target "df -h /tmp"
```

### 测试 2：VM3 服务异常 → AI 分析 → 飞书审批 → 自动重启

```bash
# 步骤 1: VM3 上停止 nginx
ssh root@aiops-target "bash /opt/demo-scripts/stop-nginx.sh"

# 步骤 2: 等待 Zabbix 告警（1~3 分钟）

# 步骤 3: 检查飞书 AI 卡片（应推荐 service_restart，参数 service_name=nginx）

# 步骤 4: 在飞书上点击"按建议执行"

# 步骤 5: 验证 nginx 是否恢复
ssh root@aiops-target "systemctl is-active nginx"
```

### 测试 3：VM4 AD 域控告警 → 飞书通知（无自动修复）

```powershell
# 步骤 1: VM4 上停止 DHCP 服务
Stop-Service DHCPServer -Force

# 步骤 2: 等待 Zabbix 告警（1~3 分钟）

# 步骤 3: 检查飞书是否收到告警卡片
# 卡片应显示"aiops-windc"主机的告警

# 步骤 4: 人工在 VM4 上恢复
Start-Service DHCPServer
```

### 测试 4：降级模式（LLM 不可用时）

```bash
# 步骤 1: 在 VM1 上临时修改 .env，故意填错 LLM API Key
vi /opt/aiops/.env
# LLM_PRIMARY_API_KEY=sk-invalid-key

# 步骤 2: 重启 AIOps 服务
cd /opt/aiops && docker compose up -d --build aiops

# 步骤 3: 发送测试告警
curl -X POST http://localhost:8000/webhook/zabbix \
  -H "Content-Type: application/json" \
  -d '{"event_id":"test-degrade","event_name":"Disk usage > 90%","severity":"high","hostname":"aiops-target","host_ip":"192.168.1.12","trigger_id":"10001","message":"Disk full","timestamp":"2026-05-05T10:00:00Z","status":"problem"}'

# 步骤 4: 飞书应收到告警卡片，但没有 AI 分析区块（降级模式）

# 步骤 5: 恢复正确的 API Key 并重启
```

---

## 九、常见问题

### Zabbix Webhook 测试失败

```bash
# 1. 检查 AIOps 是否可达
curl -v http://<VM1_IP>:8000/health

# 2. 检查 VM1 防火墙
ufw status
ufw allow 8000/tcp  # 如果未开放

# 3. 检查 Zabbix Server 日志
tail -f /var/log/zabbix/zabbix_server.log | grep webhook
```

### 飞书没有收到消息

```bash
# 检查 .env 中的飞书 Webhook URL
cat /opt/aiops/.env | grep FEISHU

# 手动测试飞书 Webhook
curl -X POST "你的飞书webhook地址" \
  -H "Content-Type: application/json" \
  -d '{"msg_type":"text","content":{"text":"AIOps 测试消息"}}'
```

### Temporal Workflow 卡住

```bash
# 查看 Temporal UI: http://<VM1_IP>:8080
# 检查 workflow 状态，是否有 pending activity

# 查看 AIOps 日志
docker compose -f /opt/aiops/docker-compose.yml logs aiops --tail 50
```

### 告警重复不触发新 Workflow

AIOps 对相同 `event_id` 做了去重。测试时请使用不同的 `event_id`：

```bash
# 每次测试用不同的 event_id
"event_id": "test-$(date +%s)"
```

### Zabbix 宏不解析

- 在 Media Type 测试窗口中，需要手动将宏替换为实际值（如 `{EVENT.ID}` → `12345`）
- 实际告警触发时，Zabbix 会自动解析宏

---

## 十、Zabbix 触发器参考

### VM3 (aiops-target) — 支持自动修复

| 触发器名称 | 表达式 | 严重级别 | 对应 Runbook |
|-----------|--------|---------|-------------|
| Disk usage > 90% | `last(/aiops-target/vfs.fs.size[/tmp,pused])>90` | High | `disk_cleanup` |
| nginx is down | `last(/aiops-target/net.tcp.service[http,,80])=0` | High | `service_restart` |
| Redis is down | `last(/aiops-target/net.tcp.service[redis,,6379])=0` | High | `service_restart` |
| CPU load > 80% | `last(/aiops-target/system.cpu.load[all,avg5])>4` | Average | 人工处理 |
| Memory usage > 90% | `last(/aiops-target/vm.memory.utilization)>90` | Average | 人工处理 |

### VM4 (aiops-windc) — 仅监控通知

| 触发器名称 | 表达式 | 严重级别 | 处理方式 |
|-----------|--------|---------|---------|
| AD Domain Controller down | `last(/aiops-windc/service.info[NTDS,state])<>0` | Disaster | 人工处理 |
| DHCP Server down | `last(/aiops-windc/service.info[DHCPServer,state])<>0` | High | 人工处理 |
| DNS Server down | `last(/aiops-windc/service.info[DNS,state])<>0` | High | 人工处理 |
| CPU load > 90% | `last(/aiops-windc/system.cpu.util)>90` | Average | 人工处理 |
| Memory usage > 90% | `last(/aiops-windc/vm.memory.utilization)>90` | Average | 人工处理 |
| C: drive > 90% | `last(/aiops-windc/vfs.fs.size[C:,pused])>90` | High | 人工处理 |

> **说明**：VM4 使用 `service.info` 监控 Windows 服务状态。Zabbix 7.0 的 `Windows by Zabbix agent` 模板已内置这些监控项。

### Runbook 参数说明

| Runbook | 参数 | 说明 |
|---------|------|------|
| `disk_cleanup` | `target_host` | 目标主机 IP |
| `disk_cleanup` | `path` | 清理路径，默认 `/tmp` |
| `disk_cleanup` | `min_age_days` | 文件最小天数，默认 7 |
| `service_restart` | `target_host` | 目标主机 IP |
| `service_restart` | `service_name` | systemd 服务名（如 `nginx`、`redis-server`） |

> **注意**：`service_name` 必须是 systemd 服务名，不是进程名。查看服务名：`systemctl list-units --type=service`

---

## 十一、Zabbix 7.0 配置注意事项

### 宏返回值

Zabbix 7.0 的 Webhook 宏返回值与旧版有差异：

| 宏 | 返回值 | 说明 |
|----|--------|------|
| `{EVENT.SEVERITY}` | `0`~`5`（数字字符串） | 需要在 Script 中映射为文字 |
| `{EVENT.STATUS}` | `0` 或 `1` | 0=recovery, 1=problem |
| `{EVENT.ID}` | 数字字符串 | 事件唯一 ID |
| `{HOST.IP}` | IP 字符串 | Agent 配置的 IP |
| `{EVENT.OPDATA}` | 文本 | 触发器附加数据，可能为空 |

### Message Templates

Zabbix 7.0 要求 Webhook Media Type 必须定义 Message Templates，否则不会触发。至少定义 `Message` 类型的模板。

### Webhook 测试

Zabbix 7.0 支持在 Web 界面直接测试 Webhook（Media types → Test），比发送真实告警更快捷。测试时需要手动将宏替换为示例值。

---

_文档版本: v1.1 | 2026-05 | Zabbix 7.0 LTS + Ubuntu 24.04 + Windows Server 2025_
