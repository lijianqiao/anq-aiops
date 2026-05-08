# Phase 4 多 Agent 协同 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 多条告警同时进来时，AIOps 能正确**关联同根因 + 抑制衍生告警 + 互斥锁防并发误操作 + 风暴限流**——单条 alert 时行为完全跟现状一致，零回归。

**Architecture:** 三件事相对独立，组合起来覆盖 §6 全部要点：
1. **Alert Correlator**（启发式快筛 → LLM 补判）放在 webhook → workflow 之间，30s 时间窗口聚合
2. **Action Mutex**（Redis 分布式锁）放在 workflow 执行 runbook 前，同 host 串行
3. **风暴限流 + 系统过载保护**（令牌桶 + pending workflow 计数）放在 webhook 入口

**Tech Stack:** Redis (已有) + 现有 Temporal/Pydantic 模型，无新依赖

**Spec:** [docs/生产级 AIOps 架构设计.md](../../生产级 AIOps 架构设计.md) §6 多 Agent 协同 + §9.6 告警风暴防护

---

## 设计权衡

### 为什么不给 Alert Correlator 引入独立服务

架构文档画的 Alert Correlator 是单独一层，但实际上对内网告警量（< 100/天）完全没必要。**直接做成一个 module，集成到 consumer.py 里**：

```
旧:  webhook → produce_alert → redis stream → consume → start_workflow
新:  webhook → produce_alert → redis stream → consume → 关联判断 → start_workflow / 加入现有组
```

少一个进程少一份故障源。如果将来告警量真的上去了再拆服务。

### 启发式快筛覆盖率目标

参考 §6.2，启发式快筛要覆盖 **80% 明确场景**，LLM 调用降到 20%。具体规则：

| 规则 | verdict | 案例 |
|---|---|---|
| 时间差 > 5 分钟 | `definitely_not` | 30s 窗口内不可能 |
| 同 IP 或同 hostname | `definitely_related` | 同机多告警必关联 |
| 不同子网 + 不同服务名 | `definitely_not` | 跨数据中心独立 |
| 其它 | `uncertain` | 交给 LLM |

### 性能优化重点（参考 python-performance-optimization）

1. **快筛用 set/dict 查找而不是 list**：`group_index_by_host: dict[str, AlertGroup]` 而不是遍历 list（O(1) vs O(n)）
2. **避免重复创建 Pydantic 实例**：alert 对象在关联期间复用同一个 instance
3. **LLM 调用用 cache**：相同的 (group_summary, new_alert_fingerprint) → 相同结果，用 `lru_cache(maxsize=128)` 减少重复 token 消耗
4. **Redis lock 用 SET NX EX**：避免 GET-then-SET race，单 round-trip
5. **风暴检测用 Redis INCR + EXPIRE**：经典令牌桶，不在 Python 层数字典

### 反过度设计

- **不引入 CMDB 拓扑**：架构文档明确说 LLM 依靠告警文本就能判断 80% 关联（§6.2 末段），CMDB 是可选增强。先不接。
- **不做衍生告警重启**：架构文档 §6.5 说"未恢复则重新评估，可能升级为新的独立告警"——你这场景规模小，先简化为"衍生告警永远抑制不重发"。后续 Phase 8 再补。

---

## File Structure

| 文件 | 操作 | 职责 |
|---|---|---|
| `src/correlator/__init__.py` | 新建 | 导出 `correlate / AlertGroup` |
| `src/correlator/quick_filter.py` | 新建 | 启发式 4 条规则 |
| `src/correlator/llm_judge.py` | 新建 | LLM 关联判断（带 lru_cache） |
| `src/correlator/groups.py` | 新建 | `AlertGroup` 状态机 + Redis 持久化 |
| `src/correlator/correlate.py` | 新建 | 主入口 `correlate(alert) → AlertGroup` |
| `src/coordination/__init__.py` | 新建 | 导出 `acquire_action_mutex / RateLimiter` |
| `src/coordination/mutex.py` | 新建 | Redis 分布式锁（SET NX EX） |
| `src/coordination/rate_limit.py` | 新建 | 令牌桶 + pending workflow 计数 |
| `src/bus/consumer.py` | 修改 | 调 correlate + rate_limit |
| `src/api/webhook.py` | 修改 | 调 rate_limit 在 produce_alert 前 |
| `src/workflows/alert_workflow.py` | 修改 | execute_runbook 前加 mutex |
| `src/models.py` | 修改 | 加 `AlertGroup` Pydantic 模型 |
| `src/config.py` | 修改 | 加 correlator/rate_limit/mutex 配置 |
| `tests/test_correlator.py` | 新建 | quick_filter / llm_judge / correlate 测试 |
| `tests/test_coordination.py` | 新建 | mutex / rate_limit 测试 |
| `tests/test_workflow.py` | 修改 | mutex 集成测试 |
| `.env.example` | 修改 | 加配置项 |
| `docs/multi-agent-coordination.md` | 新建 | 运维手册 |

---

## Task 1: AlertGroup 数据模型

**Files:**
- Modify: `src/models.py`
- Test: `tests/test_correlator.py`

- [ ] **Step 1: 写测试**

`tests/test_correlator.py` (新建):

```python
"""Alert correlator 测试套件"""

import pytest
from datetime import datetime, timezone, timedelta

from src.models import Alert, AlertGroup


def _alert(event_id: str = "1", host_ip: str = "192.168.1.10",
           event_name: str = "Disk usage > 90%", t: datetime | None = None) -> Alert:
    return Alert(
        event_id=event_id,
        event_name=event_name,
        severity="high",
        hostname=f"host-{host_ip}",
        host_ip=host_ip,
        trigger_id=event_id,
        message="test",
        timestamp=t or datetime.now(timezone.utc),
        status="problem",
    )


def test_alert_group_initial_state():
    """新建 group 必有 root，role 默认 root"""
    root = _alert("1")
    g = AlertGroup(root_alert=root)
    assert g.root_alert.event_id == "1"
    assert g.derived_alerts == []
    assert g.created_at is not None


def test_alert_group_add_derived():
    """加衍生告警后能正确读取"""
    root = _alert("1")
    g = AlertGroup(root_alert=root)
    g.derived_alerts.append(_alert("2"))
    assert len(g.derived_alerts) == 1
    assert g.derived_alerts[0].event_id == "2"


def test_alert_group_summary_text():
    """group.summary() 返回简短描述用于 LLM prompt"""
    root = _alert("1", host_ip="10.0.0.1", event_name="Disk full")
    g = AlertGroup(root_alert=root)
    g.derived_alerts.append(_alert("2", host_ip="10.0.0.1", event_name="App down"))
    s = g.summary()
    assert "10.0.0.1" in s
    assert "Disk full" in s
    assert "App down" in s


def test_alert_group_serialization_roundtrip():
    """Pydantic JSON 序列化 + 反序列化（Redis 存取必须）"""
    root = _alert("1")
    g = AlertGroup(root_alert=root)
    g.derived_alerts.append(_alert("2"))
    j = g.model_dump_json()
    restored = AlertGroup.model_validate_json(j)
    assert restored.root_alert.event_id == "1"
    assert restored.derived_alerts[0].event_id == "2"
```

