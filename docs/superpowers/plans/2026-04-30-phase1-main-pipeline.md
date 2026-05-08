# Phase 1: 主链路无 AI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 AIOps 主链路：Zabbix 告警 → 飞书通知 → 人工审批 → Ansible 执行 Runbook → 结果回写

**Architecture:** FastAPI 接收 Zabbix Webhook，写入 Redis Stream，Consumer 触发 Temporal Workflow。Workflow 编排飞书通知、审批等待、Runbook 执行。全 Docker 部署到 VM1。

**Tech Stack:** Python 3.14, FastAPI, Temporal Python SDK, Redis Streams, ansible-runner, httpx, Pydantic, pytest

**Spec:** `docs/superpowers/specs/2026-04-30-phase1-main-pipeline-design.md`

---

## File Map

| File                              | Responsibility                                     |
| --------------------------------- | -------------------------------------------------- |
| `pyproject.toml`                  | 依赖、ruff/mypy 配置                               |
| `Dockerfile`                      | App 镜像（FastAPI + Worker）                       |
| `docker-compose.yml`              | PostgreSQL + Temporal + Redis + App                |
| `.env.example`                    | 环境变量模板                                       |
| `src/__init__.py`                 | 包标识                                             |
| `src/config.py`                   | 环境变量读取（pydantic-settings）                  |
| `src/models.py`                   | Alert, RunbookResult, ExecutionResult, AuditRecord |
| `src/main.py`                     | FastAPI app + Temporal Worker 启动                 |
| `src/api/__init__.py`             | 包标识                                             |
| `src/api/webhook.py`              | `/webhook/zabbix` + `/webhook/feishu`              |
| `src/bus/__init__.py`             | 包标识                                             |
| `src/bus/producer.py`             | Redis Stream 写入（XADD）                          |
| `src/bus/consumer.py`             | Redis Stream 消费（XREADGROUP）→ 触发 Workflow     |
| `src/workflows/__init__.py`       | 包标识                                             |
| `src/workflows/alert_workflow.py` | Temporal Workflow 定义                             |
| `src/activities/__init__.py`      | 包标识                                             |
| `src/activities/feishu.py`        | 飞书卡片推送 + 审批回调处理                        |
| `src/activities/runbook.py`       | Runbook 执行调度                                   |
| `src/activities/audit.py`         | 审计日志写入                                       |
| `src/runbooks/__init__.py`        | RUNBOOK_REGISTRY                                   |
| `src/runbooks/base.py`            | BaseRunbook 抽象基类                               |
| `src/runbooks/disk_cleanup.py`    | 磁盘清理 Runbook                                   |
| `src/runbooks/service_restart.py` | 服务重启 Runbook                                   |
| `ansible/inventory.ini`           | VM2/VM3 主机清单                                   |
| `ansible/disk_cleanup.yml`        | 磁盘清理 Playbook                                  |
| `ansible/service_restart.yml`     | 服务重启 Playbook                                  |
| `tests/conftest.py`               | 共享 fixtures                                      |
| `tests/test_models.py`            | 数据模型测试                                       |
| `tests/test_webhook.py`           | Webhook endpoint 测试                              |
| `tests/test_bus.py`               | Redis Stream producer/consumer 测试                |
| `tests/test_runbooks.py`          | Runbook 逻辑测试                                   |
| `tests/test_workflow.py`          | Temporal Workflow 测试                             |

---

## Task 1: 项目脚手架 + 依赖

**Files:**
- Create: `pyproject.toml`
- Create: `src/__init__.py`
- Create: `.env.example`
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Delete: `main.py`（旧 placeholder）

- [ ] **Step 1: 更新 pyproject.toml**

```toml
[project]
name = "aiops"
version = "0.1.0"
description = "AIOps Phase 1: 告警→飞书→审批→Runbook执行"
readme = "README.md"
requires-python = ">=3.14"
dependencies = [
    "fastapi>=0.136.1",
    "uvicorn[standard]>=0.46",
    "redis[hiredis]>=7.4",
    "temporalio>=1.26.0",
    "httpx>=0.28.1",
    "pydantic>=2.13.3",
    "pydantic-settings>=2.14",
    "ansible-runner>=2.4.3",
]

[project.optional-dependencies]
dev = [
    "pytest>=9.0.3",
    "pytest-asyncio>=1.3.0",
    "pytest-cov>=7.1.0",
    "ruff>=0.15.12",
    "mypy>=1.20.2",
    "respx>=0.23.1",
]

[tool.ruff]
line-length = 120
target-version = "py314"

[tool.ruff.lint]
select = ["E", "W", "F", "I", "B", "C4", "UP", "SIM"]
ignore = ["E501"]

[tool.ruff.format]
quote-style = "double"
indent-style = "space"

[tool.mypy]
python_version = "3.14"
strict = true
warn_return_any = true
warn_unused_ignores = true
disallow_untyped_defs = true
disallow_incomplete_defs = true

[[tool.mypy.overrides]]
module = "tests.*"
disallow_untyped_defs = false

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: 创建 src/__init__.py**

```python
```

（空文件）

- [ ] **Step 3: 创建 .env.example**

```env
# Redis
REDIS_URL=redis://redis:6379/0

# Temporal
TEMPORAL_ADDRESS=temporal:7233
TEMPORAL_TASK_QUEUE=aiops-alerts

# Feishu
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/your-token-here
FEISHU_WEBHOOK_SECRET=

# Ansible
ANSIBLE_PRIVATE_DATA_DIR=/app/ansible
ANSIBLE_INVENTORY=/app/ansible/inventory.ini
```

- [ ] **Step 4: 创建 Dockerfile**

```dockerfile
FROM python:3.14-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY . .

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 5: 创建 docker-compose.yml**

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: temporal
      POSTGRES_PASSWORD: temporal
      POSTGRES_DB: temporal
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U temporal"]
      interval: 5s
      timeout: 5s
      retries: 5

  temporal:
    image: temporalio/auto-setup:1.72
    environment:
      DB: postgresql
      DB_PORT: 5432
      POSTGRES_USER: temporal
      POSTGRES_PWD: temporal
      POSTGRES_SEEDS: postgres
      TEMPORAL_ADDRESS: temporal:7233
    ports:
      - "7233:7233"
    depends_on:
      postgres:
        condition: service_healthy

  temporal-ui:
    image: temporalio/ui:2.31
    environment:
      TEMPORAL_ADDRESS: temporal:7233
    ports:
      - "8080:8080"
    depends_on:
      - temporal

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redisdata:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5

  aiops:
    build: .
    ports:
      - "8000:8000"
    env_file:
      - .env
    depends_on:
      temporal:
        condition: service_started
      redis:
        condition: service_healthy
    volumes:
      - ./src:/app/src
      - ./ansible:/app/ansible

