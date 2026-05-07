# Zabbix 对接配置指南（Zabbix 7.0 LTS）

> 将 Zabbix 告警接入 AIOps 智能处置平台。基于 Zabbix 7.0 LTS + Ubuntu 24.04 + Windows Server 2025 编写。

---

## 一、前提条件

| 条件 | 状态 |
|------|------|
| AIOps 服务已启动 (VM1) | `docker compose ps` 看到 5 个容器 |
| Zabbix Server 运行中 (VM2) | `systemctl is-active zabbix-server` |
| VM3/VM4 已加入 Zabbix 监控 | Zabbix Web 主机列表绿色 |
| 飞书应用机器人已配置 | `.env` 中 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_RECEIVE_ID` 已填写 |
| AIOps webhook token 已配置 | `.env` 中 `ZABBIX_WEBHOOK_TOKEN` 已填写（Zabbix Webhook 脚本里要带这个 token） |

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

| Name | Value | 说明 |
|------|-------|------|
| `aiops_url` | `http://<VM1-IP>:8000/webhook/zabbix` | AIOps webhook 完整地址，把 IP 换成 VM1 的实际地址 |
| `aiops_token` | `change-me` | 必须与 AIOps 的 `.env` 中 `ZABBIX_WEBHOOK_TOKEN` 保持一致 |
| `event_id` | `{EVENT.ID}` | |
| `event_name` | `{EVENT.NAME}` | |
| `severity_n` | `{EVENT.NSEVERITY}` | **数字** 0~5；不要用 `{EVENT.SEVERITY}`，那是文字 |
| `event_value` | `{EVENT.VALUE}` | **数字** 0=OK/恢复, 1=PROBLEM；不要用 `{EVENT.STATUS}`，那是文字 |
| `timestamp_unix` | `{EVENT.TIMESTAMP}` | Unix 秒；JS 里转 ISO 8601 |
| `hostname` | `{HOST.HOST}` | Zabbix 配置的主机标识（`aiops-target` 这种） |
| `host_ip` | `{HOST.IP}` | Agent 配置的 IP 地址 |
| `trigger_id` | `{TRIGGER.ID}` | |
| `message` | `{EVENT.OPDATA}` | 触发器附加数据，可能为空 |

> **Zabbix 7.0 宏陷阱**：
> - `{EVENT.SEVERITY}` / `{EVENT.STATUS}` 在 Webhook 上下文里是**文字**（`High` / `PROBLEM` 等），不能直接拿来做数值比较
> - 数字版本用 `{EVENT.NSEVERITY}` 和 `{EVENT.VALUE}`，更稳
> - `{EVENT.DATE}` 是 `YYYY.MM.DD`（点分隔）不是 `-`，拼出来不是 ISO 8601；用 `{EVENT.TIMESTAMP}` 在脚本里转更可靠
> - `{HOST.IP}` 返回 Agent 配置的 IP 地址（即 `zabbix_agent2.conf` 中 `Server` 或 `ListenIP` 对应的地址）

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

// {EVENT.NSEVERITY} 0~5 → AIOps Alert.severity 字符串
var severity_map = {
    '0': 'not_classified',
    '1': 'info',
    '2': 'warning',
    '3': 'average',
    '4': 'high',
    '5': 'disaster'
};

// {EVENT.VALUE}: 0=OK/恢复, 1=PROBLEM
var status_str = params.event_value === '0' ? 'recovery' : 'problem';

// {EVENT.TIMESTAMP} 是 Unix 秒 → ISO 8601 (UTC)
var ts_iso = new Date(parseInt(params.timestamp_unix, 10) * 1000).toISOString();

var payload = {
    event_id: params.event_id,
    event_name: params.event_name,
    severity: severity_map[params.severity_n] || 'not_classified',
    hostname: params.hostname,
    host_ip: params.host_ip,
    trigger_id: params.trigger_id,
    message: params.message || '',
    timestamp: ts_iso,
    status: status_str
};

var request = new HttpRequest();
request.addHeader('Content-Type: application/json');
// AIOps 强制要求 token；与 .env 中 ZABBIX_WEBHOOK_TOKEN 一致
request.addHeader('X-Zabbix-Token: ' + params.aiops_token);

var response = request.post(params.aiops_url, JSON.stringify(payload));

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