- [ ] **Step 2: 跑测试确认 fail**

```bash
.venv/Scripts/python.exe -m pytest tests/test_correlator.py -v
```

- [ ] **Step 3: 实现 AlertGroup**

`src/models.py` 末尾追加：

```python
class AlertGroup(BaseModel):
    """告警关联组：根因告警 + 衍生告警

    Redis key 用 group_id（root_alert.event_id）做主键。
    """

    root_alert: Alert
    derived_alerts: list[Alert] = []
    created_at: datetime = None  # type: ignore  # 默认 now() 在 model_post_init 设

    def model_post_init(self, _ctx: dict) -> None:
        if self.created_at is None:
            self.created_at = datetime.now()

    @property
    def group_id(self) -> str:
        return self.root_alert.event_id

    @property
    def root_host(self) -> str:
        return self.root_alert.host_ip

    def summary(self) -> str:
        """简短描述用于 LLM 关联判断 prompt"""
        lines = [f"[ROOT] {self.root_alert.host_ip} {self.root_alert.event_name}"]
        for a in self.derived_alerts:
            lines.append(f"[DERIVED] {a.host_ip} {a.event_name}")
        return "\n".join(lines)
```

- [ ] **Step 4: 跑测试确认 pass**

- [ ] **Step 5: Commit**

```bash
git add src/models.py tests/test_correlator.py
git commit -m "feat(correlator): AlertGroup pydantic model + summary"
```

---

## Task 2: 启发式快筛

**Files:**
- Create: `src/correlator/__init__.py`, `src/correlator/quick_filter.py`
- Modify: `tests/test_correlator.py`

- [ ] **Step 1: 写测试**

追加到 `tests/test_correlator.py`:

```python
from src.correlator.quick_filter import quick_filter, Verdict


def test_quick_filter_definitely_not_when_time_diff_over_5min():
    new = _alert("99", t=datetime.now(timezone.utc))
    root = _alert("1", t=datetime.now(timezone.utc) - timedelta(minutes=10))
    g = AlertGroup(root_alert=root)
    assert quick_filter(new, g) == Verdict.DEFINITELY_NOT


def test_quick_filter_definitely_related_same_ip():
    new = _alert("99", host_ip="10.0.0.1")
    root = _alert("1", host_ip="10.0.0.1")
    g = AlertGroup(root_alert=root)
    assert quick_filter(new, g) == Verdict.DEFINITELY_RELATED


def test_quick_filter_definitely_not_different_subnet_and_service():
    new = _alert("99", host_ip="172.16.0.1", event_name="redis OOM")
    root = _alert("1", host_ip="10.0.0.1", event_name="nginx down")
    g = AlertGroup(root_alert=root)
    assert quick_filter(new, g) == Verdict.DEFINITELY_NOT


def test_quick_filter_uncertain_otherwise():
    """同子网但不同主机 + 不同服务 → 交给 LLM"""
    new = _alert("99", host_ip="10.0.0.1", event_name="nginx down")
    root = _alert("1", host_ip="10.0.0.2", event_name="redis OOM")
    g = AlertGroup(root_alert=root)
    assert quick_filter(new, g) == Verdict.UNCERTAIN
```

- [ ] **Step 2: 跑测试确认 fail**

- [ ] **Step 3: 实现 quick_filter**

`src/correlator/__init__.py` (空占位):
```python
"""Alert correlator: 启发式快筛 + LLM 兜底关联判断"""
```

`src/correlator/quick_filter.py`:

```python
"""启发式快筛 - 不调 LLM，毫秒级返回

性能要点（python-performance-optimization）：
- 4 条 if-elif 串行，平均 < 100us
- 字符串比较用 `==` 不用 `re.match`（避免 regex 编译开销）
- 子网判断用 IP 前 16-bit 比较，避开 ipaddress 模块开销
"""

from datetime import timedelta
from enum import Enum

from src.models import Alert, AlertGroup


class Verdict(str, Enum):
    DEFINITELY_RELATED = "definitely_related"
    DEFINITELY_NOT = "definitely_not"
    UNCERTAIN = "uncertain"


_TIME_WINDOW = timedelta(minutes=5)


def _same_subnet_16(ip_a: str, ip_b: str) -> bool:
    """前两段相同则认为同 /16 子网（启发式，不严格）"""
    parts_a = ip_a.split(".")
    parts_b = ip_b.split(".")
    if len(parts_a) < 2 or len(parts_b) < 2:
        return False
    return parts_a[0] == parts_b[0] and parts_a[1] == parts_b[1]


def quick_filter(alert: Alert, group: AlertGroup) -> Verdict:
    """4 条规则覆盖 80% 明确场景"""
    root = group.root_alert

    # 规则 1: 时间差 > 5 分钟 → 独立
    if abs(alert.timestamp - root.timestamp) > _TIME_WINDOW:
        return Verdict.DEFINITELY_NOT

    # 规则 2: 同 IP/hostname → 必相关
    if alert.host_ip == root.host_ip or alert.hostname == root.hostname:
        return Verdict.DEFINITELY_RELATED

    # 规则 3: 不同子网 + 不同服务关键词 → 独立
    name_a = alert.event_name.lower()
    name_b = root.event_name.lower()
    if not _same_subnet_16(alert.host_ip, root.host_ip):
        # 用 event_name 第一个词做粗略服务名比较
        first_a = name_a.split()[0] if name_a else ""
        first_b = name_b.split()[0] if name_b else ""
        if first_a != first_b:
            return Verdict.DEFINITELY_NOT

    # 其它情况交 LLM
    return Verdict.UNCERTAIN
```