volumes:
  pgdata:
  redisdata:
```

- [ ] **Step 6: 删除旧 main.py 并安装依赖**

```bash
rm main.py
cd /d/project/aiops
.venv/Scripts/pip install -e ".[dev]"
```

- [ ] **Step 7: 验证 ruff 配置**

```bash
.venv/Scripts/ruff check src/
.venv/Scripts/ruff format --check src/
```

Expected: 无错误（src 目录还没有代码）

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml src/__init__.py .env.example Dockerfile docker-compose.yml
git rm main.py
git commit -m "feat: project scaffolding with docker-compose, Dockerfile, and dependencies"
```

---

## Task 2: 配置 + 数据模型

**Files:**
- Create: `src/config.py`
- Create: `src/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: 写 models 测试**

```python
# tests/test_models.py
from datetime import datetime

from src.models import Alert, AuditRecord, ExecutionResult, RunbookResult


class TestAlert:
    def test_create_from_dict(self) -> None:
        alert = Alert(
            event_id="12345",
            event_name="Disk usage > 90%",
            severity="high",
            hostname="web-server-01",
            host_ip="192.168.1.13",
            trigger_id="10001",
            message="Disk usage is 95% on /tmp",
            timestamp=datetime(2026, 4, 30, 14, 30, 0),
            status="problem",
        )
        assert alert.event_id == "12345"
        assert alert.severity == "high"
        assert alert.status == "problem"

    def test_roundtrip_json(self) -> None:
        alert = Alert(
            event_id="12345",
            event_name="test",
            severity="high",
            hostname="host1",
            host_ip="10.0.0.1",
            trigger_id="100",
            message="msg",
            timestamp=datetime(2026, 1, 1),
            status="problem",
        )
        data = alert.model_dump_json()
        restored = Alert.model_validate_json(data)
        assert restored == alert


class TestRunbookResult:
    def test_success(self) -> None:
        result = RunbookResult(success=True, stdout="ok", stderr="", duration_sec=1.5)
        assert result.success
        assert result.duration_sec == 1.5


class TestExecutionResult:
    def test_full_result(self) -> None:
        dry = RunbookResult(success=True, stdout="dry", stderr="", duration_sec=0.5)
        exec_ = RunbookResult(success=True, stdout="done", stderr="", duration_sec=2.0)
        result = ExecutionResult(dry_run=dry, execute=exec_, verify=True, snapshot={"disk": "80%"})
        assert result.verify
        assert result.rolled_back is False


class TestAuditRecord:
    def test_create(self) -> None:
        alert = Alert(
            event_id="1",
            event_name="test",
            severity="low",
            hostname="h",
            host_ip="1.1.1.1",
            trigger_id="1",
            message="m",
            timestamp=datetime(2026, 1, 1),
            status="problem",
        )
        record = AuditRecord(
            alert=alert,
            workflow_id="wf-1",
            decision="approved",
            runbook_id="disk_cleanup",
            runbook_params={"path": "/tmp"},
            execution_result=None,
            feishu_message_id="msg-1",
            created_at=datetime(2026, 1, 1),
            completed_at=None,
        )
        assert record.decision == "approved"
        assert record.execution_result is None
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/Scripts/python -m pytest tests/test_models.py -v
```

Expected: FAIL（ModuleNotFoundError: src.models）

- [ ] **Step 3: 写 config.py**

```python
# src/config.py
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """从环境变量读取配置"""

    redis_url: str = "redis://localhost:6379/0"
    temporal_address: str = "localhost:7233"
    temporal_task_queue: str = "aiops-alerts"
    feishu_webhook_url: str = ""
    feishu_webhook_secret: str = ""
    ansible_private_data_dir: str = "./ansible"
    ansible_inventory: str = "./ansible/inventory.ini"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
```

- [ ] **Step 4: 写 models.py**

```python
# src/models.py
from datetime import datetime

from pydantic import BaseModel


class Alert(BaseModel):
    """Zabbix Webhook 推送的告警"""

    event_id: str
    event_name: str
    severity: str
    hostname: str
    host_ip: str
    trigger_id: str
    message: str
    timestamp: datetime
    status: str  # "problem" | "recovery"


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
    snapshot: dict
    rolled_back: bool = False


class AuditRecord(BaseModel):
    """全链路审计记录"""

    alert: Alert
    workflow_id: str
    decision: str  # approved / rejected / timeout
    runbook_id: str | None
    runbook_params: dict | None
    execution_result: ExecutionResult | None
    feishu_message_id: str | None
    created_at: datetime
    completed_at: datetime | None
```

- [ ] **Step 5: 跑测试确认通过**

```bash
.venv/Scripts/python -m pytest tests/test_models.py -v
```

Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add src/config.py src/models.py tests/test_models.py
git commit -m "feat: add config and data models (Alert, RunbookResult, ExecutionResult, AuditRecord)"
```

---

## Task 3: Redis Stream Bus

**Files:**
- Create: `src/bus/__init__.py`
- Create: `src/bus/producer.py`
- Create: `src/bus/consumer.py`
- Create: `tests/test_bus.py`

- [ ] **Step 1: 写 bus 测试**

```python
# tests/test_bus.py
import json

import pytest
import redis.asyncio as aioredis

from src.bus.consumer import consume_alert
from src.bus.producer import produce_alert
from src.models import Alert
from datetime import datetime


@pytest.fixture
def alert() -> Alert:
    return Alert(
        event_id="evt-001",
        event_name="Disk full",
        severity="high",
        hostname="web-01",
        host_ip="10.0.0.1",
        trigger_id="100",
        message="Disk usage 95%",
        timestamp=datetime(2026, 1, 1, 12, 0, 0),
        status="problem",
    )


@pytest.fixture
async def redis_client():
    client = aioredis.from_url("redis://localhost:6379/0")
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.mark.asyncio
async def test_produce_and_consume(alert: Alert, redis_client: aioredis.Redis) -> None:
    # Produce
    msg_id = await produce_alert(redis_client, alert)
    assert msg_id is not None

    # Create consumer group
    try:
        await redis_client.xgroup_create("aiops:alerts", "aiops-workers", id="0", mkstream=True)
    except aioredis.exceptions.ResponseError:
        pass  # group already exists

    # Consume
    result = await consume_alert(redis_client, "aiops-workers", "test-consumer")
    assert result is not None
    consumed_alert, _ = result
    assert consumed_alert.event_id == "evt-001"
    assert consumed_alert.hostname == "web-01"


@pytest.mark.asyncio
async def test_produce_duplicate_rejected(alert: Alert, redis_client: aioredis.Redis) -> None:
    """同一 event_id 第二次写入应被拒绝（幂等去重）"""
    msg_id1 = await produce_alert(redis_client, alert)
    assert msg_id1 is not None

    msg_id2 = await produce_alert(redis_client, alert)
    assert msg_id2 is None  # 重复，返回 None


@pytest.mark.asyncio
async def test_consume_empty(redis_client: aioredis.Redis) -> None:
    try:
        await redis_client.xgroup_create("aiops:alerts", "aiops-workers", id="0", mkstream=True)
    except aioredis.exceptions.ResponseError:
        pass

    result = await consume_alert(redis_client, "aiops-workers", "test-consumer", block_ms=100)
    assert result is None
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/Scripts/python -m pytest tests/test_bus.py -v
```

