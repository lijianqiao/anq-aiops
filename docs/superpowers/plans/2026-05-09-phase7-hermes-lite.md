# Phase 7 Hermes 精简版（知识层）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 AIOps **越用越聪明**——agent 在每次诊断之前，先查 PostgreSQL 里的"过去类似告警怎么处理的"，把 Top-3 历史案例注入 system prompt。同时把 audit log 从 JSONL 文件迁到 PG，配合 `tsvector` 全文搜索，建立可检索的经验库。**不引入向量数据库**，PG 全文检索对内网告警量完全够用。

**Architecture:** 借鉴 Hermes Agent 的设计模式（参考 [Hermes Agent docs](https://hermes-agent.nousresearch.com/docs/)）：
- **持久层**：PostgreSQL（已有 Temporal 在用，复用 instance）+ `tsvector` 全文索引（不上 SQLite FTS5 是因为 PG 已经在用，少一个数据库依赖）
- **检索时机**：ReAct agent 多轮循环之前，跑一次相似案例查询；查询结果作为 "Past Experiences" 段注入 system prompt
- **写入时机**：workflow 执行完成后（不管成功/失败），异步活动把记录写进 PG（同时保留 audit.log JSONL 兜底）
- **Progressive disclosure**（借鉴 Hermes）：注入到 prompt 的只是案例**摘要 + 链接**，agent 需要详情时再调 tool 查全文（这层 Phase 8 实现，Phase 7 先做摘要注入）

**Tech Stack:** Python 3.14, asyncpg (新增), PostgreSQL 17 (已有), Pydantic, pytest

**Spec:** [docs/生产级 AIOps 架构设计.md](../../生产级 AIOps 架构设计.md) §2.1（推理辅助）+ §2.2（经验沉淀）+ §11（反馈数据）

---

## 设计权衡（务必读完）

### 为什么不接 Hermes Agent 本身

调研结论（[Nous Research GitHub](https://github.com/nousresearch/hermes-agent)）：
- Hermes Agent **不暴露 Python SDK**，是 standalone agent + CLI/Slack/Telegram 接口
- 我们想要的是 *embedded* knowledge layer，不是 *standalone* agent

所以**借鉴 Hermes 的设计**自己实现：
- ✅ SQLite + FTS5 → 我们用 PG + tsvector（已有 PG，不引新数据库）
- ✅ MEMORY.md 风格的累积事实（"last time disk fill at /tmp on aiops-target was cleaned by disk_cleanup"）→ 我们用 audit_records 表
- ✅ Progressive disclosure（先看 catalog，需要时再加载详情）→ Phase 7 prompt 只注入摘要，Phase 8 加 tool

### 为什么不上向量数据库

候选：pgvector / chromadb / qdrant / faiss。**全部 reject**，理由：

1. **告警类型固定**：`disk_cleanup / service_restart` 几个场景，关键词搜索（hostname + event_name 命中）足够区分相似案例
2. **PG tsvector 性价比最高**：你们已经有 PG（Temporal 用），加 1 个 GIN 索引就能秒级检索
3. **embedding 反过来变慢**：每次 RCA 前要跑一次 embedding API → +500ms 延迟 + 额外成本
4. **后期可加**：Phase 8 真用着不够再加 pgvector，schema 不变只加列

参考 python-performance-optimization 的 "**Algorithmic: Better algorithms and data structures**" — 在你的数据规模下，正确的索引选择 > embedding 召回。

### 性能预算

每次 RCA 前查询经验库：
- 目标延迟 < 50ms（不能阻塞 agent 响应）
- 用 GIN 索引 on `tsvector` 列 → 全文搜索 O(log n)
- 单次返回 Top-3，每条限制 500 字符
- asyncpg 连接池：max=10，对 < 100 并发够用

### 写入路径性能

audit 写入是后台异步活动，性能不关键，但：
- **批量插入**：如果将来要做 trace 写入（每个告警 N 条 trace），用 `executemany` 比循环 INSERT 快 10x
- **避免大文本字段反复 tokenize**：tsvector 用 `STORED` 列（PG 14+ generated column），插入时一次性算好

---

## File Structure

| 文件 | 操作 | 职责 |
|---|---|---|
| `src/hermes/__init__.py` | 新建 | 包占位 |
| `src/hermes/db.py` | 新建 | asyncpg 连接池单例 + schema 初始化 |
| `src/hermes/schema.sql` | 新建 | `audit_records` 表 + GIN 索引 |
| `src/hermes/repository.py` | 新建 | `save_audit / find_similar / count_total` 数据访问层 |
| `src/hermes/models.py` | 新建 | `AuditRecord` Pydantic（区别于 src/models.py 里的，加 PG 字段） |
| `src/hermes/knowledge.py` | 新建 | `query_similar_cases(alert) → str`（注入 prompt 用） |
| `src/activities/audit.py` | 修改 | 写 audit 同时写 PG（保留 JSONL 兜底） |
| `src/llm/agent.py` | 修改 | system prompt 注入 "Past Experiences" 段 |
| `src/config.py` | 修改 | 加 `hermes_db_url` |
| `src/main.py` | 修改 | lifespan 初始化 hermes 连接池 |
| `tests/test_hermes_repository.py` | 新建 | repo 单测 |
| `tests/test_hermes_knowledge.py` | 新建 | knowledge query 测试 |
| `pyproject.toml` | 修改 | +asyncpg |
| `docker-compose.yml` | 修改 | 加 init-hermes-schema 一次性 service |
| `.env.example` | 修改 | +HERMES_DB_URL |
| `docs/hermes-knowledge.md` | 新建 | 运维手册 |

---

## Task 1: 加 asyncpg 依赖 + Hermes 数据库 schema

**Files:**
- Modify: `pyproject.toml`
- Create: `src/hermes/__init__.py`, `src/hermes/schema.sql`

- [ ] **Step 1: 加依赖**

`pyproject.toml`:
```toml
"asyncpg>=0.30",
```

- [ ] **Step 2: 写 schema.sql**

`src/hermes/schema.sql`:

```sql
-- Hermes 知识层：audit_records 表（存全部 workflow 执行记录）
-- 借鉴 Hermes Agent 的 SQLite + FTS5 思路，PG 14+ tsvector 等价方案

CREATE TABLE IF NOT EXISTS audit_records (
    id              BIGSERIAL PRIMARY KEY,
    event_id        TEXT NOT NULL,
    workflow_id     TEXT NOT NULL,
    decision        TEXT NOT NULL,        -- approved/auto_approved/rejected/timeout/denied/unsupported/skipped_mutex
    runbook_id      TEXT,
    runbook_params  JSONB,                -- 整个 params dict
    -- 告警事实
    hostname        TEXT NOT NULL,
    host_ip         TEXT NOT NULL,
    severity        TEXT NOT NULL,
    event_name      TEXT NOT NULL,
    message         TEXT NOT NULL,
    -- 执行结果
    verify          BOOLEAN,
    execute_success BOOLEAN,
    exec_stdout     TEXT,                 -- ansible stdout 末尾 4000 字（节省空间）
    -- agent trace（Phase 8 用）
    agent_reasoning TEXT,
    agent_confidence REAL,
    -- 时间
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ,
    -- 全文搜索字段（generated column，自动维护）
    fts             TSVECTOR GENERATED ALWAYS AS (
        setweight(to_tsvector('simple', coalesce(event_name, '')), 'A') ||
        setweight(to_tsvector('simple', coalesce(message, '')), 'B') ||
        setweight(to_tsvector('simple', coalesce(hostname, '')), 'C') ||
        setweight(to_tsvector('simple', coalesce(agent_reasoning, '')), 'D')
    ) STORED
);

-- GIN 索引：全文搜索用（参考 python-performance-optimization Pattern 8）
CREATE INDEX IF NOT EXISTS idx_audit_fts ON audit_records USING GIN (fts);

-- B-tree 复合索引：按 host_ip 倒序拉历史
CREATE INDEX IF NOT EXISTS idx_audit_host_time
    ON audit_records (host_ip, created_at DESC);

-- B-tree on decision：按结果 filter（成功的案例 vs 失败的）
CREATE INDEX IF NOT EXISTS idx_audit_decision_success
    ON audit_records (decision, verify) WHERE verify IS NOT NULL;
```

`src/hermes/__init__.py`:
```python
"""Hermes 知识层：基于 PostgreSQL + tsvector 的告警经验库

借鉴 Hermes Agent (https://hermes-agent.nousresearch.com/) 的设计：
- SQLite + FTS5 → 我们用 PostgreSQL + tsvector
- MEMORY.md 累积事实 → 我们用 audit_records 表
- Progressive disclosure → Phase 7 注入摘要，Phase 8 加 detail tool
"""
```

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml src/hermes/__init__.py src/hermes/schema.sql
git commit -m "feat(hermes): asyncpg dep + audit_records schema with FTS"
```

---

## Task 2: asyncpg 连接池 + schema 初始化

**Files:**
- Create: `src/hermes/db.py`
- Test: `tests/test_hermes_repository.py`

- [ ] **Step 1: 写测试**

`tests/test_hermes_repository.py`:

```python
"""Hermes db + repository 测试

需要 PG 跑（docker compose up -d postgres）。
测试用单独 schema (test_hermes) 避免污染。
"""

import pytest

from src.hermes.db import HermesDB


@pytest.fixture
async def db():
    db = HermesDB(dsn="postgresql://temporal:temporal@localhost:5432/temporal")
    await db.connect()
    # 用 test_hermes schema 隔离
    async with db.pool.acquire() as conn:
        await conn.execute("CREATE SCHEMA IF NOT EXISTS test_hermes")
        await conn.execute("SET search_path TO test_hermes, public")
        await conn.execute("DROP TABLE IF EXISTS test_hermes.audit_records")
    await db.init_schema(schema_name="test_hermes")
    yield db
    async with db.pool.acquire() as conn:
        await conn.execute("DROP SCHEMA test_hermes CASCADE")
    await db.close()


@pytest.mark.asyncio
async def test_db_connect_creates_pool():
    db = HermesDB(dsn="postgresql://temporal:temporal@localhost:5432/temporal")
    await db.connect()
    assert db.pool is not None
    await db.close()


@pytest.mark.asyncio
async def test_db_init_schema_creates_table(db):
    """init_schema 应创建 audit_records 表 + 索引"""
    async with db.pool.acquire() as conn:
        result = await conn.fetchval(
            "SELECT to_regclass('test_hermes.audit_records')"
        )
        assert result is not None


@pytest.mark.asyncio
async def test_db_init_schema_idempotent(db):
    """重复跑 init_schema 不应抛错"""
    await db.init_schema(schema_name="test_hermes")
    await db.init_schema(schema_name="test_hermes")  # 第二次也得 OK
```

- [ ] **Step 2: 跑测试确认 fail**

```bash
.venv/Scripts/python.exe -m pytest tests/test_hermes_repository.py -v
```

- [ ] **Step 3: 实现 HermesDB**

`src/hermes/db.py`:

```python
"""asyncpg 连接池 + schema 初始化

性能要点（python-performance-optimization）：
- 单进程一个连接池（不每次 acquire/release 整连接）
- min_size=2 / max_size=10，覆盖典型负载
- prepared statements 自动缓存（asyncpg 默认行为，O(1) 查找）
"""

import asyncio
import logging
from pathlib import Path

import asyncpg

logger = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class HermesDB:
    """asyncpg 连接池单例"""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.pool: asyncpg.Pool | None = None

    async def connect(self, min_size: int = 2, max_size: int = 10) -> None:
        if self.pool is not None:
            return
        self.pool = await asyncpg.create_pool(
            dsn=self.dsn,
            min_size=min_size,
            max_size=max_size,
            command_timeout=10,
        )

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    async def init_schema(self, schema_name: str = "public") -> None:
        """创建表 + 索引（幂等）

        schema_name: 默认 public，测试用 test_hermes 隔离
        """
        if self.pool is None:
            raise RuntimeError("DB not connected")
        sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        async with self.pool.acquire() as conn:
            if schema_name != "public":
                await conn.execute(f"SET search_path TO {schema_name}, public")
            await conn.execute(sql)
        logger.info(f"hermes schema initialized in {schema_name}")
```

- [ ] **Step 4: 跑测试确认 pass**

需要本地 PG 跑（`docker compose up -d postgres`）。

- [ ] **Step 5: Commit**

```bash
git add src/hermes/db.py tests/test_hermes_repository.py
git commit -m "feat(hermes): asyncpg pool + schema init"
```

---

## Task 3: Repository - save / find_similar

**Files:**
- Create: `src/hermes/models.py`, `src/hermes/repository.py`
- Modify: `tests/test_hermes_repository.py`

- [ ] **Step 1: 写测试（追加）**

```python
@pytest.mark.asyncio
async def test_save_audit_record(db):
    from src.hermes.repository import AuditRepository
    from src.hermes.models import AuditRecordWrite

    repo = AuditRepository(db.pool, schema="test_hermes")
    record = AuditRecordWrite(
        event_id="e1",
        workflow_id="wf-1",
        decision="approved",
        runbook_id="disk_cleanup",
        runbook_params={"target_host": "1.1.1.1", "path": "/tmp"},
        hostname="aiops-target",
        host_ip="1.1.1.1",
        severity="high",
        event_name="Disk usage > 90%",
        message="Disk usage 95% on /tmp",
        verify=True,
        execute_success=True,
        exec_stdout="ok",
    )
    rid = await repo.save(record)
    assert rid > 0


@pytest.mark.asyncio
async def test_find_similar_by_keywords(db):
    """全文搜索匹配相似告警"""
    from src.hermes.repository import AuditRepository
    from src.hermes.models import AuditRecordWrite

    repo = AuditRepository(db.pool, schema="test_hermes")

    # 写 3 条不同告警
    await repo.save(AuditRecordWrite(
        event_id="e1", workflow_id="wf-1", decision="approved",
        runbook_id="disk_cleanup", runbook_params={"path": "/tmp"},
        hostname="host-A", host_ip="1.1.1.1", severity="high",
        event_name="Disk usage > 90% /tmp", message="tmp full",
        verify=True, execute_success=True, exec_stdout="",
    ))
    await repo.save(AuditRecordWrite(
        event_id="e2", workflow_id="wf-2", decision="approved",
        runbook_id="service_restart", runbook_params={"service_name": "nginx"},
        hostname="host-A", host_ip="1.1.1.1", severity="high",
        event_name="nginx is down", message="nginx 502",
        verify=True, execute_success=True, exec_stdout="",
    ))
    await repo.save(AuditRecordWrite(
        event_id="e3", workflow_id="wf-3", decision="rejected",
        runbook_id=None, runbook_params=None,
        hostname="host-B", host_ip="2.2.2.2", severity="warning",
        event_name="CPU high", message="cpu 95%",
        verify=None, execute_success=None, exec_stdout=None,
    ))

    # 搜 "disk tmp"
    results = await repo.find_similar(query="disk tmp", host_ip="1.1.1.1", limit=3)
    assert len(results) >= 1
    assert results[0].event_id == "e1"


@pytest.mark.asyncio
async def test_find_similar_prefers_same_host(db):
    """搜索权重：同 host_ip 应排前面"""
    from src.hermes.repository import AuditRepository
    from src.hermes.models import AuditRecordWrite

    repo = AuditRepository(db.pool, schema="test_hermes")

    # 同关键词但 host 不同
    await repo.save(AuditRecordWrite(
        event_id="other-host", workflow_id="wf", decision="approved",
        runbook_id="disk_cleanup", runbook_params={},
        hostname="other", host_ip="9.9.9.9", severity="high",
        event_name="Disk usage > 90%", message="other host",
        verify=True, execute_success=True, exec_stdout="",
    ))
    await repo.save(AuditRecordWrite(
        event_id="same-host", workflow_id="wf", decision="approved",
        runbook_id="disk_cleanup", runbook_params={},
        hostname="my", host_ip="1.1.1.1", severity="high",
        event_name="Disk usage > 90%", message="my host",
        verify=True, execute_success=True, exec_stdout="",
    ))

    results = await repo.find_similar(query="disk", host_ip="1.1.1.1", limit=3)
    assert results[0].event_id == "same-host"  # 同 host 优先


@pytest.mark.asyncio
async def test_find_similar_returns_empty_on_no_match(db):
    from src.hermes.repository import AuditRepository
    repo = AuditRepository(db.pool, schema="test_hermes")
    results = await repo.find_similar(query="xyz nonexistent", host_ip=None, limit=3)
    assert results == []
```

- [ ] **Step 2: 跑测试确认 fail**

- [ ] **Step 3: 实现 models + repository**

`src/hermes/models.py`:

```python
"""Hermes 数据模型（区别于 src/models.py 的应用模型）"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AuditRecordWrite(BaseModel):
    """写入用 model（无 id/created_at，DB 默认填）"""

    event_id: str
    workflow_id: str
    decision: str
    runbook_id: str | None
    runbook_params: dict[str, Any] | None
    hostname: str
    host_ip: str
    severity: str
    event_name: str
    message: str
    verify: bool | None = None
    execute_success: bool | None = None
    exec_stdout: str | None = None
    agent_reasoning: str | None = None
    agent_confidence: float | None = None


class AuditRecordRead(BaseModel):
    """读取用 model（含 id/created_at）"""

    id: int
    event_id: str
    workflow_id: str
    decision: str
    runbook_id: str | None
    runbook_params: dict[str, Any] | None
    hostname: str
    host_ip: str
    severity: str
    event_name: str
    message: str
    verify: bool | None
    execute_success: bool | None
    exec_stdout: str | None
    agent_reasoning: str | None
    agent_confidence: float | None
    created_at: datetime
    completed_at: datetime | None = None
```

`src/hermes/repository.py`:

```python
"""数据访问层

性能要点（python-performance-optimization）：
- find_similar 单次查询返回 Top-3，避免拉全表后 Python 层过滤
- exec_stdout 截 4000 字符（避免存几 MB 大文本）
- 全文搜索用 ts_rank 排序 + 同 host 加权
- runbook_params dict → JSONB（PG 原生支持，索引可选）
"""

from typing import Any

import asyncpg

from src.hermes.models import AuditRecordRead, AuditRecordWrite


_STDOUT_TRUNCATE = 4000


class AuditRepository:
    def __init__(self, pool: asyncpg.Pool, schema: str = "public") -> None:
        self.pool = pool
        self.schema = schema

    @property
    def _table(self) -> str:
        return f"{self.schema}.audit_records"

    async def save(self, rec: AuditRecordWrite) -> int:
        """写入一条 audit，返回 id"""
        stdout = (rec.exec_stdout or "")[-_STDOUT_TRUNCATE:]  # 只保留尾部
        async with self.pool.acquire() as conn:
            rid = await conn.fetchval(
                f"""
                INSERT INTO {self._table}
                  (event_id, workflow_id, decision, runbook_id, runbook_params,
                   hostname, host_ip, severity, event_name, message,
                   verify, execute_success, exec_stdout,
                   agent_reasoning, agent_confidence, completed_at)
                VALUES
                  ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10,
                   $11, $12, $13, $14, $15, NOW())
                RETURNING id
                """,
                rec.event_id, rec.workflow_id, rec.decision, rec.runbook_id,
                _to_json(rec.runbook_params),
                rec.hostname, rec.host_ip, rec.severity, rec.event_name, rec.message,
                rec.verify, rec.execute_success, stdout,
                rec.agent_reasoning, rec.agent_confidence,
            )
        return int(rid)

    async def find_similar(
        self,
        query: str,
        host_ip: str | None,
        limit: int = 3,
        only_successful: bool = False,
    ) -> list[AuditRecordRead]:
        """全文搜索 Top-N 相似告警

        排序：同 host_ip 优先 + ts_rank 高优先 + 时间近优先
        """
        if not query.strip():
            return []
        ts_query = " | ".join(query.split())  # OR 模式：宽松匹配
        host_boost = "CASE WHEN host_ip = $2 THEN 1 ELSE 0 END" if host_ip else "0"

        params: list[Any] = [ts_query]
        if host_ip:
            params.append(host_ip)
        success_clause = "AND verify = true" if only_successful else ""

        sql = f"""
            SELECT id, event_id, workflow_id, decision, runbook_id, runbook_params,
                   hostname, host_ip, severity, event_name, message,
                   verify, execute_success, exec_stdout,
                   agent_reasoning, agent_confidence, created_at, completed_at,
                   ts_rank(fts, to_tsquery('simple', $1)) AS rank
            FROM {self._table}
            WHERE fts @@ to_tsquery('simple', $1)
            {success_clause}
            ORDER BY ({host_boost}) DESC, rank DESC, created_at DESC
            LIMIT {int(limit)}
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)

        return [_row_to_read(r) for r in rows]

    async def count_total(self) -> int:
        async with self.pool.acquire() as conn:
            v = await conn.fetchval(f"SELECT count(*) FROM {self._table}")
        return int(v or 0)