- [ ] **Step 4: 跑测试确认 pass**

- [ ] **Step 5: Commit**

```bash
git add src/correlator/__init__.py src/correlator/quick_filter.py tests/test_correlator.py
git commit -m "feat(correlator): heuristic quick_filter (4 rules, sub-ms)"
```

---

## Task 3: LLM 关联判断（带缓存）

**Files:**
- Create: `src/correlator/llm_judge.py`
- Modify: `tests/test_correlator.py`

- [ ] **Step 1: 写测试**

```python
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_llm_judge_returns_related_or_not():
    """LLM 返回 {"related": true} → True"""
    from src.correlator.llm_judge import llm_judge

    with patch("src.correlator.llm_judge._call_llm", new=AsyncMock(return_value={
        "related": True,
        "reason": "服务调用关系",
    })):
        ok, reason = await llm_judge(
            alert_summary="cache-01 redis OOM",
            group_summary="db-01 mysql conn timeout",
        )
    assert ok is True
    assert "服务" in reason


@pytest.mark.asyncio
async def test_llm_judge_cache_hit_avoids_llm_call(monkeypatch):
    """同 (alert_summary, group_summary) 第二次调用走缓存"""
    from src.correlator import llm_judge as mod

    call_count = {"n": 0}

    async def counting_call(*args, **kwargs):
        call_count["n"] += 1
        return {"related": False, "reason": "无关"}

    monkeypatch.setattr(mod, "_call_llm", counting_call)
    mod.llm_judge.cache_clear()

    await mod.llm_judge("a", "b")
    await mod.llm_judge("a", "b")  # 应走 cache
    await mod.llm_judge("a", "c")  # 不同 input，新调用

    assert call_count["n"] == 2  # a/b 1 次 + a/c 1 次（a/b 第 2 次走 cache）


@pytest.mark.asyncio
async def test_llm_judge_returns_safe_default_on_error(monkeypatch):
    """LLM 抛错时安全 default：not related（保守，避免误合并）"""
    from src.correlator.llm_judge import llm_judge
    from src.correlator import llm_judge as mod

    async def failing(*args, **kwargs):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(mod, "_call_llm", failing)
    mod.llm_judge.cache_clear()

    ok, reason = await llm_judge("x", "y")
    assert ok is False
    assert "fallback" in reason.lower() or "error" in reason.lower()
```

- [ ] **Step 2: 跑测试确认 fail**

- [ ] **Step 3: 实现 llm_judge**

`src/correlator/llm_judge.py`:

```python
"""LLM 关联判断（带 async-cache 优化）

性能要点（python-performance-optimization）：
- async_lru_cache 实现：用 dict 手动缓存（functools.lru_cache 不支持 async）
- LLM 调用错误时返回 (False, "...")，保守避免错合并 → 错误的 group 状态
- 缓存 key 是 (alert_summary, group_summary) 字符串元组
- 缓存大小 128 条（一天告警量 < 100，足够覆盖）
"""

import functools
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# 简单 LRU async cache（functools.lru_cache 不支持 coroutine）
_CACHE: dict[tuple[str, str], tuple[bool, str]] = {}
_CACHE_MAX = 128


def _cache_get(key: tuple[str, str]) -> tuple[bool, str] | None:
    return _CACHE.get(key)


def _cache_put(key: tuple[str, str], value: tuple[bool, str]) -> None:
    if len(_CACHE) >= _CACHE_MAX:
        # 简单 LRU：删第一个（dict 在 Python 3.7+ 保持插入顺序）
        first_key = next(iter(_CACHE))
        del _CACHE[first_key]
    _CACHE[key] = value


def _cache_clear() -> None:
    _CACHE.clear()


async def _call_llm(prompt: str) -> dict[str, Any]:
    """调主 LLM router，返回 dict {related: bool, reason: str}

    单独抽出来方便 mock。
    """
    import src.activities.llm as llm_activities  # 延迟 import，避免循环

    if llm_activities.llm_router is None:
        raise RuntimeError("llm_router not initialized")

    from pydantic import BaseModel

    class Result(BaseModel):
        related: bool
        reason: str = ""

    result = await llm_activities.llm_router.invoke(prompt, Result)
    return {"related": result.related, "reason": result.reason}


async def llm_judge(alert_summary: str, group_summary: str) -> tuple[bool, str]:
    """判断 alert 是否与 group 同根因

    Returns:
        (related, reason)
    """
    cache_key = (alert_summary, group_summary)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    prompt = f"""判断下面新告警是否与已有告警组属于同一根因（30 秒时间窗口内）。

已有告警组：
{group_summary}

新告警：
{alert_summary}

返回 JSON: {{"related": true/false, "reason": "..."}}
"""
    try:
        data = await _call_llm(prompt)
        result = (bool(data.get("related")), str(data.get("reason", ""))[:200])
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"llm_judge fallback to not-related due to: {exc}")
        result = (False, f"llm error fallback: {exc}")

    _cache_put(cache_key, result)
    return result


# 暴露 cache_clear 给测试用
llm_judge.cache_clear = _cache_clear  # type: ignore[attr-defined]
```

- [ ] **Step 4: 跑测试确认 pass**

- [ ] **Step 5: Commit**

```bash
git add src/correlator/llm_judge.py tests/test_correlator.py
git commit -m "feat(correlator): LLM judge with async LRU cache"
```

---

## Task 4: Group 状态持久化（Redis）

**Files:**
- Create: `src/correlator/groups.py`
- Modify: `tests/test_correlator.py`

- [ ] **Step 1: 写测试**

```python
@pytest.mark.asyncio
async def test_group_store_save_and_get():
    from src.correlator.groups import GroupStore
    import redis.asyncio as aioredis

    redis = aioredis.from_url("redis://localhost:6379/15")  # test DB
    await redis.flushdb()
    store = GroupStore(redis)

    root = _alert("1", host_ip="10.0.0.1")
    g = AlertGroup(root_alert=root)
    await store.save(g)

    fetched = await store.get(g.group_id)
    assert fetched is not None
    assert fetched.root_alert.event_id == "1"

    await redis.aclose()


@pytest.mark.asyncio
async def test_group_store_active_groups_within_window():
    """active_groups 只返回 30s 内创建的（避免历史污染）"""
    from src.correlator.groups import GroupStore
    import redis.asyncio as aioredis

    redis = aioredis.from_url("redis://localhost:6379/15")
    await redis.flushdb()
    store = GroupStore(redis, window_sec=30)

    g1 = AlertGroup(root_alert=_alert("1"))
    await store.save(g1)

    active = await store.active_groups()
    assert len(active) == 1
    assert active[0].group_id == "1"

    await redis.aclose()


@pytest.mark.asyncio
async def test_group_store_ttl_expires():
    """超出窗口的 group 应被 Redis TTL 自动清理"""
    from src.correlator.groups import GroupStore
    import redis.asyncio as aioredis

    redis = aioredis.from_url("redis://localhost:6379/15")
    await redis.flushdb()
    store = GroupStore(redis, window_sec=1)  # 1s for fast test

    await store.save(AlertGroup(root_alert=_alert("1")))

    import asyncio
    await asyncio.sleep(2)

    active = await store.active_groups()
    assert active == []
    await redis.aclose()
```