> **要点**：URL 和 token 都通过 Webhook params 注入，换环境只改 params 就行，不用动 JS。

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
| aiops_url | `http://192.168.1.10:8000/webhook/zabbix` |
| aiops_token | `change-me`（与 .env 中 `ZABBIX_WEBHOOK_TOKEN` 一致） |
| event_id | `99999` |
| event_name | `Test alert from Zabbix` |
| severity_n | `4` |
| event_value | `1` |
| timestamp_unix | `1746460800` |
| hostname | `aiops-target` |
| host_ip | `192.168.1.12` |
| trigger_id | `99999` |
| message | `This is a test alert` |

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

### 4.2 飞书审批回调（长连接，无 HTTP 端点）

AIOps 通过 **lark-oapi 的 WebSocket 长连接**接收卡片按钮回调，**不需要**公网域名 / HTTPS / 反向代理。

启动时 `src/feishu_listener.py` 会用 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 主动连飞书，订阅 `card.action.trigger` 事件。审批人在飞书卡片上点"批准/拒绝"，飞书 push 回调到 AIOps，listener 解析后给 Temporal workflow 发 signal。

飞书开放平台配置（自建应用）：
1. 应用功能 → 机器人：开启
2. 权限管理：勾选 `im:message`、`im:message:send_as_bot`
3. 事件与回调 → 事件订阅：选 **"使用长连接接收事件"**（不要选 webhook）
4. 添加事件：`card.action.trigger`
5. 凭证页拿到 App ID / App Secret，填进 `.env`

> 没有 `/webhook/feishu` HTTP 端点，curl 也连不上，这是设计如此。

---

## 五、手动测试（curl）

在 VM1 或任何能访问 AIOps 的机器上执行。

> AIOps 的 `/webhook/zabbix` **强制要求 token**，curl 测试也必须带 `Authorization: Bearer <token>`（或 `X-Zabbix-Token: <token>`，两种都支持）。

```bash
# 先把 token 加载到环境变量，下面的 curl 复用它
export ZABBIX_WEBHOOK_TOKEN=$(grep ^ZABBIX_WEBHOOK_TOKEN /opt/aiops/.env | cut -d= -f2)
```

### 5.1 VM3 磁盘告警测试

```bash
curl -X POST http://localhost:8000/webhook/zabbix \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${ZABBIX_WEBHOOK_TOKEN}" \
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
  -H "Authorization: Bearer ${ZABBIX_WEBHOOK_TOKEN}" \
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
  -H "Authorization: Bearer ${ZABBIX_WEBHOOK_TOKEN}" \
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
  -H "Authorization: Bearer $(grep ^ZABBIX_WEBHOOK_TOKEN /opt/aiops/.env | cut -d= -f2)" \
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

### 飞书没有收到消息 / 点了按钮没反应

```bash
# 1. 检查 .env 应用机器人配置
cat /opt/aiops/.env | grep FEISHU

# 2. 看 listener 是否正常连上长连接
docker compose -f /opt/aiops/docker-compose.yml logs aiops --tail 50 | grep -i "lark\|feishu"
# 应该看到 "Lark WS listener thread started" 和 "connected to wss://..."

# 3. 手动测试 IM v1 发消息接口（验证 App ID/Secret + receive_id 有效）
APP_ID=$(grep ^FEISHU_APP_ID /opt/aiops/.env | cut -d= -f2)
APP_SECRET=$(grep ^FEISHU_APP_SECRET /opt/aiops/.env | cut -d= -f2)
RECEIVE_ID=$(grep ^FEISHU_RECEIVE_ID /opt/aiops/.env | cut -d= -f2)

# 拿 tenant_access_token
TOKEN=$(curl -s -X POST https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal \
  -H "Content-Type: application/json" \
  -d "{\"app_id\":\"${APP_ID}\",\"app_secret\":\"${APP_SECRET}\"}" | jq -r .tenant_access_token)

# 发一条文本
curl -X POST "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id" \
  -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
  -d "{\"receive_id\":\"${RECEIVE_ID}\",\"msg_type\":\"text\",\"content\":\"{\\\"text\\\":\\\"AIOps 测试\\\"}\"}"