def _to_json(value: dict | None) -> str | None:
    import json
    return json.dumps(value) if value else None


def _row_to_read(row: asyncpg.Record) -> AuditRecordRead:
    import json
    params_raw = row["runbook_params"]
    params = json.loads(params_raw) if isinstance(params_raw, str) else params_raw
    return AuditRecordRead(
        id=row["id"],
        event_id=row["event_id"],
        workflow_id=row["workflow_id"],
        decision=row["decision"],
        runbook_id=row["runbook_id"],
        runbook_params=params,
        hostname=row["hostname"],
        host_ip=row["host_ip"],
        severity=row["severity"],
        event_name=row["event_name"],
        message=row["message"],
        verify=row["verify"],
        execute_success=row["execute_success"],
        exec_stdout=row["exec_stdout"],
        agent_reasoning=row["agent_reasoning"],
        agent_confidence=row["agent_confidence"],
        created_at=row["created_at"],
        completed_at=row["completed_at"],
    )
```

- [ ] **Step 4: 跑测试确认 pass**

- [ ] **Step 5: Commit**

```bash
git add src/hermes/models.py src/hermes/repository.py tests/test_hermes_repository.py
git commit -m "feat(hermes): repository save + find_similar (FTS)"
```

---

## Task 4: knowledge.py - 注入 prompt 用的 query 函数

**Files:**
- Create: `src/hermes/knowledge.py`
- Test: `tests/test_hermes_knowledge.py`

- [ ] **Step 1: 写测试**

`tests/test_hermes_knowledge.py`:

```python
"""knowledge.query_similar_cases 测试"""