- [ ] **Step 2: 跑测试确认 fail**

- [ ] **Step 3: 实现 GroupStore**

`src/correlator/groups.py`:

```python
"""AlertGroup Redis 存储

Redis schema:
  Key:   correlator:group:<group_id>     Type: string (Pydantic JSON)  TTL: window_sec
  Key:   correlator:groups:active        Type: set    Members: group_id
                                                       TTL: 自动按 group 过期

性能要点：
- group JSON 直接存 string 而不是 hash，单 GET 拿全数据
- active group 列表用 set，pipeline 批量取 JSON 一次往返
- TTL 由 Redis 自动管理，不需要应用层定时清理
"""

import time
from typing import Iterable

import redis.asyncio as aioredis

from src.models import AlertGroup


class GroupStore:
    """AlertGroup Redis 持久化"""

    def __init__(self, redis: aioredis.Redis, window_sec: int = 30) -> None:
        self.redis = redis
        self.window_sec = window_sec

    @staticmethod
    def _group_key(group_id: str) -> str:
        return f"correlator:group:{group_id}"

    _ACTIVE_SET = "correlator:groups:active"

    async def save(self, group: AlertGroup) -> None:
        """保存 group，TTL = window_sec"""
        key = self._group_key(group.group_id)
        async with self.redis.pipeline(transaction=False) as pipe:
            pipe.set(key, group.model_dump_json(), ex=self.window_sec)
            pipe.sadd(self._ACTIVE_SET, group.group_id)
            pipe.expire(self._ACTIVE_SET, self.window_sec)
            await pipe.execute()

    async def get(self, group_id: str) -> AlertGroup | None:
        raw = await self.redis.get(self._group_key(group_id))
        if raw is None:
            return None
        return AlertGroup.model_validate_json(raw)

    async def active_groups(self) -> list[AlertGroup]:
        """返回当前窗口内所有活跃 group（Redis pipeline 一次取完）"""
        ids: Iterable[bytes] = await self.redis.smembers(self._ACTIVE_SET)
        if not ids:
            return []
        keys = [self._group_key(i.decode() if isinstance(i, bytes) else i) for i in ids]

        async with self.redis.pipeline(transaction=False) as pipe:
            for k in keys:
                pipe.get(k)
            raws = await pipe.execute()

        result: list[AlertGroup] = []
        for raw, gid in zip(raws, ids):
            if raw is None:
                # group 已 TTL 过期但 set 没同步清理，顺手清
                gid_str = gid.decode() if isinstance(gid, bytes) else gid
                await self.redis.srem(self._ACTIVE_SET, gid_str)
                continue
            result.append(AlertGroup.model_validate_json(raw))
        return result
```

- [ ] **Step 4: 跑测试确认 pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_correlator.py -v -k "store"
```

需要本地 Redis 跑（端口 6379）。

- [ ] **Step 5: Commit**

```bash
git add src/correlator/groups.py tests/test_correlator.py
git commit -m "feat(correlator): GroupStore with Redis TTL + pipeline"
```

---

## Task 5: correlate 主入口

**Files:**
- Create: `src/correlator/correlate.py`
- Modify: `tests/test_correlator.py`

- [ ] **Step 1: 写测试**

```python
@pytest.mark.asyncio
async def test_correlate_creates_new_group_when_no_active():
    from src.correlator.correlate import correlate
    from src.correlator.groups import GroupStore
    import redis.asyncio as aioredis

    redis = aioredis.from_url("redis://localhost:6379/15")
    await redis.flushdb()
    store = GroupStore(redis)

    g = await correlate(_alert("1"), store)
    assert g.group_id == "1"
    assert g.derived_alerts == []
    await redis.aclose()


@pytest.mark.asyncio
async def test_correlate_attaches_to_existing_group_same_host(monkeypatch):
    """同 host → quick_filter 判定 related → 加入现有 group"""
    from src.correlator.correlate import correlate
    from src.correlator.groups import GroupStore
    import redis.asyncio as aioredis

    redis = aioredis.from_url("redis://localhost:6379/15")
    await redis.flushdb()
    store = GroupStore(redis)

    g1 = await correlate(_alert("1", host_ip="10.0.0.1"), store)
    g2 = await correlate(_alert("2", host_ip="10.0.0.1"), store)

    assert g1.group_id == g2.group_id  # 同 group
    assert len(g2.derived_alerts) == 1
    assert g2.derived_alerts[0].event_id == "2"
    await redis.aclose()


@pytest.mark.asyncio
async def test_correlate_creates_independent_group_unrelated_hosts():
    from src.correlator.correlate import correlate
    from src.correlator.groups import GroupStore
    import redis.asyncio as aioredis

    redis = aioredis.from_url("redis://localhost:6379/15")
    await redis.flushdb()
    store = GroupStore(redis)

    g1 = await correlate(_alert("1", host_ip="10.0.0.1", event_name="nginx down"), store)
    g2 = await correlate(_alert("2", host_ip="172.16.0.1", event_name="redis OOM"), store)

    assert g1.group_id != g2.group_id  # 独立
    await redis.aclose()