Expected: FAIL

- [ ] **Step 3: 创建 src/bus/__init__.py**

```python
```

- [ ] **Step 4: 写 producer.py**

```python
# src/bus/producer.py
import json

import redis.asyncio as aioredis

from src.models import Alert

STREAM_KEY = "aiops:alerts"


async def produce_alert(client: aioredis.Redis, alert: Alert) -> str | None:
    """写入 Redis Stream，同一 event_id 去重。返回消息 ID 或 None（重复）"""

    dedup_key = f"aiops:dedup:{alert.event_id}"
    # SETNX：只有第一次写入成功
    is_new = await client.set(dedup_key, "1", nx=True, ex=3600)  # 1h TTL
    if not is_new:
        return None

    data = alert.model_dump_json()
    msg_id = await client.xadd(STREAM_KEY, {"data": data})
    return msg_id
```

- [ ] **Step 5: 写 consumer.py**

```python
# src/bus/consumer.py
import json

import redis.asyncio as aioredis

from src.models import Alert

STREAM_KEY = "aiops:alerts"


async def consume_alert(
    client: aioredis.Redis,
    group: str,
    consumer: str,
    block_ms: int = 5000,
) -> tuple[Alert, str] | None:
    """从 Redis Stream 消费一条告警。返回 (Alert, message_id) 或 None"""

    results = await client.xreadgroup(
        groupname=group,
        consumername=consumer,
        streams={STREAM_KEY: ">"},
        count=1,
        block=block_ms,
    )

    if not results:
        return None

    _stream, messages = results[0]
    msg_id, fields = messages[0]
    raw = fields[b"data"].decode("utf-8")
    alert = Alert.model_validate_json(raw)

    # ACK 消息
    await client.xack(STREAM_KEY, group, msg_id)

    return alert, msg_id
```

- [ ] **Step 6: 跑测试确认通过**

```bash
.venv/Scripts/python -m pytest tests/test_bus.py -v
```

Expected: 全部 PASS（需要本地 Redis 运行）

- [ ] **Step 7: Commit**

```bash
git add src/bus/ tests/test_bus.py
git commit -m "feat: Redis Stream bus with producer (XADD + dedup) and consumer (XREADGROUP)"
```

---

## Task 4: Runbook 基类 + Ansible Playbooks

**Files:**
- Create: `src/runbooks/__init__.py`
- Create: `src/runbooks/base.py`
- Create: `ansible/inventory.ini`
- Create: `ansible/disk_cleanup.yml`
- Create: `ansible/service_restart.yml`
- Create: `tests/test_runbooks.py`

- [ ] **Step 1: 写 Runbook 测试**

```python
# tests/test_runbooks.py
from unittest.mock import patch, MagicMock

import pytest

from src.models import RunbookResult
from src.runbooks.base import BaseRunbook
from src.runbooks.disk_cleanup import DiskCleanupParams, DiskCleanupRunbook
from src.runbooks.service_restart import ServiceRestartParams, ServiceRestartRunbook


class TestDiskCleanupRunbook:
    def test_params_schema(self) -> None:
        rb = DiskCleanupRunbook()
        schema = rb.params_schema()
        assert schema is DiskCleanupParams

    def test_params_defaults(self) -> None:
        params = DiskCleanupParams(target_host="10.0.0.1")
        assert params.path == "/tmp"
        assert params.min_age_days == 7

    @patch("src.runbooks.disk_cleanup.run_ansible")
    def test_dry_run(self, mock_run: MagicMock) -> None:
        mock_run.return_value = RunbookResult(
            success=True, stdout="would delete 5 files", stderr="", duration_sec=1.0
        )
        rb = DiskCleanupRunbook()
        params = DiskCleanupParams(target_host="10.0.0.1")
        result = rb.dry_run(params)
        assert result.success
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args.kwargs["check"] is True  # dry-run mode

    @patch("src.runbooks.disk_cleanup.run_ansible")
    def test_verify(self, mock_run: MagicMock) -> None:
        mock_run.return_value = RunbookResult(
            success=True, stdout="disk_usage=45", stderr="", duration_sec=0.5
        )
        rb = DiskCleanupRunbook()
        params = DiskCleanupParams(target_host="10.0.0.1")
        assert rb.verify(params) is True


class TestServiceRestartRunbook:
    def test_params_schema(self) -> None:
        rb = ServiceRestartRunbook()
        schema = rb.params_schema()
        assert schema is ServiceRestartParams

    @patch("src.runbooks.service_restart.run_ansible")
    def test_dry_run(self, mock_run: MagicMock) -> None:
        mock_run.return_value = RunbookResult(
            success=True, stdout="service nginx is active", stderr="", duration_sec=0.5
        )
        rb = ServiceRestartRunbook()
        params = ServiceRestartParams(target_host="10.0.0.1", service_name="nginx")
        result = rb.dry_run(params)
        assert result.success

    @patch("src.runbooks.service_restart.run_ansible")
    def test_verify_active(self, mock_run: MagicMock) -> None:
        mock_run.return_value = RunbookResult(
            success=True, stdout="service_state=active", stderr="", duration_sec=0.5
        )
        rb = ServiceRestartRunbook()
        params = ServiceRestartParams(target_host="10.0.0.1", service_name="nginx")
        assert rb.verify(params) is True

    @patch("src.runbooks.service_restart.run_ansible")
    def test_verify_inactive(self, mock_run: MagicMock) -> None:
        mock_run.return_value = RunbookResult(
            success=True, stdout="service_state=inactive", stderr="", duration_sec=0.5
        )
        rb = ServiceRestartRunbook()
        params = ServiceRestartParams(target_host="10.0.0.1", service_name="nginx")
        assert rb.verify(params) is False
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/Scripts/python -m pytest tests/test_runbooks.py -v
```

