# Phase 1: 主链路无 AI 设计文档

> 告警 → 飞书 → 人工确认 → Runbook 执行

## 1. 概述

### 1.1 目标

实现 AIOps 主链路，完全不涉及 LLM：

```
Zabbix Webhook → FastAPI → Redis Stream → Temporal Workflow
    → 飞书告警卡片 → 人工审批 → Ansible 执行 Runbook → 结果回写
```

### 1.2 约束

- Python 3.14，本地 Windows 开发，部署到 VM1 (aiops-core)
- 全 Docker 化部署（docker-compose）
- 集群 4 台 VM 就绪，Ansible 免密已配置
- 飞书自定义机器人 Webhook 已申请
- 2 个示例 Runbook：disk_cleanup + service_restart

---

## 2. 项目结构

```
aiops/
├── pyproject.toml
├── docker-compose.yml
├── Dockerfile
├── .env.example
├── src/
│   ├── __init__.py
│   ├── main.py             # FastAPI 入口 + Temporal Worker 启动
│   ├── config.py           # 环境变量配置
│   ├── models.py           # Pydantic 数据模型
│   ├── api/
│   │   ├── __init__.py
│   │   └── webhook.py      # /webhook/zabbix + /webhook/feishu
│   ├── bus/
│   │   ├── __init__.py
│   │   ├── producer.py     # Redis Stream 写入
│   │   └── consumer.py     # Redis Stream 消费 → 触发 Workflow
│   ├── workflows/
│   │   ├── __init__.py
│   │   └── alert_workflow.py  # Temporal Workflow 定义
│   ├── activities/
│   │   ├── __init__.py
│   │   ├── feishu.py       # 飞书通知 + 审批回调处理
│   │   ├── runbook.py      # Runbook 执行调度
│   │   └── audit.py        # 审计日志写入
│   └── runbooks/
│       ├── __init__.py     # RUNBOOK_REGISTRY 注册表
│       ├── base.py         # BaseRunbook 抽象基类
│       ├── disk_cleanup.py
│       └── service_restart.py
├── ansible/
│   ├── inventory.ini       # VM2/VM3 连接信息
│   ├── disk_cleanup.yml
│   └── service_restart.yml
├── tests/
│   ├── conftest.py
│   ├── test_webhook.py
│   ├── test_workflow.py
│   └── test_runbooks.py
└── docs/
```

---

## 3. 数据模型

### 3.1 核心模型（src/models.py）

```python
from datetime import datetime
from pydantic import BaseModel

class Alert(BaseModel):
    """Zabbix Webhook 推送的告警"""
    event_id: str              # {EVENT.ID} - 唯一标识，用于幂等去重
    event_name: str            # {EVENT.NAME} - trigger 名称
    severity: str              # {EVENT.SEVERITY} - disaster/high/average/warning/info
    hostname: str              # {HOST.NAME}
    host_ip: str               # {HOST.IP}
    trigger_id: str            # {TRIGGER.ID}
    message: str               # {EVENT.MESSAGE}
    timestamp: datetime        # {EVENT.DATE} + {EVENT.TIME}
    status: str                # "problem" | "recovery"

class RunbookResult(BaseModel):
    """单步执行结果"""
    success: bool
    stdout: str
    stderr: str
    duration_sec: float

class ExecutionResult(BaseModel):
    """完整执行结果"""
    dry_run: RunbookResult
    execute: RunbookResult
    verify: bool
    snapshot: dict             # 执行前快照
    rolled_back: bool = False

class AuditRecord(BaseModel):
    """全链路审计记录"""
    alert: Alert
    workflow_id: str
    decision: str              # approved / rejected / timeout
    runbook_id: str | None
    runbook_params: dict | None
    execution_result: ExecutionResult | None
    feishu_message_id: str | None
    created_at: datetime
    completed_at: datetime | None
```

### 3.2 Runbook 基类（src/runbooks/base.py）

```python
from abc import ABC, abstractmethod
from pydantic import BaseModel

class BaseRunbook(ABC):
    """每个 Runbook 必须实现五要素"""

    @abstractmethod
    def params_schema(self) -> type[BaseModel]:
        """参数 Schema"""

    @abstractmethod
    def dry_run(self, params: BaseModel) -> RunbookResult:
        """仿真执行，不产生副作用"""

    @abstractmethod
    def execute(self, params: BaseModel) -> ExecutionResult:
        """实际执行，含快照"""

    @abstractmethod
    def rollback(self, snapshot: dict) -> bool:
        """回滚到快照状态"""

    @abstractmethod
    def verify(self, params: BaseModel) -> bool:
        """反向验证：目标是否恢复健康"""
```

---

## 4. 基础设施

### 4.1 docker-compose 服务