@pytest.mark.asyncio
async def test_correlate_uses_llm_for_uncertain(monkeypatch):
    """quick_filter 返回 UNCERTAIN 时调 LLM"""
    from src.correlator import correlate as corr_mod
    from src.correlator.groups import GroupStore
    import redis.asyncio as aioredis

    redis = aioredis.from_url("redis://localhost:6379/15")
    await redis.flushdb()
    store = GroupStore(redis)

    async def fake_judge(*args, **kwargs):
        return (True, "LLM 判定关联")

    monkeypatch.setattr(corr_mod, "llm_judge", fake_judge)

    # 故意造一个 quick_filter 返回 UNCERTAIN 的场景：同 /16 子网，不同 host，相同服务前缀
    a1 = _alert("1", host_ip="10.0.0.1", event_name="db connection timeout")
    a2 = _alert("2", host_ip="10.0.0.2", event_name="db slow query")

    g1 = await corr_mod.correlate(a1, store)
    g2 = await corr_mod.correlate(a2, store)

    # LLM 说关联了 → 应合并
    assert g1.group_id == g2.group_id
    await redis.aclose()
```

- [ ] **Step 2: 跑测试确认 fail**

- [ ] **Step 3: 实现 correlate**

`src/correlator/correlate.py`:

```python
"""Correlate 主入口：alert → AlertGroup（新建或加入现有）

性能要点：
- 进 LLM 之前先快筛，覆盖 80% 场景
- active groups 用 Redis pipeline 一次性拉取，避免 N 次 RTT
- 同步代码路径（无 LLM）平均 < 5ms
"""

import logging

from src.correlator.groups import GroupStore
from src.correlator.llm_judge import llm_judge
from src.correlator.quick_filter import Verdict, quick_filter
from src.models import Alert, AlertGroup

logger = logging.getLogger(__name__)


async def correlate(alert: Alert, store: GroupStore) -> AlertGroup:
    """决定 alert 加入现有 group 还是新建独立 group

    返回 alert 最终归属的 group。
    """
    active = await store.active_groups()

    matched: AlertGroup | None = None
    for group in active:
        verdict = quick_filter(alert, group)

        if verdict == Verdict.DEFINITELY_RELATED:
            matched = group
            break
        if verdict == Verdict.DEFINITELY_NOT:
            continue

        # UNCERTAIN → LLM 兜底
        ok, reason = await llm_judge(
            alert_summary=f"{alert.host_ip} {alert.event_name}",
            group_summary=group.summary(),
        )
        if ok:
            logger.info(f"llm_judge merged alert {alert.event_id} into group {group.group_id}: {reason}")
            matched = group
            break

    if matched is not None:
        matched.derived_alerts.append(alert)
        await store.save(matched)
        return matched

    # 新建独立 group
    new_group = AlertGroup(root_alert=alert)
    await store.save(new_group)
    return new_group
```

- [ ] **Step 4: 跑测试确认 pass**

- [ ] **Step 5: Commit**

```bash
git add src/correlator/correlate.py tests/test_correlator.py
git commit -m "feat(correlator): main correlate() entry combining quick_filter + llm_judge"
```

---

## Task 6: Action Mutex（Redis 分布式锁）

**Files:**
- Create: `src/coordination/__init__.py`, `src/coordination/mutex.py`
- Test: `tests/test_coordination.py`

- [ ] **Step 1: 写测试**

`tests/test_coordination.py`:

```python
"""Action Mutex + RateLimiter 测试"""

import pytest
import redis.asyncio as aioredis

from src.coordination.mutex import acquire_action_mutex, release_action_mutex


@pytest.fixture
async def redis():
    r = aioredis.from_url("redis://localhost:6379/15")
    await r.flushdb()
    yield r
    await r.flushdb()
    await r.aclose()


@pytest.mark.asyncio
async def test_mutex_acquire_release(redis):
    """同 target 第一次获锁成功，释放后再次成功"""
    token1 = await acquire_action_mutex(redis, "host:1.1.1.1", ttl=10)
    assert token1 is not None
    await release_action_mutex(redis, "host:1.1.1.1", token1)

    token2 = await acquire_action_mutex(redis, "host:1.1.1.1", ttl=10)
    assert token2 is not None


@pytest.mark.asyncio
async def test_mutex_blocks_second_acquire(redis):
    """没释放前第二次获锁返回 None"""
    token1 = await acquire_action_mutex(redis, "host:1.1.1.1", ttl=10)
    assert token1 is not None

    token2 = await acquire_action_mutex(redis, "host:1.1.1.1", ttl=10)
    assert token2 is None  # 被锁


@pytest.mark.asyncio
async def test_mutex_release_safe_with_wrong_token(redis):
    """用错误 token release 不影响别人的锁"""
    token = await acquire_action_mutex(redis, "host:1.1.1.1", ttl=10)
    assert token is not None

    # 别人误用 wrong token release
    await release_action_mutex(redis, "host:1.1.1.1", "wrong-token")

    # 锁应该还在
    token2 = await acquire_action_mutex(redis, "host:1.1.1.1", ttl=10)
    assert token2 is None  # 还被原 token 占着


@pytest.mark.asyncio
async def test_mutex_ttl_auto_expires(redis):
    """TTL 到了自动释放（防 worker crash 后死锁）"""
    import asyncio
    token = await acquire_action_mutex(redis, "host:1.1.1.1", ttl=1)
    assert token is not None
    await asyncio.sleep(2)
    token2 = await acquire_action_mutex(redis, "host:1.1.1.1", ttl=10)
    assert token2 is not None
```

- [ ] **Step 2: 跑测试确认 fail**

- [ ] **Step 3: 实现 mutex**

`src/coordination/__init__.py`:
```python
"""协同：Action Mutex + Rate Limit"""
```

`src/coordination/mutex.py`:

```python
"""Action Mutex - Redis 分布式锁

性能要点（python-performance-optimization）：
- 用 SET NX EX 一次原子操作（避免 SETNX + EXPIRE 两次往返带来的 race）
- release 用 Lua 脚本 CAS（compare-and-swap）：只有 token 匹配才删
- TTL 兜底：worker crash 后锁自动释放，避免死锁

使用：
    token = await acquire_action_mutex(redis, target, ttl=300)
    if token is None:
        # 被别人占着，转人工或重试
        return
    try:
        ... do work ...
    finally:
        await release_action_mutex(redis, target, token)
"""

import secrets
from typing import Final

import redis.asyncio as aioredis

# Lua 脚本：原子 CAS release（只有 token 匹配才 delete）
_RELEASE_LUA: Final[str] = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


def _lock_key(target: str) -> str:
    return f"coord:action_mutex:{target}"


async def acquire_action_mutex(
    redis: aioredis.Redis, target: str, ttl: int = 300
) -> str | None:
    """原子获锁，返回 token；失败返回 None

    Args:
        target: 互斥目标（host_ip / service_name 等）
        ttl: 锁 TTL 秒，超过自动释放（防死锁）
    """
    token = secrets.token_urlsafe(16)
    ok = await redis.set(_lock_key(target), token, nx=True, ex=ttl)
    return token if ok else None