Expected: FAIL

- [ ] **Step 3: 写 base.py**

```python
# src/runbooks/base.py
import time
from abc import ABC, abstractmethod

import ansible_runner
from pydantic import BaseModel

from src.config import settings
from src.models import RunbookResult


def run_ansible(playbook: str, extravars: dict, check: bool = False) -> RunbookResult:
    """执行 Ansible Playbook，返回 RunbookResult"""

    start = time.monotonic()
    r = ansible_runner.run(
        private_data_dir=settings.ansible_private_data_dir,
        playbook=playbook,
        inventory=settings.ansible_inventory,
        extravars=extravars,
        **({"cmdline": "--check"} if check else {}),
    )
    duration = time.monotonic() - start

    stdout = r.stdout.read() if r.stdout else ""
    stderr = r.stderr.read() if r.stderr else ""

    return RunbookResult(
        success=r.status == "successful",
        stdout=stdout,
        stderr=stderr,
        duration_sec=round(duration, 2),
    )


class BaseRunbook(ABC):
    """每个 Runbook 必须实现五要素"""

    @abstractmethod
    def params_schema(self) -> type[BaseModel]:
        """参数 Schema"""

    @abstractmethod
    def dry_run(self, params: BaseModel) -> RunbookResult:
        """仿真执行，不产生副作用"""

    @abstractmethod
    def execute(self, params: BaseModel) -> RunbookResult:
        """实际执行"""

    @abstractmethod
    def rollback(self, snapshot: dict) -> bool:
        """回滚到快照状态"""

    @abstractmethod
    def verify(self, params: BaseModel) -> bool:
        """反向验证：目标是否恢复健康"""
```

- [ ] **Step 4: 创建 ansible/inventory.ini**

```ini
[aiops_targets]
aiops-target ansible_host=192.168.1.12 ansible_user=root

[all:vars]
ansible_python_interpreter=/usr/bin/python3
```

- [ ] **Step 5: 创建 ansible/disk_cleanup.yml**

```yaml
---
- name: Disk cleanup
  hosts: "{{ target_host }}"
  gather_facts: false
  vars:
    cleanup_path: "{{ path | default('/tmp') }}"
    min_age: "{{ min_age_days | default(7) }}"

  tasks:
    - name: Find old files (dry-run info)
      ansible.builtin.find:
        paths: "{{ cleanup_path }}"
        age: "{{ min_age }}d"
        file_type: file
      register: old_files

    - name: Show files to delete
      ansible.builtin.debug:
        msg: "Would delete {{ old_files.files | length }} files in {{ cleanup_path }}"

    - name: Delete old files
      ansible.builtin.find:
        paths: "{{ cleanup_path }}"
        age: "{{ min_age }}d"
        file_type: file
      register: to_delete

    - name: Remove files
      ansible.builtin.file:
        path: "{{ item.path }}"
        state: absent
      loop: "{{ to_delete.files }}"
      when: to_delete.files | length > 0

    - name: Check disk usage after cleanup
      ansible.builtin.shell:
        cmd: "df --output=pcent {{ cleanup_path }} | tail -1 | tr -d ' %'"
      register: disk_usage
      changed_when: false

    - name: Set disk usage fact
      ansible.builtin.set_fact:
        disk_usage: "{{ disk_usage.stdout }}"
```

- [ ] **Step 6: 创建 ansible/service_restart.yml**

```yaml
---
- name: Service restart
  hosts: "{{ target_host }}"
  gather_facts: false
  vars:
    svc_name: "{{ service_name }}"

  tasks:
    - name: Check current service status
      ansible.builtin.systemd:
        name: "{{ svc_name }}"
      register: svc_before
      ignore_errors: true

    - name: Show current status
      ansible.builtin.debug:
        msg: "Service {{ svc_name }} state: {{ svc_before.status.ActiveState | default('unknown') }}"

    - name: Restart service
      ansible.builtin.systemd:
        name: "{{ svc_name }}"
        state: restarted
      register: restart_result

    - name: Wait for service to start
      ansible.builtin.pause:
        seconds: 3

    - name: Verify service is active
      ansible.builtin.systemd:
        name: "{{ svc_name }}"
      register: svc_after

    - name: Report final status
      ansible.builtin.debug:
        msg: "Service {{ svc_name }} state after restart: {{ svc_after.status.ActiveState }}"
```

- [ ] **Step 7: 写 disk_cleanup.py**

```python
# src/runbooks/disk_cleanup.py
from pydantic import BaseModel

from src.models import RunbookResult
from src.runbooks.base import BaseRunbook, run_ansible


class DiskCleanupParams(BaseModel):
    """磁盘清理参数"""

    target_host: str
    path: str = "/tmp"
    min_age_days: int = 7


class DiskCleanupRunbook(BaseRunbook):
    """清理目标主机上指定路径下的过期文件"""

    def params_schema(self) -> type[BaseModel]:
        return DiskCleanupParams

    def dry_run(self, params: BaseModel) -> RunbookResult:
        p = DiskCleanupParams.model_validate(params.model_dump())
        return run_ansible(
            "disk_cleanup.yml",
            extravars={
                "target_host": p.target_host,
                "path": p.path,
                "min_age_days": p.min_age_days,
            },
            check=True,
        )

    def execute(self, params: BaseModel) -> RunbookResult:
        p = DiskCleanupParams.model_validate(params.model_dump())
        return run_ansible(
            "disk_cleanup.yml",
            extravars={
                "target_host": p.target_host,
                "path": p.path,
                "min_age_days": p.min_age_days,
            },
        )

    def rollback(self, snapshot: dict) -> bool:
        # 磁盘清理无法回滚
        return False

    def verify(self, params: BaseModel) -> bool:
        p = DiskCleanupParams.model_validate(params.model_dump())
        result = run_ansible(
            "disk_cleanup.yml",
            extravars={
                "target_host": p.target_host,
                "path": p.path,
                "min_age_days": p.min_age_days,
            },
            check=True,
        )
        # 从输出中提取磁盘使用率
        if not result.success:
            return False
        for line in result.stdout.splitlines():
            if "disk_usage=" in line:
                usage = int(line.split("disk_usage=")[1].strip())
                return usage < 80
        return False
```

- [ ] **Step 8: 写 service_restart.py**