import pytest
from datetime import datetime, timezone

from src.models import Alert
from src.hermes.knowledge import query_similar_cases, format_cases_for_prompt
from src.hermes.models import AuditRecordRead


def _alert(host_ip: str = "1.1.1.1", event_name: str = "Disk full") -> Alert:
    return Alert(
        event_id="x", event_name=event_name, severity="high",
        hostname="h", host_ip=host_ip, trigger_id="t", message="m",
        timestamp=datetime.now(timezone.utc), status="problem",
    )


def test_format_cases_empty():
    assert "no past cases" in format_cases_for_prompt([]).lower()


def test_format_cases_with_records():
    rec = AuditRecordRead(
        id=1, event_id="e1", workflow_id="wf", decision="approved",
        runbook_id="disk_cleanup", runbook_params={"path": "/tmp"},
        hostname="h", host_ip="1.1.1.1", severity="high",
        event_name="Disk full", message="tmp full",
        verify=True, execute_success=True, exec_stdout="",
        agent_reasoning="/tmp 95%", agent_confidence=0.9,
        created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    text = format_cases_for_prompt([rec])
    assert "disk_cleanup" in text
    assert "/tmp" in text
    assert "✅" in text  # verify=True 标记
    assert "Disk full" in text


def test_format_cases_marks_failed_attempts():
    """失败案例要明显标记，让 LLM 知道避坑"""
    rec = AuditRecordRead(
        id=1, event_id="e1", workflow_id="wf", decision="approved",
        runbook_id="service_restart", runbook_params={"service_name": "mysql"},
        hostname="h", host_ip="1.1.1.1", severity="high",
        event_name="mysql down", message="...",
        verify=False, execute_success=True, exec_stdout="",
        agent_reasoning="restart", agent_confidence=0.6,
        created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    text = format_cases_for_prompt([rec])
    assert "❌" in text or "fail" in text.lower()


@pytest.mark.asyncio
async def test_query_similar_cases_with_real_repo(db):
    """集成测试：真实 PG + 真实 query"""
    from src.hermes.repository import AuditRepository
    from src.hermes.models import AuditRecordWrite

    repo = AuditRepository(db.pool, schema="test_hermes")
    await repo.save(AuditRecordWrite(
        event_id="e1", workflow_id="wf", decision="approved",
        runbook_id="disk_cleanup", runbook_params={"path": "/tmp"},
        hostname="aiops-target", host_ip="192.168.198.130",
        severity="high", event_name="Disk usage > 90%", message="tmp full",
        verify=True, execute_success=True, exec_stdout="",
    ))

    cases = await query_similar_cases(
        repo,
        alert=_alert(host_ip="192.168.198.130", event_name="Disk usage > 90%"),
        limit=3,
    )
    assert len(cases) == 1
    assert cases[0].host_ip == "192.168.198.130"
```

- [ ] **Step 2: 跑测试确认 fail**

- [ ] **Step 3: 实现 knowledge**

`src/hermes/knowledge.py`:

```python
"""注入到 agent prompt 用的相似案例查询 + 格式化

借鉴 Hermes Agent 的 progressive disclosure：
- 先注入"案例列表 + 简短描述"（轻量，省 token）
- agent 觉得需要详情时，可以通过 tool 调 detail（Phase 8 实现）

性能要点：
- 单次 PG 查询 < 50ms（GIN 索引 + LIMIT 3）
- format 函数纯字符串拼接 O(n)，n=3 几乎零成本
- agent_diagnose 调用前同步 await，但延迟可接受
"""

from src.hermes.models import AuditRecordRead
from src.hermes.repository import AuditRepository
from src.models import Alert


async def query_similar_cases(
    repo: AuditRepository,
    alert: Alert,
    limit: int = 3,
) -> list[AuditRecordRead]:
    """根据 alert 内容找历史相似案例

    Query 策略：用 event_name + message 关键词全文搜，host_ip 加权排前。
    """
    query = f"{alert.event_name} {alert.message}"
    return await repo.find_similar(
        query=query,
        host_ip=alert.host_ip,
        limit=limit,
        only_successful=False,  # 失败案例也要看，让 agent 避坑
    )


def format_cases_for_prompt(cases: list[AuditRecordRead]) -> str:
    """把案例渲染成 markdown，注入 system prompt"""
    if not cases:
        return "_no past cases found_"

    lines = []
    for i, c in enumerate(cases, 1):
        verify_mark = "✅" if c.verify else ("❌" if c.verify is False else "?")
        # 简短模板，控制 token 数
        lines.append(
            f"{i}. [{c.created_at.strftime('%Y-%m-%d')}] "
            f"`{c.event_name}` on {c.host_ip}\n"
            f"   - decision: {c.decision} | runbook: `{c.runbook_id}` | params: {c.runbook_params or '{}'}\n"
            f"   - result: {verify_mark} verify={c.verify}\n"
            f"   - reasoning: {(c.agent_reasoning or 'n/a')[:200]}"
        )
    return "\n".join(lines)
```

- [ ] **Step 4: 跑测试确认 pass**

- [ ] **Step 5: Commit**

```bash
git add src/hermes/knowledge.py tests/test_hermes_knowledge.py
git commit -m "feat(hermes): query_similar_cases + prompt formatter"
```

---

## Task 5: agent_diagnose 注入 Past Experiences

**Files:**
- Modify: `src/llm/agent.py`
- Modify: `src/activities/llm.py`
- Modify: `tests/test_agent.py`

- [ ] **Step 1: 写测试**

```python
@pytest.mark.asyncio
async def test_agent_includes_past_cases_in_system_prompt():
    """注入相似案例到 system prompt 第一条 message"""
    from src.llm.agent import DiagnosticAgent
    from unittest.mock import AsyncMock

    client = AsyncMock()
    client.chat_with_tools.return_value = {
        "content": None,
        "tool_calls": [{
            "id": "x", "type": "function",
            "function": {"name": "propose_action", "arguments":
                '{"runbook_id":"disk_cleanup","params":{},"reasoning":"x","confidence":0.9,"risk_level":"low"}'},
        }],
    }

    past_cases_text = "1. [2026-05-01] `Disk usage > 90%` on 1.1.1.1\n   - decision: approved | runbook: disk_cleanup\n"
    agent = DiagnosticAgent(client, max_turns=2, past_cases_text=past_cases_text)
    await agent.diagnose(_alert())

    # 第一条 message 是 system prompt，里面应包含 past cases
    sent_messages = client.chat_with_tools.call_args.kwargs["messages"]
    sys_msg = sent_messages[0]
    assert sys_msg["role"] == "system"
    assert "Past Experiences" in sys_msg["content"] or "历史" in sys_msg["content"]
    assert "Disk usage > 90%" in sys_msg["content"]
```

- [ ] **Step 2: 改 agent.py 接收 past_cases_text**

`src/llm/agent.py`:

```python
SYSTEM_PROMPT_TEMPLATE = """你是 AIOps 诊断 Agent。收到告警后，你的目标是给出最合适的执行计划。

工作流程：
1. **先观察，再决策**：磁盘类告警必须先调 get_disk_usage 看哪个挂载点紧张，
   再调 get_directory_sizes 定位哪个目录占用最多。
   服务类告警先调 list_failed_services 或 get_service_status 确认服务真的挂了。
2. **基于事实**：propose_action 时的 reasoning 必须引用工具输出的事实。
3. **严格 schema**：propose_action 的 params 严格按 runbook schema。
4. **target_host 永远填告警里的 host_ip**。
5. **不要发明新字段**。

可用 Runbook：
- disk_cleanup: {target_host, path, min_age_days}
- service_restart: {target_host, service_name}
- none: {} 表示无合适修复

{past_cases_section}

可用工具：5 个诊断工具 + 1 个 propose_action 终止工具。
最多调 5 轮。
"""


PAST_CASES_SECTION_TEMPLATE = """

## 📚 Past Experiences (Hermes 知识层)

下面是过去对类似告警的处置历史。**仅供参考**，本次告警的实际处置方案以你
通过工具收集到的事实为准。注意带 ❌ 的失败案例——避免重复同样的错误。

{past_cases_text}
"""


class DiagnosticAgent:
    def __init__(
        self,
        llm_client: Any,
        max_turns: int = 5,
        timeout_per_call: float = 60,
        past_cases_text: str = "",
    ):
        self.llm = llm_client
        self.max_turns = max_turns
        self.timeout_per_call = timeout_per_call
        # 注入到 system prompt 的过去案例文本（已 format 好）
        self.past_cases_text = past_cases_text

    async def diagnose(self, alert: Alert) -> AgentResult:
        past_section = (
            PAST_CASES_SECTION_TEMPLATE.format(past_cases_text=self.past_cases_text)
            if self.past_cases_text and self.past_cases_text.strip()
            else ""
        )
        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(past_cases_section=past_section)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": _format_alert(alert)},
        ]
        # ... 余下不变 ...
```

- [ ] **Step 3: 改 activities/llm.py 在调 agent 之前查 hermes**

`src/activities/llm.py`:

```python
"""LLM Activity：注入历史经验"""

import json
import logging

from temporalio import activity

from src.hermes import knowledge as hermes_knowledge
from src.hermes.repository import AuditRepository
from src.llm.agent import AgentLimitExceeded, DiagnosticAgent
from src.models import Alert

logger = logging.getLogger(__name__)

llm_router = None
hermes_repo: AuditRepository | None = None  # main.py lifespan 注入


@activity.defn
async def agent_diagnose(alert_json: str) -> str:
    alert = Alert.model_validate_json(alert_json)
    if llm_router is None:
        raise RuntimeError("llm_router not initialized")

    # 1. 查 Hermes 历史经验（容错：失败不阻塞）
    past_cases_text = ""
    if hermes_repo is not None:
        try:
            cases = await hermes_knowledge.query_similar_cases(hermes_repo, alert, limit=3)
            past_cases_text = hermes_knowledge.format_cases_for_prompt(cases)
            logger.info(f"hermes injected {len(cases)} past cases for alert {alert.event_id}")
        except Exception as exc:
            logger.warning(f"hermes query failed, proceeding without past cases: {exc}")
            past_cases_text = ""

    client = llm_router.select_client_for_agent()
    agent = DiagnosticAgent(
        llm_client=client, max_turns=5, past_cases_text=past_cases_text,
    )

    try:
        result = await agent.diagnose(alert)
    except AgentLimitExceeded:
        return json.dumps({"plan": None, "trace": [], "agent_failed": True})

    plan_dict = result.plan.model_dump() if result.plan else None
    return json.dumps({"plan": plan_dict, "trace": result.trace}, ensure_ascii=False)
```

- [ ] **Step 4: 跑测试确认 pass**

- [ ] **Step 5: Commit**

```bash
git add src/llm/agent.py src/activities/llm.py tests/test_agent.py
git commit -m "feat(hermes): inject past cases into agent system prompt"
```

---

## Task 6: 双写 audit (JSONL + PG)

**Files:**
- Modify: `src/activities/audit.py`
- Modify: `tests/test_audit.py`

- [ ] **Step 1: 写测试**

```python
@pytest.mark.asyncio
async def test_write_audit_writes_to_pg_and_jsonl(tmp_path, monkeypatch, db):
    """同时写 JSONL（兜底）和 PG（hermes）"""
    from src.hermes.repository import AuditRepository
    from src.activities import audit as audit_mod

    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(audit_mod.settings, "audit_log_path", str(audit_path))
    repo = AuditRepository(db.pool, schema="test_hermes")
    monkeypatch.setattr(audit_mod, "_repo", repo)

    alert_json = Alert(
        event_id="e1", event_name="Disk full", severity="high",
        hostname="h", host_ip="1.1.1.1", trigger_id="t", message="m",
        timestamp="2026-05-09T10:00:00Z", status="problem",
    ).model_dump_json()

    await audit_mod.write_audit(
        alert_json, "wf-1", "approved", "disk_cleanup",
        '{"path":"/tmp"}', None, "msg-1",
    )

    # JSONL 文件有一行
    assert audit_path.exists()
    assert len(audit_path.read_text().splitlines()) == 1

    # PG 表有一行
    count = await repo.count_total()
    assert count == 1


@pytest.mark.asyncio
async def test_write_audit_pg_failure_fallback_to_jsonl(tmp_path, monkeypatch):
    """PG 挂了时不影响 JSONL 写入"""
    from src.activities import audit as audit_mod

    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(audit_mod.settings, "audit_log_path", str(audit_path))

    class BrokenRepo:
        async def save(self, *args, **kwargs):
            raise RuntimeError("PG down")

    monkeypatch.setattr(audit_mod, "_repo", BrokenRepo())

    alert_json = Alert(
        event_id="e1", event_name="x", severity="high",
        hostname="h", host_ip="1.1.1.1", trigger_id="t", message="m",
        timestamp="2026-05-09T10:00:00Z", status="problem",
    ).model_dump_json()

    # 不应抛错
    await audit_mod.write_audit(alert_json, "wf-1", "approved", None, None, None, None)

    # JSONL 还是写了
    assert audit_path.exists()
    assert len(audit_path.read_text().splitlines()) == 1
```

- [ ] **Step 2: 改 audit.py**

```python
"""审计写入：双写 JSONL（兜底）+ PG（Hermes 知识层）"""

import datetime as dt
import json
import logging
from pathlib import Path

from temporalio import activity

from src.config import settings
from src.hermes.models import AuditRecordWrite
from src.hermes.repository import AuditRepository
from src.models import Alert, AuditRecord, ExecutionResult

logger = logging.getLogger(__name__)

# 由 main.py lifespan 注入；None 时降级为只写 JSONL
_repo: AuditRepository | None = None


def set_repo(repo: AuditRepository | None) -> None:
    global _repo
    _repo = repo


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
    alert = Alert.model_validate_json(alert_json)
    execution_result = (
        ExecutionResult.model_validate_json(execution_result_json)
        if execution_result_json else None
    )
    runbook_params = json.loads(runbook_params_json) if runbook_params_json else None

    record = AuditRecord(
        alert=alert,
        workflow_id=workflow_id,
        decision=decision,
        runbook_id=runbook_id,
        runbook_params=runbook_params,
        execution_result=execution_result,
        feishu_message_id=feishu_message_id,
        created_at=dt.datetime.now(dt.UTC),
        completed_at=dt.datetime.now(dt.UTC),
    )

    # ① JSONL 兜底（永远写）
    audit_json = record.model_dump_json()
    audit_path = Path(settings.audit_log_path)
    if audit_path.parent != Path("."):
        audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as f:
        f.write(audit_json + "\n")

    # ② PG 写入（Hermes 知识层；失败不阻塞）
    if _repo is not None:
        try:
            verify = execution_result.verify if execution_result else None
            execute_success = execution_result.execute.success if execution_result else None
            stdout = execution_result.execute.stdout if execution_result else None
            await _repo.save(AuditRecordWrite(
                event_id=alert.event_id,
                workflow_id=workflow_id,
                decision=decision,
                runbook_id=runbook_id,
                runbook_params=runbook_params,
                hostname=alert.hostname,
                host_ip=alert.host_ip,
                severity=alert.severity,
                event_name=alert.event_name,
                message=alert.message,
                verify=verify,
                execute_success=execute_success,
                exec_stdout=stdout,
            ))
        except Exception as exc:
            logger.warning(f"hermes audit write failed (jsonl is fine): {exc}")

    print(f"[AUDIT] {record.alert.event_id} | {record.decision} | {record.runbook_id}")
    return audit_json
```

- [ ] **Step 3: 跑测试确认 pass**

- [ ] **Step 4: Commit**

```bash
git add src/activities/audit.py tests/test_audit.py
git commit -m "feat(hermes): dual-write audit (JSONL fallback + PG knowledge layer)"
```

---

## Task 7: main.py lifespan + .env + 文档

**Files:**
- Modify: `src/main.py`, `src/config.py`, `.env.example`
- Create: `docs/hermes-knowledge.md`

- [ ] **Step 1: 改 config.py**

```python
hermes_db_url: str = "postgresql://temporal:temporal@postgres:5432/temporal"
```

- [ ] **Step 2: 改 main.py lifespan 初始化 Hermes**

```python
# main.py：
from src.hermes.db import HermesDB
from src.hermes.repository import AuditRepository
import src.activities.audit as audit_mod
import src.activities.llm as llm_mod

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ...原有代码...

    # Hermes 初始化（失败时降级，不阻塞主流程）
    hermes_db = HermesDB(dsn=settings.hermes_db_url)
    try:
        await hermes_db.connect()
        await hermes_db.init_schema()
        repo = AuditRepository(hermes_db.pool)
        audit_mod.set_repo(repo)
        llm_mod.hermes_repo = repo
        logger.info("Hermes knowledge layer ready")
    except Exception as exc:
        logger.warning(f"Hermes init failed, running without knowledge layer: {exc}")
        hermes_db = None

    # ...

    yield

    if hermes_db is not None:
        await hermes_db.close()
```

- [ ] **Step 3: .env.example 加 HERMES_DB_URL**

```bash
# Phase 7: Hermes 知识层
HERMES_DB_URL=postgresql://temporal:temporal@postgres:5432/temporal
```

- [ ] **Step 4: 写 docs/hermes-knowledge.md**

```markdown
# Hermes 知识层操作手册

> 配套 [生产级 AIOps 架构设计](生产级 AIOps 架构设计.md) §2 + §11

## 工作原理

每次 agent 诊断前：

```
agent_diagnose(alert)
  ├─ ① Hermes.query_similar_cases(alert) → Top-3 历史案例
  ├─ ② 注入到 system prompt 的 "Past Experiences" 段
  └─ ③ ReAct 多轮循环（同前）
```

每次 workflow 完成后：

```
write_audit
  ├─ JSONL（兜底，永远写）
  └─ PG audit_records（Hermes，失败不阻塞）
```

## Schema

audit_records 表字段全集见 [src/hermes/schema.sql](../src/hermes/schema.sql)。

关键字段：
- `event_name` / `message` / `hostname` 进 tsvector → 全文搜索
- `verify` 字段标记最终是否真修好
- `agent_reasoning` 存 agent 当时的推理（Phase 8 用）

## 检索

```sql
-- 看过去 1 周的成功案例
SELECT created_at, host_ip, event_name, runbook_id, decision
FROM audit_records
WHERE verify = true AND created_at > NOW() - INTERVAL '7 days'
ORDER BY created_at DESC;

-- 找类似 alert 的案例
SELECT *, ts_rank(fts, to_tsquery('simple', 'disk | tmp')) AS rank
FROM audit_records
WHERE fts @@ to_tsquery('simple', 'disk | tmp')
ORDER BY rank DESC LIMIT 5;
```

## 性能调优

| 现象 | 原因 | 修法 |
|---|---|---|
| 查询慢 (> 200ms) | GIN 索引未命中 | `EXPLAIN ANALYZE` 看是否用 idx_audit_fts |
| 表过大 | 历史数据无 TTL | 加分区表或定期 archive 一年前的 |
| 失败案例噪声 | 多次重试都失败的告警 | `find_similar(only_successful=True)` |

## 关闭知识层（紧急回滚）

`.env` 设 `HERMES_DB_URL=` 留空，重启后 lifespan init 会跳过，audit 只写 JSONL，agent 不注入历史。
```

- [ ] **Step 5: Commit**

```bash
git add src/main.py src/config.py .env.example docs/hermes-knowledge.md
git commit -m "feat(hermes): wire into lifespan + ops manual"
```

---

## Task 8: 端到端验证 + 性能基准

**Files:** 无代码修改

- [ ] **Step 1: 启动 + 验证 schema**

```bash
sudo docker compose up -d --build aiops
sudo docker compose logs aiops --tail 30 | grep -i hermes
# 期望: "Hermes knowledge layer ready"

# 直接连 PG 看表
sudo docker compose exec postgres psql -U temporal -d temporal \
    -c "\d audit_records"
```

- [ ] **Step 2: 触发几次告警，看 PG 写入**

```bash
# 触发 3 次 fill-disk
for i in 1 2 3; do
    ssh lijianqiao@192.168.198.130 "sudo bash /opt/demo-scripts/fill-disk.sh 3500"
    # 等 workflow 完成（飞书审批 + 执行）
    sleep 60
done

# 验证 PG
sudo docker compose exec postgres psql -U temporal -d temporal \
    -c "SELECT event_id, decision, verify, host_ip FROM audit_records ORDER BY created_at DESC LIMIT 5;"
```

- [ ] **Step 3: 第 4 次告警，验证 agent 注入历史**

```bash
ssh lijianqiao@192.168.198.130 "sudo bash /opt/demo-scripts/fill-disk.sh 3500"

# 看 agent activity 日志
sudo docker compose logs aiops --tail 100 | grep -i "hermes injected"
# 期望: "hermes injected 3 past cases for alert ..."

# 飞书卡片应显示 agent 引用历史的 reasoning（"参考历史 ..."）
```

- [ ] **Step 4: 性能基准（参考 python-performance-optimization Pattern 1 cProfile）**

```bash
sudo docker compose exec aiops python -c "
import asyncio, time
from src.hermes.db import HermesDB
from src.hermes.repository import AuditRepository
from src.config import settings

async def main():
    db = HermesDB(settings.hermes_db_url)
    await db.connect()
    repo = AuditRepository(db.pool)

    # 100 次查询 benchmark
    start = time.time()
    for _ in range(100):
        await repo.find_similar('disk usage tmp', host_ip='192.168.198.130', limit=3)
    elapsed = time.time() - start
    print(f'100x find_similar: {elapsed:.2f}s, avg {elapsed*10:.1f}ms/query')

    await db.close()

asyncio.run(main())
"
```

期望：avg < 50ms/query。如果超过 100ms：
- `EXPLAIN ANALYZE` 看是否用 GIN 索引
- 检查表大小，> 100k 行时考虑加分区

---

## Done definition

- [ ] 全套测试 PASS（约 12 个新增）
- [ ] PG audit_records 表存在 + GIN 索引存在
- [ ] 告警触发后 PG 表多 1 行
- [ ] agent_diagnose 日志显示 "hermes injected N past cases"
- [ ] 第 N 次同类告警的飞书卡片，agent 推理引用了历史案例
- [ ] PG 故意挂掉（`docker compose stop postgres`）时，告警仍能正常处理（只是不写 PG，JSONL 还在写，agent 没历史可注入）
- [ ] find_similar avg < 50ms/query