# code=0 即成功
```

按钮点了没反应，重点查：
- 飞书开放平台 → 事件订阅是否选了**长连接**且订阅了 `card.action.trigger`
- AIOps 容器是否能出网到 `*.feishu.cn:443`
- 应用是否已发布版本（自建应用首次需要在"版本管理与发布"里发版本）

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

Zabbix 7.0 的 Webhook 宏在**文字版 / 数字版**之间容易踩坑：

| 宏 | 返回值 | 说明 |
|----|--------|------|
| `{EVENT.SEVERITY}` | 文字（`Disaster`/`High`/`Average`/`Warning`/`Information`/`Not classified`） | 不要做数值比较 |
| `{EVENT.NSEVERITY}` | 数字字符串 `0`~`5` | **推荐**用这个 |
| `{EVENT.STATUS}` | 文字（`PROBLEM` / `RESOLVED`） | 不要做数值比较 |
| `{EVENT.VALUE}` | 数字 `0`/`1`（0=OK/恢复, 1=PROBLEM） | **推荐**用这个 |
| `{EVENT.DATE}` | `YYYY.MM.DD`（点分隔！） | 不是 ISO 8601，拼出来 Pydantic 会解析失败 |
| `{EVENT.TIME}` | `HH:MM:SS` | 同上，单独用难拼标准时间 |
| `{EVENT.TIMESTAMP}` | Unix 秒（数字字符串） | **推荐**，在 JS 里 `new Date(x*1000).toISOString()` |
| `{EVENT.ID}` | 数字字符串 | 事件唯一 ID |
| `{HOST.HOST}` | Zabbix 配置的主机标识 | 给 Ansible 用 |
| `{HOST.IP}` | IP 字符串 | Agent 配置的 IP |
| `{EVENT.OPDATA}` | 文本 | 触发器附加数据，可能为空 |

### Message Templates

Zabbix 7.0 要求 Webhook Media Type 必须定义 Message Templates，否则不会触发。至少定义 `Message` 类型的模板。

### Webhook 测试

Zabbix 7.0 支持在 Web 界面直接测试 Webhook（Media types → Test），比发送真实告警更快捷。测试时需要手动将宏替换为示例值。

---

## 十二、Ubuntu 监控项配置（VM3 aiops-target）

针对 [docs/scripts/](scripts/) 下的故障注入脚本，每条都对应一个监控项 + 触发器。

### 12.1 链接基础模板

Zabbix Web → Configuration → Hosts → `aiops-target` → Templates 标签页：

| 模板 | 用途 |
|---|---|
| `Linux by Zabbix agent` | CPU / 内存 / 磁盘 LLD / 网络 / 进程 等基础指标 |

> **不要**同时 link `Linux by Zabbix agent active` —— 两个模板 item key 完全相同，会冲突。被动模式（默认）就够了。

### 12.2 自定义监控项

模板里没有的端口探活，要手动加。Configuration → Hosts → `aiops-target` → Items → Create item：

| Name | Key | Type | Update interval | Value type | 用途 |
|---|---|---|---|---|---|
| nginx port 80 | `net.tcp.service[http,,80]` | Zabbix agent | 1m | Numeric (unsigned) | stop-nginx.sh 监控 |
| redis port 6379 | `net.tcp.service[tcp,,6379]` | Zabbix agent | 1m | Numeric (unsigned) | stop-redis.sh 监控 |

> `net.tcp.service` 返回 1=端口可用 / 0=不可用。Zabbix agent 自身就能跑，不需要在 VM3 上装额外采集器。
>
> `/tmp` 磁盘使用率走的是 `Linux by Zabbix agent` 模板的 LLD（`vfs.fs.dependent.size[{#FSNAME},pused]`），首次发现需要 1 小时（默认 LLD 间隔）。可以在 Discovery rules 里把 `Mounted filesystem discovery` 的 interval 改成 5m 加速。

### 12.3 触发器

Configuration → Hosts → `aiops-target` → Triggers → Create trigger：

| Trigger name | Severity | Expression | 对应脚本 | 对应 Runbook |
|---|---|---|---|---|
| `Disk usage > 90% on /tmp` | High | `last(/aiops-target/vfs.fs.size[/tmp,pused])>90` | fill-disk.sh | `disk_cleanup` ✅ |
| `nginx is down on aiops-target` | High | `last(/aiops-target/net.tcp.service[http,,80])=0` | stop-nginx.sh | `service_restart` ✅ |
| `redis is down on aiops-target` | High | `last(/aiops-target/net.tcp.service[tcp,,6379])=0` | stop-redis.sh | `service_restart` ✅ |
| `CPU load > 80% on aiops-target` | Average | `avg(/aiops-target/system.cpu.load[all,avg5],3m)>{$CPU_LOAD_THRESH}` | stress-cpu.sh | 人工 |
| `Memory usage > 90% on aiops-target` | Average | `last(/aiops-target/vm.memory.utilization)>90` | stress-memory.sh | 人工 |

> **触发器命名约定**：name 直接进 LLM prompt 用作根因匹配。让名字里包含 `disk` / `nginx` / `redis` / `cpu` / `memory` 这种关键词，LLM 推荐 Runbook 时会更准。
>
> **触发器表达式**：磁盘和服务用 `last(...)` 单点判断（恢复快）；CPU 用 `avg(...,3m)` 避免瞬时抖动；内存用 `last(...)` （内存紧张通常持续）。
>
> **CPU_LOAD_THRESH 宏**：建议在 host macros 里设 `{$CPU_LOAD_THRESH} = 4`（约等于 4 核机器 80%），方便不同 VM 单独调阈值。

### 12.4 Action 关联

Configuration → Actions → Trigger actions → `Send to AIOps`（在 §3.1 创建过）：

把 condition 改成只匹配上面的 5 个触发器（避免把模板里几百个其他 trigger 都灌给 AIOps）：

| Condition | Operator | Value |
|---|---|---|
| Trigger | equals | `Disk usage > 90% on /tmp` |
| Trigger | equals | `nginx is down on aiops-target` |
| Trigger | equals | `redis is down on aiops-target` |
| Trigger | equals | `CPU load > 80% on aiops-target` |
| Trigger | equals | `Memory usage > 90% on aiops-target` |

把 Operator type 改成 `OR`（任意一个匹配就发）。

> 这一步避免 Zabbix 把 `Linux by Zabbix agent` 模板里 200+ 个 trigger 全部推给 AIOps，省 LLM token。

---

## 十三、Windows AD 监控项配置（VM4 aiops-windc）

VM4 是域控（NTDS / DNS / Netlogon / KDC），**只通知不自动修复**。

### 13.1 链接模板

Configuration → Hosts → `aiops-windc` → Templates：

| 模板 | 用途 |
|---|---|
| `Windows by Zabbix agent` | CPU / 内存 / 磁盘 / 网络 等基础（被动模式） |
| `Active Directory by Zabbix agent` | LDAP 速率 / DRA 复制 / KDC 认证速率 等 AD 性能指标 |

> **注意**：`Active Directory by Zabbix agent` 模板**不**直接监控 NTDS / DNS / Netlogon 服务的启停，它只看性能计数器。要监控服务本身的 Running/Stopped 状态，得手动加 `service.info` item（见下文）。

### 13.2 自定义监控项（服务状态）

Configuration → Hosts → `aiops-windc` → Items → Create item：

| Name | Key | Type | Interval | Value type |
|---|---|---|---|---|
| AD Domain Services state | `service.info[NTDS,state]` | Zabbix agent | 1m | Numeric (unsigned) |
| DNS Server state | `service.info[DNS,state]` | Zabbix agent | 1m | Numeric (unsigned) |
| Netlogon state | `service.info[Netlogon,state]` | Zabbix agent | 1m | Numeric (unsigned) |
| Kerberos KDC state | `service.info[Kdc,state]` | Zabbix agent | 1m | Numeric (unsigned) |

**`service.info[<svc>,state]` 返回值**（Zabbix Agent 2）：

| 值 | 含义 |
|---|---|
| 0 | Running |
| 1 | Paused |
| 2 | Start pending |
| 3 | Pause pending |
| 4 | Continue pending |
| 5 | Stop pending |
| 6 | Stopped |
| 7 | Unknown |
| 255 | No such service |

### 13.3 触发器

| Trigger name | Severity | Expression | 对应脚本 |
|---|---|---|---|
| `AD Domain Controller (NTDS) is down on aiops-windc` | Disaster | `last(/aiops-windc/service.info[NTDS,state])<>0` | stop-services.ps1 -Service AD |
| `DNS Server is down on aiops-windc` | High | `last(/aiops-windc/service.info[DNS,state])<>0` | stop-services.ps1 -Service DNS |
| `Netlogon is down on aiops-windc` | High | `last(/aiops-windc/service.info[Netlogon,state])<>0` | （手动停 Netlogon 测试） |
| `Kerberos KDC is down on aiops-windc` | High | `last(/aiops-windc/service.info[Kdc,state])<>0` | （手动停 Kdc 测试） |

> 用 `<>0`（不等于 Running）覆盖 Stopped / Paused / Unknown 等所有非健康状态，比 `=6`（仅 Stopped）更稳。
>
> NTDS 是 Disaster 级别，因为它一倒整个域认证全瘫；DNS / Netlogon / KDC 单挂还不至于。

### 13.4 Action 关联

Configuration → Actions → Trigger actions：建议**单独建一个 Action** 而不是混进 §3.1 的 `Send to AIOps`，叫 `Notify AD Issues`：

- Conditions：上面 4 个触发器 OR
- Operations：发 `AIOps Webhook`（同样推到 AIOps，让 AIOps 推飞书告知运维介入；workflow 里因为没有匹配的 Runbook，会走 `unsupported` 分支只通知不执行）

---

## 十四、Windows DHCP 监控项配置（VM4 aiops-windc）

Zabbix 7.0 没有官方 DHCP Server 模板，要手动加。

### 14.1 监控项

Configuration → Hosts → `aiops-windc` → Items → Create item：

| Name | Key | Type | Interval | Value type | 用途 |
|---|---|---|---|---|---|
| DHCP Server state | `service.info[DHCPServer,state]` | Zabbix agent | 1m | Numeric (unsigned) | 服务启停 |
| DHCP Discovers/sec | `perf_counter_en["\DHCP Server\Discovers/sec"]` | Zabbix agent | 1m | Numeric (float) | 客户端寻址请求速率 |
| DHCP Acks/sec | `perf_counter_en["\DHCP Server\Acks/sec"]` | Zabbix agent | 1m | Numeric (float) | 成功分配速率 |
| DHCP Naks/sec | `perf_counter_en["\DHCP Server\Naks/sec"]` | Zabbix agent | 1m | Numeric (float) | 拒绝速率，**激增=IP 池可能快空** |

> 第二、三、四个 item 是进阶监控，只关心服务启停可以只加第一个。
>
> `perf_counter_en` 走的是英文计数器名，避免在中文 Windows 上 perfcounter 名翻译导致取不到值。中文系统也支持 `_en` 变体。

### 14.2 触发器

| Trigger name | Severity | Expression | 对应脚本 |
|---|---|---|---|
| `DHCP Server is down on aiops-windc` | High | `last(/aiops-windc/service.info[DHCPServer,state])<>0` | stop-services.ps1 -Service DHCP |
| `DHCP Naks/sec spike on aiops-windc` | Average | `avg(/aiops-windc/perf_counter_en["\DHCP Server\Naks/sec"],5m)>1` | （IP 池将耗尽，无脚本模拟） |

### 14.3 Action 关联

可以把 `DHCP Server is down` 加到 §13.4 的 `Notify AD Issues` Action 里，AD/DHCP/DNS 共用一个通知通道。

---

## 十五、监控项一览（速查）

VM3 (aiops-target) — 5 个触发器，3 个走 AIOps 自动修复：

| 触发器 | Runbook | 故障脚本 |
|---|---|---|
| Disk usage > 90% on /tmp | `disk_cleanup` ✅ | `fill-disk.sh` |
| nginx is down | `service_restart` ✅ | `stop-nginx.sh` |
| redis is down | `service_restart` ✅ | `stop-redis.sh` |
| CPU load > 80% | 人工 | `stress-cpu.sh` |
| Memory > 90% | 人工 | `stress-memory.sh` |

VM4 (aiops-windc) — 5 个触发器，全部仅通知：

| 触发器 | 处理 | 故障脚本 |
|---|---|---|
| AD (NTDS) down | 人工 | `stop-services.ps1 -Service AD` |
| DNS down | 人工 | `stop-services.ps1 -Service DNS` |
| Netlogon down | 人工 | 手动 `Stop-Service Netlogon` |
| KDC down | 人工 | 手动 `Stop-Service Kdc` |
| DHCP Server down | 人工 | `stop-services.ps1 -Service DHCP` |

---

_文档版本: v1.2 | 2026-05 | Zabbix 7.0 LTS + Ubuntu 24.04 + Windows Server 2025 + 飞书长连接_