```python
# src/runbooks/service_restart.py
from pydantic import BaseModel

from src.models import RunbookResult
from src.runbooks.base import BaseRunbook, run_ansible


class ServiceRestartParams(BaseModel):
    """服务重启参数"""

    target_host: str
    service_name: str


class ServiceRestartRunbook(BaseRunbook):
    """重启目标主机上的 systemd 服务"""

    def params_schema(self) -> type[BaseModel]:
        return ServiceRestartParams

    def dry_run(self, params: BaseModel) -> RunbookResult:
        p = ServiceRestartParams.model_validate(params.model_dump())
        return run_ansible(
            "service_restart.yml",
            extravars={
                "target_host": p.target_host,
                "service_name": p.service_name,
            },
            check=True,
        )

    def execute(self, params: BaseModel) -> RunbookResult:
        p = ServiceRestartParams.model_validate(params.model_dump())
        return run_ansible(
            "service_restart.yml",
            extravars={
                "target_host": p.target_host,
                "service_name": p.service_name,
            },
        )

    def rollback(self, snapshot: dict) -> bool:
        # 无版本化回滚
        return False

    def verify(self, params: BaseModel) -> bool:
        p = ServiceRestartParams.model_validate(params.model_dump())
        result = run_ansible(
            "service_restart.yml",
            extravars={
                "target_host": p.target_host,
                "service_name": p.service_name,
            },
            check=True,
        )
        if not result.success:
            return False
        return "service_state=active" in result.stdout
```

- [ ] **Step 9: 写 __init__.py 注册表**

```python
# src/runbooks/__init__.py
from src.runbooks.base import BaseRunbook
from src.runbooks.disk_cleanup import DiskCleanupRunbook
from src.runbooks.service_restart import ServiceRestartRunbook

RUNBOOK_REGISTRY: dict[str, type[BaseRunbook]] = {
    "disk_cleanup": DiskCleanupRunbook,
    "service_restart": ServiceRestartRunbook,
}
```

- [ ] **Step 10: 跑测试确认通过**

```bash
.venv/Scripts/python -m pytest tests/test_runbooks.py -v
```

Expected: 全部 PASS

- [ ] **Step 11: Commit**

```bash
git add src/runbooks/ ansible/ tests/test_runbooks.py
git commit -m "feat: Runbook base class + disk_cleanup and service_restart with Ansible playbooks"
```

---

## Task 5: 飞书 Activity

**Files:**
- Create: `src/activities/__init__.py`
- Create: `src/activities/feishu.py`

- [ ] **Step 1: 创建 src/activities/__init__.py**

```python
```

- [ ] **Step 2: 写 feishu.py**

```python
# src/activities/feishu.py
import json

import httpx
from temporalio import activity

from src.config import settings
from src.models import Alert


def build_feishu_card(alert: Alert, workflow_id: str) -> dict:
    """构造飞书 Interactive Card"""

    severity_emoji = {
        "disaster": "🔴",
        "high": "🟠",
        "average": "🟡",
        "warning": "🔵",
        "info": "⚪",
    }
    emoji = severity_emoji.get(alert.severity, "⚪")

    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"{emoji} AIOps 告警通知"},
                "template": "red" if alert.severity in ("disaster", "high") else "orange",
            },
            "elements": [
                {
                    "tag": "div",
                    "fields": [
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**设备：**{alert.hostname}"}},
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**IP：**{alert.host_ip}"}},
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**级别：**{alert.severity}"}},
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**状态：**{alert.status}"}},
                    ],
                },
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**告警：**{alert.event_name}"}},
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**详情：**{alert.message}"}},
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**时间：**{alert.timestamp}"}},
                {"tag": "hr"},
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "批准执行"},
                            "type": "primary",
                            "value": json.dumps({"workflow_id": workflow_id, "action": "approve", "alert_id": alert.event_id}),
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "拒绝"},
                            "type": "danger",
                            "value": json.dumps({"workflow_id": workflow_id, "action": "reject", "alert_id": alert.event_id}),
                        },
                    ],
                },
            ],
        },
    }


@activity.defn
async def send_feishu_alert(alert_json: str, workflow_id: str) -> str:
    """推送告警卡片到飞书，返回 message_id"""

    alert = Alert.model_validate_json(alert_json)
    card = build_feishu_card(alert, workflow_id)

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            settings.feishu_webhook_url,
            json=card,
            timeout=10,
        )
        resp.raise_for_status()
        result = resp.json()

    # 飞书 webhook 返回 {"StatusCode": 0, "StatusMessage": "success", ...}
    if result.get("StatusCode", -1) != 0:
        raise RuntimeError(f"Feishu API error: {result}")

    return result.get("msg_id", "")


@activity.defn
async def send_feishu_result(message: str) -> None:
    """推送执行结果到飞书"""

    payload = {
        "msg_type": "text",
        "content": {"text": message},
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            settings.feishu_webhook_url,
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
```

- [ ] **Step 3: Lint 检查**

```bash
.venv/Scripts/ruff check src/activities/feishu.py
.venv/Scripts/ruff format --check src/activities/feishu.py
```

Expected: 无错误

- [ ] **Step 4: Commit**

```bash
git add src/activities/
git commit -m "feat: feishu activity - alert card push and result notification"
```

---

## Task 6: Runbook Activity + 审计 Activity

**Files:**
- Create: `src/activities/runbook.py`
- Create: `src/activities/audit.py`

- [ ] **Step 1: 写 runbook activity**

```python
# src/activities/runbook.py
import json

from temporalio import activity

from src.models import ExecutionResult, RunbookResult
from src.runbooks import RUNBOOK_REGISTRY


@activity.defn
async def execute_runbook(runbook_id: str, params_json: str) -> str:
    """执行 Runbook：dry-run → execute → verify。返回 ExecutionResult JSON"""

    if runbook_id not in RUNBOOK_REGISTRY:
        raise ValueError(f"Unknown runbook: {runbook_id}")

    runbook_cls = RUNBOOK_REGISTRY[runbook_id]
    runbook = runbook_cls()
    schema = runbook.params_schema()
    params = schema.model_validate_json(params_json)

    # 1. dry-run
    dry_result = runbook.dry_run(params)
    if not dry_result.success:
        result = ExecutionResult(
            dry_run=dry_result,
            execute=RunbookResult(success=False, stdout="", stderr="dry-run failed", duration_sec=0),
            verify=False,
            snapshot={},
        )
        return result.model_dump_json()

    # 2. execute
    exec_result = runbook.execute(params)

    # 3. verify
    verified = False
    if exec_result.success:
        verified = runbook.verify(params)

    # 4. rollback if verify failed
    rolled_back = False
    if exec_result.success and not verified:
        snapshot = {"params": params_json, "exec_stdout": exec_result.stdout}
        rolled_back = runbook.rollback(snapshot)

    result = ExecutionResult(
        dry_run=dry_result,
        execute=exec_result,
        verify=verified,
        snapshot={"params": params_json},
        rolled_back=rolled_back,
    )
    return result.model_dump_json()
```