async def release_action_mutex(
    redis: aioredis.Redis, target: str, token: str
) -> bool:
    """释放锁。只有 token 匹配才真删（避免误删别人的锁）"""
    result = await redis.eval(_RELEASE_LUA, 1, _lock_key(target), token)
    return bool(result)
```

- [ ] **Step 4: 跑测试确认 pass**

- [ ] **Step 5: Commit**

```bash
git add src/coordination/__init__.py src/coordination/mutex.py tests/test_coordination.py
git commit -m "feat(coordination): Redis distributed mutex with Lua CAS release"
```

---

## Task 7: 风暴限流 + 系统过载保护

**Files:**
- Create: `src/coordination/rate_limit.py`
- Modify: `tests/test_coordination.py`

- [ ] **Step 1: 写测试**

```python
@pytest.mark.asyncio
async def test_rate_limiter_allows_within_limit(redis):
    """每分钟 5 个 token，前 5 次 allow"""
    from src.coordination.rate_limit import RateLimiter

    rl = RateLimiter(redis, key="test", limit=5, window_sec=60)

    for i in range(5):
        ok = await rl.try_acquire()
        assert ok is True, f"call {i} should pass"


@pytest.mark.asyncio
async def test_rate_limiter_blocks_over_limit(redis):
    from src.coordination.rate_limit import RateLimiter

    rl = RateLimiter(redis, key="test", limit=3, window_sec=60)

    for _ in range(3):
        await rl.try_acquire()
    blocked = await rl.try_acquire()
    assert blocked is False


@pytest.mark.asyncio
async def test_rate_limiter_resets_after_window(redis):
    import asyncio
    from src.coordination.rate_limit import RateLimiter

    rl = RateLimiter(redis, key="test", limit=2, window_sec=1)

    await rl.try_acquire()
    await rl.try_acquire()
    assert (await rl.try_acquire()) is False

    await asyncio.sleep(1.5)
    assert (await rl.try_acquire()) is True


@pytest.mark.asyncio
async def test_pending_workflow_counter(redis):
    from src.coordination.rate_limit import PendingWorkflowGauge

    g = PendingWorkflowGauge(redis)
    assert (await g.count()) == 0

    await g.incr()
    await g.incr()
    assert (await g.count()) == 2

    await g.decr()
    assert (await g.count()) == 1


@pytest.mark.asyncio
async def test_system_overloaded_when_too_many_pending(redis):
    from src.coordination.rate_limit import PendingWorkflowGauge, SystemOverloadGuard

    g = PendingWorkflowGauge(redis)
    for _ in range(50):
        await g.incr()

    guard = SystemOverloadGuard(redis, max_pending=50)
    assert (await guard.is_overloaded()) is True
```

- [ ] **Step 2: 跑测试确认 fail**

- [ ] **Step 3: 实现 rate_limit**

`src/coordination/rate_limit.py`:

```python
"""风暴限流 + 系统过载保护

3 个组件：
1. RateLimiter: 滑动计数器，单分钟 N 个告警上限
2. PendingWorkflowGauge: 当前 in-flight workflow 计数
3. SystemOverloadGuard: pending workflow 超阈值时拒新告警

性能要点（python-performance-optimization）：
- INCR + EXPIRE 用 pipeline 一次往返
- 不在 Python 层 sliding window 维护时间戳列表（内存爆炸 + GC 开销）
- pending counter 用 Redis INCR/DECR 无锁原子操作

使用模式：
    rl = RateLimiter(redis, "alerts", limit=100, window_sec=60)
    if not await rl.try_acquire():
        # 风暴模式：转人工
        return
"""

import redis.asyncio as aioredis


class RateLimiter:
    """固定窗口计数器 (fixed window counter)

    注：固定窗口在窗口边界有 burst 风险（理论 2x），但实现简单
    且对 AIOps 场景足够。需要严格平滑可换 sliding-window-log 方案。
    """

    def __init__(self, redis: aioredis.Redis, key: str, limit: int, window_sec: int) -> None:
        self.redis = redis
        self._key_prefix = f"coord:rate:{key}"
        self.limit = limit
        self.window_sec = window_sec

    def _bucket_key(self, ts: int | None = None) -> str:
        import time
        if ts is None:
            ts = int(time.time())
        bucket = ts // self.window_sec
        return f"{self._key_prefix}:{bucket}"

    async def try_acquire(self) -> bool:
        """获 token；超限返回 False"""
        key = self._bucket_key()
        async with self.redis.pipeline(transaction=False) as pipe:
            pipe.incr(key)
            pipe.expire(key, self.window_sec)
            count, _ = await pipe.execute()
        return int(count) <= self.limit


class PendingWorkflowGauge:
    """全局 in-flight workflow 计数器（INCR/DECR）"""

    KEY = "coord:pending_workflows"

    def __init__(self, redis: aioredis.Redis) -> None:
        self.redis = redis

    async def incr(self) -> int:
        return int(await self.redis.incr(self.KEY))

    async def decr(self) -> int:
        return int(await self.redis.decr(self.KEY))

    async def count(self) -> int:
        v = await self.redis.get(self.KEY)
        return int(v) if v else 0


class SystemOverloadGuard:
    """pending workflow 超阈值时认为系统过载"""

    def __init__(self, redis: aioredis.Redis, max_pending: int = 50) -> None:
        self.gauge = PendingWorkflowGauge(redis)
        self.max_pending = max_pending

    async def is_overloaded(self) -> bool:
        return (await self.gauge.count()) >= self.max_pending
```

- [ ] **Step 4: 跑测试确认 pass**

- [ ] **Step 5: Commit**

```bash
git add src/coordination/rate_limit.py tests/test_coordination.py
git commit -m "feat(coordination): rate limiter + system overload guard"
```

---

## Task 8: Webhook 入口集成限流

**Files:**
- Modify: `src/api/webhook.py`
- Modify: `tests/test_webhook.py`

- [ ] **Step 1: 写测试**

`tests/test_webhook.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_webhook_rejects_when_rate_limit_exceeded(zabbix_payload):
    """超过 100/min 时返回 429 + 转人工通知"""
    with patch("src.api.webhook.RateLimiter") as MockRL:
        instance = MockRL.return_value
        instance.try_acquire = AsyncMock(return_value=False)

        with patch("src.api.webhook.produce_alert", new_callable=AsyncMock):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/webhook/zabbix", json=zabbix_payload, headers=_zabbix_headers()
                )

        assert resp.status_code == 429
        assert "rate limit" in resp.text.lower()