| 服务 | 镜像 | 端口 | 用途 |
|---|---|---|---|
| postgres | postgres:16 | 5432 | Temporal 后端 + 审计存储 |
| temporal | temporalio/auto-setup:1.72 | 7233 | Temporal Server |
| temporal-ui | temporalio/ui:2.31 | 8080 | Temporal Web UI |
| redis | redis:7-alpine | 6379 | Event Bus (Streams) |
| aiops | 本地构建 | 8000 | FastAPI + Temporal Worker |

### 4.2 关键决策

- Temporal auto-setup 镜像首次启动自动创建 default namespace
- FastAPI 和 Temporal Worker 同一容器（Phase 1 流量不大）
- Redis Stream consumer group 保证每条告警只处理一次
- 凭证通过 `.env` 注入

### 4.3 Redis Stream 结构

```
Stream: aiops:alerts
Consumer Group: aiops-workers
消息格式: Alert JSON
```

### 4.4 Zabbix 对接

Zabbix Media Type → Webhook URL: `http://VM1_IP:8000/webhook/zabbix`

---

## 5. Temporal Workflow

### 5.1 主流程

```
Workflow(id=event_id)
  → Activity: send_feishu_alert(alert)         # 推飞书卡片
  → Activity: wait_feishu_approval(30min超时)   # 等待审批 signal
  → 分支:
      [approved]
        → Activity: execute_runbook(runbook_id, params)  # dry-run → execute → verify
        → Activity: write_audit(record)
        → Activity: send_feishu_result(result)
      [rejected]
        → Activity: write_audit(record, decision="rejected")
        → Activity: send_feishu_rejected()
      [timeout]
        → Activity: write_audit(record, decision="timeout")
        → Activity: send_feishu_timeout()
```

### 5.2 审批机制

- 飞书卡片按钮携带 `{workflow_id, action, alert_id}`
- `/webhook/feishu` 收到回调后调 `temporal_client.signal_workflow(workflow_id, "approval", decision)`
- Workflow 用 `wait_condition` 等待 signal，超时 30 分钟

---

## 6. 飞书集成

### 6.1 推送告警卡片

- 用 `httpx` 异步 POST 飞书 Webhook API
- 卡片为 Interactive Card JSON，含告警信息 + 操作按钮
- 按钮回调数据：`{"workflow_id": "xxx", "action": "approve/reject", "alert_id": "xxx"}`

### 6.2 接收审批回调

- FastAPI `/webhook/feishu` 端点
- 验证飞书签名（可选）
- 解析按钮 action → signal Temporal Workflow

### 6.3 配置

```
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxx
FEISHU_WEBHOOK_SECRET=xxx
```

---

## 7. Runbook 执行

### 7.1 执行方式

通过 `ansible-runner` Python 库调 Ansible Playbook。

### 7.2 disk_cleanup

```yaml
# ansible/disk_cleanup.yml
# 参数：target_host, path=/tmp, min_age_days=7
# dry-run: find 找出将删除的文件列表
# execute: 删除过期文件
# verify: 检查磁盘使用率 < 80%
# rollback: 无法回滚，通知人工
```

### 7.3 service_restart

```yaml
# ansible/service_restart.yml
# 参数：target_host, service_name
# dry-run: 检查服务当前状态
# execute: systemctl restart
# verify: 检查服务状态为 active
# rollback: 通知人工（无版本化回滚）
```

### 7.4 注册表

```python
RUNBOOK_REGISTRY = {
    "disk_cleanup": DiskCleanupRunbook,
    "service_restart": ServiceRestartRunbook,
}
```

---

## 8. 错误处理

| 场景 | 处理 |
|---|---|
| 重复 Webhook（同一 event_id） | Redis SETNX 去重 |
| Redis 写入失败 | 返回 500，Zabbix 重试 |
| Activity 超时 | retry 3 次，后标记失败 |
| 飞书 API 失败 | retry 3 次 |
| 审批超时 30min | 标记 timeout，飞书通知 |
| Ansible 执行失败 | 记录 stderr，尝试 rollback |
| 反向验证不通过 | auto rollback + 飞书通知人工介入 |

---

## 9. 测试

### 9.1 核心链路测试

1. **test_webhook.py**：模拟 Zabbix POST → 验证 Alert 解析 + Redis Stream 写入
2. **test_workflow.py**：mock 飞书 + Ansible → 验证审批分支逻辑
3. **test_runbooks.py**：Ansible `--check` 模式 → 验证 Playbook 语法

### 9.2 部署验证

手动流程：`docker compose build && docker compose up -d`

---

## 10. 不做的事（Phase 1 范围外）

- LLM 推理（Phase 2）
- Alert Correlator 告警关联（Phase 4）
- Policy/OPA 权限控制（Phase 3）
- Hermes 知识层（Phase 7）
- CI/CD 自动部署
- 监控自监控（Phase 3）