- [ ] **Step 2: 写 audit activity**

```python
# src/activities/audit.py
import json
from datetime import datetime, timezone

from temporalio import activity

from src.models import AuditRecord, Alert, ExecutionResult


@activity.defn
async def write_audit(
    alert_json: str,
    workflow_id: str,
    decision: str,
    runbook_id: str | None,
    runbook_params_json: str | None,
    execution_result_json: str | None,
    feishu_message_id: str | None,
) -> str:
    """写入审计日志，返回 AuditRecord JSON"""

    alert = Alert.model_validate_json(alert_json)
    execution_result = ExecutionResult.model_validate_json(execution_result_json) if execution_result_json else None
    runbook_params = json.loads(runbook_params_json) if runbook_params_json else None

    record = AuditRecord(
        alert=alert,
        workflow_id=workflow_id,
        decision=decision,
        runbook_id=runbook_id,
        runbook_params=runbook_params,
        execution_result=execution_result,
        feishu_message_id=feishu_message_id,
        created_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
    )

    # Phase 1: 先写日志，后续接 PostgreSQL
    print(f"[AUDIT] {record.alert.event_id} | {record.decision} | {record.runbook_id}")

    return record.model_dump_json()
```

- [ ] **Step 3: Lint 检查**

```bash
.venv/Scripts/ruff check src/activities/
.venv/Scripts/ruff format --check src/activities/
```

Expected: 无错误

- [ ] **Step 4: Commit**

```bash
git add src/activities/runbook.py src/activities/audit.py
git commit -m "feat: runbook execution activity + audit logging activity"
```

---

## Task 7: Temporal Workflow

**Files:**
- Create: `src/workflows/__init__.py`
- Create: `src/workflows/alert_workflow.py`
- Create: `tests/test_workflow.py`

- [ ] **Step 1: 写 Workflow 测试**

```python
# tests/test_workflow.py
import json
from datetime import datetime
from unittest.mock import patch, AsyncMock

import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.models import Alert, ExecutionResult, RunbookResult
from src.workflows.alert_workflow import AlertWorkflow, ApprovalDecision


@pytest.fixture
def alert_json() -> str:
    alert = Alert(
        event_id="evt-test-001",
        event_name="Disk full",
        severity="high",
        hostname="web-01",
        host_ip="10.0.0.1",
        trigger_id="100",
        message="Disk usage 95%",
        timestamp=datetime(2026, 1, 1, 12, 0, 0),
        status="problem",
    )
    return alert.model_dump_json()


@pytest.mark.asyncio
async def test_workflow_approved(alert_json: str) -> None:
    """审批通过场景：发送告警 → 收到 approve 信号 → 执行 Runbook → 写审计"""

    mock_feishu_result = ExecutionResult(
        dry_run=RunbookResult(success=True, stdout="dry", stderr="", duration_sec=0.1),
        execute=RunbookResult(success=True, stdout="done", stderr="", duration_sec=1.0),
        verify=True,
        snapshot={},
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        with (
            patch("src.activities.feishu.send_feishu_alert", new_callable=AsyncMock) as mock_send,
            patch("src.activities.feishu.send_feishu_result", new_callable=AsyncMock) as mock_result,
            patch("src.activities.runbook.execute_runbook", new_callable=AsyncMock) as mock_exec,
            patch("src.activities.audit.write_audit", new_callable=AsyncMock) as mock_audit,
        ):
            mock_send.return_value = "msg-001"
            mock_exec.return_value = mock_feishu_result.model_dump_json()
            mock_audit.return_value = "{}"
            mock_result.return_value = None

            async with Worker(
                env.client,
                task_queue="test-queue",
                workflows=[AlertWorkflow],
                activities=[mock_send, mock_exec, mock_audit, mock_result],
            ):
                handle = await env.client.start_workflow(
                    AlertWorkflow.run,
                    alert_json,
                    id="test-wf-001",
                    task_queue="test-queue",
                )

                # 发送审批信号
                await handle.signal(AlertWorkflow.approve, ApprovalDecision(approved=True))

                result = await handle.result()
                assert result == "approved"


@pytest.mark.asyncio
async def test_workflow_rejected(alert_json: str) -> None:
    """审批拒绝场景"""

    async with await WorkflowEnvironment.start_time_skipping() as env:
        with (
            patch("src.activities.feishu.send_feishu_alert", new_callable=AsyncMock) as mock_send,
            patch("src.activities.feishu.send_feishu_result", new_callable=AsyncMock) as mock_result,
            patch("src.activities.audit.write_audit", new_callable=AsyncMock) as mock_audit,
        ):
            mock_send.return_value = "msg-002"
            mock_audit.return_value = "{}"
            mock_result.return_value = None

            async with Worker(
                env.client,
                task_queue="test-queue",
                workflows=[AlertWorkflow],
                activities=[mock_send, mock_audit, mock_result],
            ):
                handle = await env.client.start_workflow(
                    AlertWorkflow.run,
                    alert_json,
                    id="test-wf-002",
                    task_queue="test-queue",
                )

                await handle.signal(AlertWorkflow.approve, ApprovalDecision(approved=False))

                result = await handle.result()
                assert result == "rejected"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/Scripts/python -m pytest tests/test_workflow.py -v
```

Expected: FAIL

- [ ] **Step 3: 创建 src/workflows/__init__.py**

```python
```

- [ ] **Step 4: 写 alert_workflow.py**