@pytest.mark.asyncio
async def test_webhook_rejects_when_overloaded(zabbix_payload):
    with patch("src.api.webhook.SystemOverloadGuard") as MockGuard:
        instance = MockGuard.return_value
        instance.is_overloaded = AsyncMock(return_value=True)

        with patch("src.api.webhook.produce_alert", new_callable=AsyncMock):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/webhook/zabbix", json=zabbix_payload, headers=_zabbix_headers()
                )
        assert resp.status_code == 503
        assert "overloaded" in resp.text.lower()
```

- [ ] **Step 2: 改 webhook.py**

```python
# 顶部新增 import
from src.coordination.rate_limit import RateLimiter, SystemOverloadGuard

# 在 zabbix_webhook 函数 _require_zabbix_auth 后插入：

@router.post("/webhook/zabbix")
async def zabbix_webhook(alert: Alert, request: Request):
    _require_zabbix_auth(request)
    redis = request.app.state.redis

    # 1. 系统过载保护
    guard = SystemOverloadGuard(redis, max_pending=settings.max_pending_workflows)
    if await guard.is_overloaded():
        raise HTTPException(status_code=503, detail="aiops overloaded, fallback to manual")

    # 2. 风暴限流
    rl = RateLimiter(redis, "zabbix_alerts", limit=settings.alert_rate_limit_per_min, window_sec=60)
    if not await rl.try_acquire():
        raise HTTPException(status_code=429, detail="rate limit exceeded, alert dropped")

    msg_id = await produce_alert(redis, alert)
    if msg_id is None:
        return {"status": "duplicate", "event_id": alert.event_id}
    return {"status": "accepted", "event_id": alert.event_id, "stream_id": msg_id}
```

`src/config.py` 加：
```python
# Phase 4: 协同
alert_rate_limit_per_min: int = 100
max_pending_workflows: int = 50
correlator_window_sec: int = 30
```

- [ ] **Step 3: 跑测试确认 pass**

- [ ] **Step 4: Commit**

```bash
git add src/api/webhook.py src/config.py tests/test_webhook.py
git commit -m "feat(coordination): webhook integrates rate limit + overload guard"
```

---

## Task 9: Consumer 集成 Correlator

**Files:**
- Modify: `src/bus/consumer.py`
- Modify: `tests/test_bus.py`

- [ ] **Step 1: 改 consumer，告警进 Temporal 之前先关联**

```python
# consumer.py 改 start_consumer_loop 内 alert 处理段：

async def start_consumer_loop(app) -> None:
    redis = app.state.redis
    temporal = app.state.temporal
    # ... 已有代码 ...

    from src.correlator.correlate import correlate
    from src.correlator.groups import GroupStore
    from src.coordination.rate_limit import PendingWorkflowGauge

    store = GroupStore(redis, window_sec=settings.correlator_window_sec)
    gauge = PendingWorkflowGauge(redis)

    while True:
        result = await reclaim_pending_alert(redis, "aiops-workers", "worker-1")
        if result is None:
            result = await consume_alert(redis, "aiops-workers", "worker-1", block_ms=5000)
        if result is None:
            continue

        alert, msg_id = result

        # Phase 4: 关联判断
        group = await correlate(alert, store)

        # 衍生告警：抑制（不开新 workflow，直接 ack 让根因 workflow 接管）
        if alert.event_id != group.root_alert.event_id:
            logger.info(f"derived alert {alert.event_id} suppressed (root {group.root_alert.event_id})")
            await ack_alert(redis, "aiops-workers", msg_id)
            continue

        workflow_id = f"alert-{alert.event_id}"
        try:
            await temporal.start_workflow(
                "AlertWorkflow", alert.model_dump_json(),
                id=workflow_id,
                task_queue=settings.temporal_task_queue,
            )
            await gauge.incr()
            await ack_alert(redis, "aiops-workers", msg_id)
        except WorkflowAlreadyStartedError:
            await ack_alert(redis, "aiops-workers", msg_id)
        except Exception as e:
            logger.error(f"start_workflow failed for {alert.event_id}: {e}")
            await asyncio.sleep(5)
```

- [ ] **Step 2: workflow 完成时 decr gauge**

`src/workflows/alert_workflow.py`：在 run() 末尾每个 return 之前 decr。但 workflow 不能直接调 redis（sandbox），改成加一个 activity：

`src/activities/coordination.py`（新建）:
```python
"""协同相关 activities：counter decrement 等"""

import logging
from temporalio import activity

logger = logging.getLogger(__name__)

# 模块级 redis client，由 main.py lifespan 初始化
redis_client = None


@activity.defn
async def decr_pending_gauge() -> None:
    if redis_client is None:
        return
    try:
        await redis_client.decr("coord:pending_workflows")
    except Exception as exc:
        logger.warning(f"decr pending gauge failed: {exc}")
```

`src/workflows/alert_workflow.py` 加 finally-style 的 cleanup：在 run 方法最外层包 try/finally，finally 调 decr_pending_gauge activity。

```python
# 在 run 方法开头：
try:
    # ...原有所有代码...
    return decision_label