```python
# src/workflows/alert_workflow.py
import asyncio
import json
from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from src.activities.feishu import send_feishu_alert, send_feishu_result
    from src.activities.runbook import execute_runbook
    from src.activities.audit import write_audit


def _select_runbook(alert: dict) -> str:
    """Phase 1 简单匹配：按告警名称关键词选 Runbook"""
    name = alert.get("event_name", "").lower()
    if "disk" in name or "磁盘" in name:
        return "disk_cleanup"
    if "service" in name or "进程" in name or "process" in name:
        return "service_restart"
    return "disk_cleanup"


class ApprovalDecision:
    """审批决策信号载荷"""

    def __init__(self, approved: bool) -> None:
        self.approved = approved


@workflow.defn
class AlertWorkflow:
    """告警处理主工作流"""

    def __init__(self) -> None:
        self._approval_received = False
        self._approved = False

    @workflow.run
    async def run(self, alert_json: str) -> str:
        alert = json.loads(alert_json)
        event_id = alert["event_id"]
        workflow_id = workflow.info().workflow_id

        # 1. 推送飞书告警卡片
        feishu_msg_id = await workflow.execute_activity(
            send_feishu_alert,
            args=[alert_json, workflow_id],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=workflow.RetryPolicy(maximum_attempts=3),
        )

        # 2. 等待审批信号（30 分钟超时）
        try:
            await workflow.wait_condition(
                lambda: self._approval_received,
                timeout=timedelta(minutes=30),
            )
        except asyncio.TimeoutError:
            # 审批超时
            await workflow.execute_activity(
                write_audit,
                args=[alert_json, workflow_id, "timeout", None, None, None, feishu_msg_id],
                start_to_close_timeout=timedelta(seconds=10),
            )
            await workflow.execute_activity(
                send_feishu_result,
                args=[f"⏰ 告警 {event_id} 审批超时（30分钟），已跳过"],
                start_to_close_timeout=timedelta(seconds=10),
            )
            return "timeout"

        if not self._approved:
            # 审批拒绝
            await workflow.execute_activity(
                write_audit,
                args=[alert_json, workflow_id, "rejected", None, None, None, feishu_msg_id],
                start_to_close_timeout=timedelta(seconds=10),
            )
            await workflow.execute_activity(
                send_feishu_result,
                args=[f"❌ 告警 {event_id} 已被拒绝"],
                start_to_close_timeout=timedelta(seconds=10),
            )
            return "rejected"

        # 3. 执行 Runbook（Phase 1 无 LLM，按告警关键词匹配）
        runbook_id = _select_runbook(alert)
        runbook_params = json.dumps({"target_host": alert["host_ip"]})

        exec_result_json = await workflow.execute_activity(
            execute_runbook,
            args=[runbook_id, runbook_params],
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=workflow.RetryPolicy(maximum_attempts=1),
        )

        # 4. 写审计
        await workflow.execute_activity(
            write_audit,
            args=[alert_json, workflow_id, "approved", runbook_id, runbook_params, exec_result_json, feishu_msg_id],
            start_to_close_timeout=timedelta(seconds=10),
        )

        # 5. 飞书通知结果
        exec_result = json.loads(exec_result_json)
        if exec_result.get("verify"):
            msg = f"✅ 告警 {event_id} 处理成功（Runbook: {runbook_id}）"
        else:
            msg = f"⚠️ 告警 {event_id} 执行完成但验证未通过，可能需要人工介入"

        await workflow.execute_activity(
            send_feishu_result,
            args=[msg],
            start_to_close_timeout=timedelta(seconds=10),
        )

        return "approved"

    @workflow.signal
    def approve(self, decision: ApprovalDecision) -> None:
        """接收飞书审批回调信号"""
        self._approval_received = True
        self._approved = decision.approved
```

- [ ] **Step 5: 跑测试确认通过**

```bash
.venv/Scripts/python -m pytest tests/test_workflow.py -v
```

Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add src/workflows/ tests/test_workflow.py
git commit -m "feat: Temporal AlertWorkflow with signal-based approval and three branches"
```

---

## Task 8: FastAPI Webhook Endpoints

**Files:**
- Create: `src/api/__init__.py`
- Create: `src/api/webhook.py`
- Create: `tests/test_webhook.py`

- [ ] **Step 1: 写 webhook 测试**

```python
# tests/test_webhook.py
import json
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app


@pytest.fixture
def zabbix_payload() -> dict:
    return {
        "event_id": "12345",
        "event_name": "Disk usage > 90%",
        "severity": "high",
        "hostname": "web-server-01",
        "host_ip": "192.168.1.13",
        "trigger_id": "10001",
        "message": "Disk usage is 95% on /tmp",
        "timestamp": "2026-04-30T14:30:00Z",
        "status": "problem",
    }


@pytest.mark.asyncio
async def test_zabbix_webhook_success(zabbix_payload: dict) -> None:
    with patch("src.api.webhook.produce_alert", new_callable=AsyncMock) as mock_produce:
        mock_produce.return_value = "1234567890-0"

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/webhook/zabbix", json=zabbix_payload)

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "accepted"
        assert data["event_id"] == "12345"


@pytest.mark.asyncio
async def test_zabbix_webhook_duplicate(zabbix_payload: dict) -> None:
    with patch("src.api.webhook.produce_alert", new_callable=AsyncMock) as mock_produce:
        mock_produce.return_value = None  # 重复

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/webhook/zabbix", json=zabbix_payload)

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "duplicate"


@pytest.mark.asyncio
async def test_zabbix_webhook_invalid_payload() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhook/zabbix", json={"bad": "data"})

    assert resp.status_code == 422  # Pydantic validation error
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/Scripts/python -m pytest tests/test_webhook.py -v
```

Expected: FAIL

- [ ] **Step 3: 创建 src/api/__init__.py**

```python
```

- [ ] **Step 4: 写 main.py（FastAPI app）**

```python
# src/main.py
import asyncio
import json
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from temporalio.client import Client

from src.api.webhook import router as webhook_router
from src.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化 Redis + Temporal 客户端，关闭时清理"""

    # Redis
    redis_client = aioredis.from_url(settings.redis_url)
    app.state.redis = redis_client

    # Temporal client
    temporal_client = await Client.connect(settings.temporal_address)
    app.state.temporal = temporal_client

    # 创建 Redis Stream consumer group
    try:
        await redis_client.xgroup_create("aiops:alerts", "aiops-workers", id="0", mkstream=True)
    except aioredis.exceptions.ResponseError:
        pass  # already exists

    yield

    await redis_client.aclose()
    await temporal_client.__aexit__(None, None, None)


app = FastAPI(title="AIOps Phase 1", lifespan=lifespan)
app.include_router(webhook_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
```

- [ ] **Step 5: 写 webhook.py**

```python
# src/api/webhook.py
import json
import logging

from fastapi import APIRouter, Request

from src.bus.producer import produce_alert
from src.models import Alert

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/webhook/zabbix")
async def zabbix_webhook(request: Request):
    """接收 Zabbix 告警 Webhook"""

    body = await request.json()
    alert = Alert.model_validate(body)

    redis = request.app.state.redis
    msg_id = await produce_alert(redis, alert)

    if msg_id is None:
        logger.info(f"Duplicate alert: {alert.event_id}")
        return {"status": "duplicate", "event_id": alert.event_id}

    logger.info(f"Alert received: {alert.event_id} -> stream {msg_id}")
    return {"status": "accepted", "event_id": alert.event_id, "stream_id": msg_id}


@router.post("/webhook/feishu")
async def feishu_webhook(request: Request):
    """接收飞书卡片审批回调"""

    body = await request.json()

    # 飞书回调格式: {"action": {"value": "..."}}
    action_value = body.get("action", {}).get("value", "{}")
    callback = json.loads(action_value)

    workflow_id = callback.get("workflow_id")
    action = callback.get("action")
    approved = action == "approve"

    if not workflow_id:
        return {"status": "error", "message": "missing workflow_id"}

    # 通过 Temporal signal 发送审批结果
    temporal = request.app.state.temporal
    handle = temporal.get_workflow_handle(workflow_id)

    from src.workflows.alert_workflow import AlertWorkflow, ApprovalDecision

    await handle.signal(AlertWorkflow.approve, ApprovalDecision(approved=approved))

    logger.info(f"Approval signal sent: workflow={workflow_id}, approved={approved}")
    return {"status": "ok"}
```

- [ ] **Step 6: 跑测试确认通过**

```bash
.venv/Scripts/python -m pytest tests/test_webhook.py -v
```

Expected: 全部 PASS

- [ ] **Step 7: Commit**

```bash
git add src/main.py src/api/ tests/test_webhook.py
git commit -m "feat: FastAPI webhook endpoints for Zabbix alerts and Feishu approval callbacks"
```

---

## Task 9: Redis Stream Consumer Worker

**Files:**
- Modify: `src/bus/consumer.py`
- Modify: `src/main.py`

- [ ] **Step 1: 扩展 consumer.py 添加 worker 循环**

```python
# src/bus/consumer.py 新增以下内容

import asyncio
import logging

from src.config import settings

logger = logging.getLogger(__name__)


async def start_consumer_loop(app) -> None:
    """持续消费 Redis Stream，触发 Temporal Workflow"""

    redis = app.state.redis
    temporal = app.state.temporal

    # 确保 consumer group 存在
    try:
        await redis.xgroup_create("aiops:alerts", "aiops-workers", id="0", mkstream=True)
    except Exception:
        pass

    logger.info("Consumer loop started")

    while True:
        result = await consume_alert(redis, "aiops-workers", "worker-1", block_ms=5000)
        if result is None:
            continue

        alert, msg_id = result
        workflow_id = f"alert-{alert.event_id}"

        try:
            await temporal.start_workflow(
                "AlertWorkflow",
                alert.model_dump_json(),
                id=workflow_id,
                task_queue=settings.temporal_task_queue,
            )
            logger.info(f"Workflow started: {workflow_id}")
        except Exception as e:
            logger.error(f"Failed to start workflow for {alert.event_id}: {e}")
```

- [ ] **Step 2: 更新 main.py lifespan 启动 consumer**

在 `lifespan` 函数的 `yield` 之前添加：

```python
    # 启动 consumer 后台任务
    from src.bus.consumer import start_consumer_loop
    consumer_task = asyncio.create_task(start_consumer_loop(app))

    yield

    consumer_task.cancel()
```

- [ ] **Step 3: Lint 检查**

```bash
.venv/Scripts/ruff check src/
```

Expected: 无错误

- [ ] **Step 4: Commit**

```bash
git add src/bus/consumer.py src/main.py
git commit -m "feat: Redis Stream consumer worker that triggers Temporal workflows"
```

---

## Task 10: Temporal Worker 启动集成

**Files:**
- Modify: `src/main.py`

- [ ] **Step 1: 更新 main.py 添加 Temporal Worker 启动**

在 `lifespan` 函数中添加 Worker：

```python
from temporalio.worker import Worker

with workflow.unsafe.imports_passed_through():
    from src.activities.feishu import send_feishu_alert, send_feishu_result
    from src.activities.runbook import execute_runbook
    from src.activities.audit import write_audit
from src.workflows.alert_workflow import AlertWorkflow
```

在 lifespan 的 yield 之前：

```python
    # 启动 Temporal Worker
    worker = Worker(
        temporal_client,
        task_queue=settings.temporal_task_queue,
        workflows=[AlertWorkflow],
        activities=[send_feishu_alert, send_feishu_result, execute_runbook, write_audit],
    )
    worker_task = asyncio.create_task(worker.run())

    yield

    worker_task.cancel()
    consumer_task.cancel()
```

- [ ] **Step 2: Lint + 全量测试**

```bash
.venv/Scripts/ruff check src/
.venv/Scripts/python -m pytest tests/ -v
```

Expected: 无 lint 错误，测试全部通过

- [ ] **Step 3: Commit**

```bash
git add src/main.py
git commit -m "feat: integrate Temporal Worker startup into FastAPI lifespan"
```

---

## Task 11: 端到端冒烟测试

- [ ] **Step 1: 本地 docker-compose 启动**

```bash
cd /d/project/aiops
cp .env.example .env
docker compose up -d
```

- [ ] **Step 2: 检查服务状态**

```bash
docker compose ps
```

Expected: 5 个容器全部 running

- [ ] **Step 3: 访问 Temporal UI**

浏览器打开 `http://localhost:8080`，确认 Temporal 正常

- [ ] **Step 4: 模拟 Zabbix Webhook 调用**

```bash
curl -X POST http://localhost:8000/webhook/zabbix \
  -H "Content-Type: application/json" \
  -d '{
    "event_id": "smoke-test-001",
    "event_name": "Disk usage > 90%",
    "severity": "high",
    "hostname": "aiops-target",
    "host_ip": "192.168.1.12",
    "trigger_id": "10001",
    "message": "Disk usage 95% on /tmp",
    "timestamp": "2026-04-30T14:30:00Z",
    "status": "problem"
  }'
```

Expected: `{"status": "accepted", "event_id": "smoke-test-001", ...}`

- [ ] **Step 5: 检查 Temporal UI 中是否有 Workflow**

打开 `http://localhost:8080`，应看到 `alert-smoke-test-001` workflow 在运行（等待审批）

- [ ] **Step 6: 模拟飞书审批回调**

```bash
curl -X POST http://localhost:8000/webhook/feishu \
  -H "Content-Type: application/json" \
  -d '{
    "action": {
      "value": "{\"workflow_id\": \"alert-smoke-test-001\", \"action\": \"approve\", \"alert_id\": \"smoke-test-001\"}"
    }
  }'
```

Expected: `{"status": "ok"}`

- [ ] **Step 7: 确认 Workflow 完成**

Temporal UI 中 workflow 状态变为 Completed

- [ ] **Step 8: Commit 冒烟测试文档**

```bash
git add docs/
git commit -m "docs: add smoke test instructions"
```

---

## Final Checklist

- [ ] 所有测试通过：`pytest tests/ -v`
- [ ] Lint 无错误：`ruff check src/`
- [ ] docker-compose 本地跑通
- [ ] Zabbix Webhook → 飞书 → 审批 → Runbook 全链路验证
- [ ] 代码推送到 Git