finally:
    # 不管什么 return / exception 都减 gauge
    try:
        await workflow.execute_activity(
            "decr_pending_gauge",
            start_to_close_timeout=timedelta(seconds=5),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
    except Exception:
        pass  # gauge 出错不能影响主流程
```

`src/main.py` 注册 + 初始化 redis_client：
```python
import src.activities.coordination as coord_activities

# lifespan 里：
coord_activities.redis_client = redis_client

# Worker activities 列表加 decr_pending_gauge
```

- [ ] **Step 3: 跑全套测试**

```bash
.venv/Scripts/python.exe -m pytest tests/ -v
```

- [ ] **Step 4: Commit**

```bash
git add src/bus/consumer.py src/workflows/alert_workflow.py src/activities/coordination.py src/main.py
git commit -m "feat(coordination): consumer correlate + workflow gauge decrement"
```

---

## Task 10: Workflow execute_runbook 加 mutex

**Files:**
- Modify: `src/workflows/alert_workflow.py`
- Modify: `tests/test_workflow.py`

- [ ] **Step 1: 写测试**

```python
@pytest.mark.asyncio
async def test_workflow_skips_when_mutex_held():
    """mutex 已被别人持有 → workflow 跳过执行 + 通知"""
    # 详细 mock 略，关键是 acquire_mutex 返回 None → workflow return "skipped_mutex"
```

- [ ] **Step 2: 实现**

mutex 同样要走 activity，不能在 workflow 直接连 redis：

`src/activities/coordination.py` 加：
```python
@activity.defn
async def try_acquire_mutex(target: str, ttl: int = 600) -> str:
    """返回 token 字符串，失败返回空字符串"""
    if redis_client is None:
        return "skip"  # 不阻断流程
    from src.coordination.mutex import acquire_action_mutex
    token = await acquire_action_mutex(redis_client, target, ttl)
    return token or ""


@activity.defn
async def release_mutex(target: str, token: str) -> None:
    if not token or token == "skip" or redis_client is None:
        return
    from src.coordination.mutex import release_action_mutex
    await release_action_mutex(redis_client, target, token)
```

`alert_workflow.py` 在执行 runbook 前后包 mutex：

```python
# Step 8 改为：
mutex_target = f"host:{alert['host_ip']}"
mutex_token = await workflow.execute_activity(
    "try_acquire_mutex",
    args=[mutex_target, 600],
    start_to_close_timeout=timedelta(seconds=10),
    retry_policy=RetryPolicy(maximum_attempts=2),
)
if not mutex_token:
    # 被别人占着，转人工
    await workflow.execute_activity("send_feishu_result", args=[
        f"⚠️ 告警 {event_id} 跳过自动执行：目标 {alert['host_ip']} 正被另一个告警处理",
    ], start_to_close_timeout=timedelta(seconds=10), retry_policy=_NOTIFY_RETRY)
    await workflow.execute_activity("write_audit", args=[
        alert_json, workflow_id, "skipped_mutex", runbook_id, runbook_params, None, feishu_msg_id,
    ], start_to_close_timeout=timedelta(seconds=10), retry_policy=_NOTIFY_RETRY)
    return "skipped_mutex"

try:
    exec_result_json = await workflow.execute_activity(
        "execute_runbook",
        args=[runbook_id, runbook_params],
        start_to_close_timeout=timedelta(minutes=10),
        retry_policy=RetryPolicy(maximum_attempts=1),
    )
finally:
    await workflow.execute_activity(
        "release_mutex", args=[mutex_target, mutex_token],
        start_to_close_timeout=timedelta(seconds=5),
        retry_policy=RetryPolicy(maximum_attempts=2),
    )
```

- [ ] **Step 3: 测试 + Commit**

```bash
git add src/activities/coordination.py src/workflows/alert_workflow.py src/main.py tests/test_workflow.py
git commit -m "feat(coordination): action mutex around execute_runbook"
```

---

## Task 11: 文档 + 端到端验证

**Files:**
- Create: `docs/multi-agent-coordination.md`
- Modify: `.env.example`

- [ ] **Step 1: 写文档**

`docs/multi-agent-coordination.md`：

```markdown
# Phase 4: 多 Agent 协同 操作手册

> 配套架构 [docs/生产级 AIOps 架构设计.md](生产级 AIOps 架构设计.md) §6 + §9.6

## 4 层防护

```
[A] Webhook 入口                 [B] Consumer 关联              [C] Workflow 执行
┌─────────────────────┐  ┌──────────────────────────┐  ┌──────────────────┐
│ rate_limit (100/min)│→ │ correlate (30s window)    │→ │ action_mutex     │
│ overload guard (50) │  │  ├─ quick_filter (4 rule) │  │ (per host)       │
└─────────────────────┘  │  └─ llm_judge (cache)     │  └──────────────────┘
                         └──────────────────────────┘
```

## 配置

| env | 默认 | 说明 |
|---|---|---|
| ALERT_RATE_LIMIT_PER_MIN | 100 | 单分钟告警上限 |
| MAX_PENDING_WORKFLOWS | 50 | in-flight workflow 上限 |
| CORRELATOR_WINDOW_SEC | 30 | 关联窗口 |

## 调优

- 告警平稳但偶发尖峰 → 抬 `ALERT_RATE_LIMIT_PER_MIN`
- workflow 执行慢导致积压 → 抬 `MAX_PENDING_WORKFLOWS` 或加 worker 副本
- 误合并多 → 把 `CORRELATOR_WINDOW_SEC` 调小（如 10s）
- 漏关联多 → 调大窗口 + 检查 quick_filter 规则
```

- [ ] **Step 2: .env.example 加配置**

```bash
# Phase 4
ALERT_RATE_LIMIT_PER_MIN=100
MAX_PENDING_WORKFLOWS=50
CORRELATOR_WINDOW_SEC=30
```

- [ ] **Step 3: 端到端验证脚本**

```bash
# 验证 1: 同 host 多告警合并
for i in 1 2 3; do
    curl -X POST http://localhost:8000/webhook/zabbix \
        -H "Authorization: Bearer ${ZABBIX_WEBHOOK_TOKEN}" \
        -H "Content-Type: application/json" \
        -d '{"event_id":"test-'$i'","event_name":"FS / 90%","severity":"high",
             "hostname":"aiops-target","host_ip":"192.168.198.130","trigger_id":"t",
             "message":"test","timestamp":"2026-05-09T10:00:00Z","status":"problem"}'
    sleep 1
done

# 期望：只有第 1 条触发 workflow，2/3 被识别为衍生 + 抑制
# 看 audit log: tail audit.log | jq

# 验证 2: 风暴限流
for i in $(seq 1 200); do
    curl -X POST ... -d "..."
done
# 期望：第 101+ 条返回 429
```

- [ ] **Step 4: Commit**

```bash
git add docs/multi-agent-coordination.md .env.example
git commit -m "docs(coordination): operations manual + e2e verification"
```

---

## Done definition

- [ ] 全套测试 PASS（约 35 个新增）
- [ ] 同 host 30s 内多告警合并为 1 个 workflow
- [ ] 不同 host 独立 workflow 并发跑
- [ ] 100+/min 告警触发 429
- [ ] mutex 阻止同 host 两个 workflow 同时跑 ansible
- [ ] gauge 在 workflow 完成后正确 decrement（看 redis: `GET coord:pending_workflows`）
- [ ] 跟现状回归：单条告警行为不变
