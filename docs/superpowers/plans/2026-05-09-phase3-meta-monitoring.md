# Phase 3 收尾：元监控（Meta-Monitoring）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** AIOps 自身故障能被独立通道发现并告警——不能让"监控系统监控自己"。架构文档 §10 明确要求：AIOps 自身告警必须走独立飞书机器人 + 独立群，绝不依赖 AIOps 自己的链路。

**Architecture:** 一个独立的 `healthcheck` 进程（cron + 简单 Python 脚本），每分钟探测 AIOps 的 5 个关键依赖（FastAPI / Temporal / Redis / Lark WS / LLM Provider），任一异常就走**第二个**飞书 webhook 推到运维告警群。元监控刻意做得"傻"：不复用 AIOps 任何代码路径，也不连 AIOps 的 PG/Redis，避免连环失效。

**Tech Stack:** Python 3.14, httpx (sync), redis-cli (subprocess), feishu webhook v2, systemd timer / cron / docker compose 都行

**Spec:** [docs/生产级 AIOps 架构设计.md](../../生产级 AIOps 架构设计.md) §10 系统自监控

---

## 设计权衡

### 为什么不复用 src/ 任何代码

如果 healthcheck 复用 `src/llm/router.py` 调 LLM、复用 `src/activities/feishu.py` 发飞书，那一旦 AIOps 主进程挂了导致 import 出错，healthcheck 也跟着挂——**完全违背元监控的初衷**。

healthcheck 必须满足：
- **零代码依赖**：只 import 标准库 + httpx，不 `from src import ...`
- **零基础设施依赖**：不连 PG、不连 AIOps 的 Redis stream consumer
- **独立飞书凭据**：另建一个 `cli_xxx_meta` 应用 + 独立运维告警群
- **独立部署**：单独 docker container 或 systemd timer，跟 aiops-1 容器**生命周期解耦**

### 为什么不引入 Prometheus / Grafana

架构文档 §10 提到 "用 cron + simple healthcheck 检查 AIOps 关键组件存活"。引入 Prometheus 是过度设计：

- 你的告警量 < 100/天，5 个组件，每分钟 1 次轮询，状态点 **5×60×24 = 7200/天**——SQLite 都装得下
- Prometheus + Grafana + Alertmanager 多 3 个组件要监控，问题更多
- 真要可视化，几行 SQL + Plotly 自己画就行

**反过度设计原则**：如果一年内告警量 < 1000/天，**不用 Prometheus**。

### 性能预算（参考 python-performance-optimization）

healthcheck 单次执行预算：
- 5 个 HTTP probe，每个 timeout=2s → 最多 10s
- 内存常驻 < 30MB（python:slim + httpx）
- CPU 几乎零占用（每分钟 1 次）

由于探测都是 I/O 等待，**用同步 httpx 即可**——为 5 个 probe 引入 asyncio 反而是过度优化（参考 best practice "Profile before optimizing"）。

---

## File Structure

| 文件 | 操作 | 职责 |
|---|---|---|
| `meta_monitor/__init__.py` | 新建 | 包占位 |
| `meta_monitor/healthcheck.py` | 新建 | 单次探测 + 推飞书；**纯标准库 + httpx** |
| `meta_monitor/probes.py` | 新建 | 5 个 probe 函数（FastAPI / Temporal / Redis / Lark / LLM） |
| `meta_monitor/feishu_alert.py` | 新建 | 独立飞书 webhook 发文本（不复用 src/activities/feishu.py） |
| `meta_monitor/Dockerfile` | 新建 | python:slim + httpx，跑 cron-like loop |
| `meta_monitor/.env.example` | 新建 | 独立配置：META_FEISHU_WEBHOOK_URL 等 |
| `tests/test_meta_monitor.py` | 新建 | probe 单元测试（用 respx mock httpx） |
| `docker-compose.yml` | 修改 | 加 `meta-monitor` service |
| `.env.example` | 修改 | 加 `META_*` 配置项目说明 |
| `docs/meta-monitoring.md` | 新建 | 运维手册 |

> 故意把 meta_monitor/ 放在 src/ **外面**，物理隔离。

---

## Task 1: 飞书旧式 Webhook 发文本（最简渠道）

> **设计要点**：元监控用最简的"自定义机器人 webhook"，不用 IM v1 应用接口。原因是自定义 webhook 配置最简单（一个 URL，无需 App ID/Secret），出问题概率最低。

**Files:**
- Create: `meta_monitor/__init__.py`
- Create: `meta_monitor/feishu_alert.py`
- Test: `tests/test_meta_monitor.py`

- [ ] **Step 1: 写测试**

`tests/test_meta_monitor.py`:

```python
"""meta_monitor 元监控测试

故意只 mock httpx 网络层，不 import 任何 src/* 的东西，
保证测试不会因为 src/ 改动而连带失败。
"""

import respx
import pytest
from httpx import Response


@pytest.mark.asyncio
async def test_feishu_alert_posts_text_message(monkeypatch):
    """正常路径：调 send_alert，httpx 收到带正确 payload 的 POST"""
    from meta_monitor.feishu_alert import send_alert

    monkeypatch.setenv(
        "META_FEISHU_WEBHOOK_URL",
        "https://open.feishu.cn/open-apis/bot/v2/hook/test-token",
    )

    with respx.mock(base_url="https://open.feishu.cn") as mock:
        route = mock.post("/open-apis/bot/v2/hook/test-token").mock(
            return_value=Response(200, json={"StatusCode": 0, "code": 0})
        )
        send_alert("⚠️ AIOps healthcheck failed: temporal unreachable")

    assert route.called
    payload = route.calls.last.request.read().decode()
    assert "msg_type" in payload
    assert "AIOps healthcheck failed" in payload


def test_feishu_alert_silently_skips_when_unconfigured(monkeypatch, capsys):
    """没配 META_FEISHU_WEBHOOK_URL 时不应抛错（避免连环失效）"""
    from meta_monitor.feishu_alert import send_alert

    monkeypatch.delenv("META_FEISHU_WEBHOOK_URL", raising=False)
    send_alert("test message")  # 不应抛错
    captured = capsys.readouterr()
    assert "META_FEISHU_WEBHOOK_URL not configured" in captured.err


def test_feishu_alert_swallows_network_errors(monkeypatch, capsys):
    """网络错误时打 stderr 但不抛——元监控本身不能成为故障源"""
    from meta_monitor.feishu_alert import send_alert
    import httpx

    monkeypatch.setenv("META_FEISHU_WEBHOOK_URL", "https://invalid.example.com/hook")

    with respx.mock() as mock:
        mock.post("https://invalid.example.com/hook").mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        send_alert("test")

    captured = capsys.readouterr()
    assert "feishu alert failed" in captured.err.lower()
```

- [ ] **Step 2: 跑测试确认 fail**

```bash
.venv/Scripts/python.exe -m pytest tests/test_meta_monitor.py -v
```

预期：3 FAIL（模块不存在）

- [ ] **Step 3: 实现 feishu_alert**

`meta_monitor/__init__.py`:
```python
"""AIOps 元监控（独立部署，不依赖 src/）"""
```

`meta_monitor/feishu_alert.py`:

```python
"""独立飞书 webhook 文本告警

故意不复用 src/activities/feishu.py：
- 用最简单的"自定义机器人 webhook"，无需 App ID/Secret
- 失败时静默打 stderr，绝不抛异常（元监控不能成为故障源）

性能：每分钟 1 次调用，单次网络 IO 同步等待 timeout=5s 完全够用，
不需要 async（参考 python-performance-optimization 的 "profile before
optimizing" 和 "avoid premature optimization"）。
"""

import json
import os
import sys

import httpx


def send_alert(message: str) -> None:
    """发文本到独立飞书运维告警群

    Args:
        message: 告警文本，会被包成 {"msg_type": "text", "content": {"text": ...}}
    """
    url = os.environ.get("META_FEISHU_WEBHOOK_URL", "").strip()
    if not url:
        print("META_FEISHU_WEBHOOK_URL not configured, skipping alert", file=sys.stderr)
        return

    payload = {"msg_type": "text", "content": {"text": message}}
    try:
        with httpx.Client(timeout=5) as client:
            resp = client.post(url, json=payload)
            if resp.status_code != 200:
                print(
                    f"feishu alert failed: HTTP {resp.status_code} body={resp.text[:200]}",
                    file=sys.stderr,
                )
    except Exception as exc:  # noqa: BLE001
        print(f"feishu alert failed: {exc}", file=sys.stderr)
```

- [ ] **Step 4: 跑测试确认 pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_meta_monitor.py -v
```

预期：3 PASS

- [ ] **Step 5: Commit**

```bash
git add meta_monitor/ tests/test_meta_monitor.py
git commit -m "feat(meta-monitor): standalone feishu alerter (no src/ deps)"
```

---

## Task 2: 5 个 Probe 函数

**Files:**
- Create: `meta_monitor/probes.py`
- Modify: `tests/test_meta_monitor.py`

- [ ] **Step 1: 写测试（覆盖 5 个 probe 各一个 PASS / FAIL 用例）**

把以下追加到 `tests/test_meta_monitor.py`:

```python
import respx
from httpx import Response


# ---------- FastAPI probe ----------

def test_probe_fastapi_ok():
    from meta_monitor.probes import probe_fastapi

    with respx.mock(base_url="http://aiops") as mock:
        mock.get("/health").mock(return_value=Response(200, json={"status": "ok"}))
        ok, msg = probe_fastapi("http://aiops")

    assert ok is True
    assert "ok" in msg


def test_probe_fastapi_down():
    from meta_monitor.probes import probe_fastapi
    import httpx

    with respx.mock() as mock:
        mock.get("http://aiops/health").mock(side_effect=httpx.ConnectError("refused"))
        ok, msg = probe_fastapi("http://aiops")

    assert ok is False
    assert "refused" in msg.lower() or "connect" in msg.lower()


# ---------- Temporal probe ----------

def test_probe_temporal_ok():
    from meta_monitor.probes import probe_temporal

    # Temporal grpc 7233 用 TCP 连接判活：respx 不支持 raw TCP，
    # 改用 telnetlib 风格的 socket 探测，测试用 monkeypatch
    import socket

    real_create_connection = socket.create_connection

    def fake_create(addr, timeout=None):
        if addr == ("temporal", 7233):
            class Sock:
                def close(self): pass
            return Sock()
        return real_create_connection(addr, timeout=timeout)

    socket.create_connection = fake_create
    try:
        ok, msg = probe_temporal("temporal:7233")
        assert ok is True
    finally:
        socket.create_connection = real_create_connection


def test_probe_temporal_down(monkeypatch):
    from meta_monitor.probes import probe_temporal
    import socket

    def fake_create(addr, timeout=None):
        raise ConnectionRefusedError("temporal down")

    monkeypatch.setattr(socket, "create_connection", fake_create)
    ok, msg = probe_temporal("temporal:7233")
    assert ok is False


# ---------- Redis probe ----------

def test_probe_redis_ok(monkeypatch):
    from meta_monitor.probes import probe_redis
    import socket

    def fake_create(addr, timeout=None):
        if addr == ("redis", 6379):
            class Sock:
                def close(self): pass
            return Sock()

    monkeypatch.setattr(socket, "create_connection", fake_create)
    ok, msg = probe_redis("redis:6379")
    assert ok is True


# ---------- Lark WS probe ----------

def test_probe_lark_ws_ok():
    from meta_monitor.probes import probe_lark_ws

    with respx.mock() as mock:
        mock.get("https://open.feishu.cn/open-apis/").mock(return_value=Response(404))
        ok, msg = probe_lark_ws()

    assert ok is True  # 任何 < 500 都算飞书 API 在线


def test_probe_lark_ws_down():
    from meta_monitor.probes import probe_lark_ws
    import httpx

    with respx.mock() as mock:
        mock.get("https://open.feishu.cn/open-apis/").mock(
            side_effect=httpx.ConnectTimeout("timeout")
        )
        ok, msg = probe_lark_ws()

    assert ok is False


# ---------- LLM probe ----------

def test_probe_llm_skipped_when_no_url(monkeypatch):
    """没配 LLM_PRIMARY_BASE_URL 时 probe 应该跳过（返回 OK + 'skipped' msg）"""
    from meta_monitor.probes import probe_llm

    monkeypatch.delenv("LLM_PRIMARY_BASE_URL", raising=False)
    ok, msg = probe_llm()
    assert ok is True
    assert "skipped" in msg.lower()
```

- [ ] **Step 2: 跑测试确认 fail**

```bash
.venv/Scripts/python.exe -m pytest tests/test_meta_monitor.py -v
```

- [ ] **Step 3: 实现 probes**

`meta_monitor/probes.py`:

```python
"""5 个独立 probe 函数

每个 probe 返回 (ok: bool, message: str)：
- ok=True 表示组件健康
- message 用于 alert 文本里的诊断 hint

性能：所有 probe 同步执行，单个 timeout 2s，5 个串行最多 10s。
不引入 asyncio 因为：
  1. probe 数量固定 5 个，不会扩展
  2. 同步代码更易调试，对元监控这种"绝不能挂"的程序至关重要
"""

import os
import socket
from typing import Any

import httpx

_PROBE_TIMEOUT = 2.0


def _tcp_probe(host: str, port: int, label: str) -> tuple[bool, str]:
    """通用 TCP 三次握手检测"""
    try:
        sock = socket.create_connection((host, port), timeout=_PROBE_TIMEOUT)
        sock.close()
        return True, f"{label} tcp ok"
    except Exception as exc:  # noqa: BLE001
        return False, f"{label} tcp fail: {exc}"


def probe_fastapi(base_url: str) -> tuple[bool, str]:
    """探 AIOps FastAPI /health 端点"""
    try:
        with httpx.Client(timeout=_PROBE_TIMEOUT) as client:
            r = client.get(f"{base_url.rstrip('/')}/health")
        if r.status_code == 200:
            return True, "fastapi /health ok"
        return False, f"fastapi /health http {r.status_code}"
    except Exception as exc:  # noqa: BLE001
        return False, f"fastapi unreachable: {exc}"


def probe_temporal(addr: str) -> tuple[bool, str]:
    """探 Temporal gRPC 端口（TCP 三次握手即可，不发真 grpc 请求避免引依赖）"""
    host, port = addr.split(":") if ":" in addr else (addr, "7233")
    return _tcp_probe(host, int(port), "temporal")


def probe_redis(addr: str) -> tuple[bool, str]:
    """探 Redis 端口"""
    host, port = addr.split(":") if ":" in addr else (addr, "6379")
    return _tcp_probe(host, int(port), "redis")


def probe_lark_ws() -> tuple[bool, str]:
    """探飞书开放平台是否可达（HTTP 返回 < 500 即认为在线）"""
    try:
        with httpx.Client(timeout=_PROBE_TIMEOUT) as client:
            r = client.get("https://open.feishu.cn/open-apis/")
        if r.status_code < 500:
            return True, f"lark http {r.status_code}"
        return False, f"lark http {r.status_code}"
    except Exception as exc:  # noqa: BLE001
        return False, f"lark unreachable: {exc}"


def probe_llm() -> tuple[bool, str]:
    """探 LLM 主提供商可达性（GET base_url，预期 401/404 也算在线）

    没配 LLM_PRIMARY_BASE_URL 时跳过（OK + skipped 文本）。
    """
    base_url = os.environ.get("LLM_PRIMARY_BASE_URL", "").strip()
    if not base_url:
        return True, "llm probe skipped (no LLM_PRIMARY_BASE_URL)"

    try:
        with httpx.Client(timeout=_PROBE_TIMEOUT) as client:
            r = client.get(base_url)
        # 200/401/403/404 都说明 endpoint 在线
        if r.status_code < 500:
            return True, f"llm http {r.status_code}"
        return False, f"llm http {r.status_code}"
    except Exception as exc:  # noqa: BLE001
        return False, f"llm unreachable: {exc}"


PROBES: dict[str, Any] = {
    "fastapi": lambda: probe_fastapi(os.environ.get("META_AIOPS_URL", "http://aiops:8000")),
    "temporal": lambda: probe_temporal(os.environ.get("META_TEMPORAL_ADDR", "temporal:7233")),
    "redis": lambda: probe_redis(os.environ.get("META_REDIS_ADDR", "redis:6379")),
    "lark": probe_lark_ws,
    "llm": probe_llm,
}
```

- [ ] **Step 4: 跑测试确认 pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_meta_monitor.py -v
```

预期：所有 PASS（约 11 个测试）

- [ ] **Step 5: Commit**

```bash
git add meta_monitor/probes.py tests/test_meta_monitor.py
git commit -m "feat(meta-monitor): 5 probes for fastapi/temporal/redis/lark/llm"
```

---

## Task 3: healthcheck 主循环

**Files:**
- Create: `meta_monitor/healthcheck.py`
- Modify: `tests/test_meta_monitor.py`

- [ ] **Step 1: 写测试**

追加到 `tests/test_meta_monitor.py`:

```python
def test_healthcheck_run_once_all_ok(monkeypatch):
    """所有 probe 都 OK 时 → 不发告警"""
    from meta_monitor import probes
    from meta_monitor.healthcheck import run_once

    sent = []
    monkeypatch.setattr(probes, "PROBES", {
        "p1": lambda: (True, "p1 ok"),
        "p2": lambda: (True, "p2 ok"),
    })
    monkeypatch.setattr("meta_monitor.healthcheck.send_alert", lambda msg: sent.append(msg))

    failures = run_once()
    assert failures == []
    assert sent == []  # 全 OK 不发告警


def test_healthcheck_run_once_one_failure_sends_alert(monkeypatch):
    """有 1 个 probe FAIL 时发告警，message 含失败原因"""
    from meta_monitor import probes
    from meta_monitor.healthcheck import run_once

    sent = []
    monkeypatch.setattr(probes, "PROBES", {
        "ok_one": lambda: (True, "ok_one ok"),
        "broken": lambda: (False, "broken: connection refused"),
    })
    monkeypatch.setattr("meta_monitor.healthcheck.send_alert", lambda msg: sent.append(msg))

    failures = run_once()
    assert failures == ["broken"]
    assert len(sent) == 1
    assert "broken" in sent[0]
    assert "connection refused" in sent[0]


def test_healthcheck_dedup_within_window(monkeypatch):
    """同一组件连续失败时，5 分钟内只发一次告警（避免刷屏）"""
    from meta_monitor import probes
    from meta_monitor.healthcheck import run_once, _alert_state

    sent = []
    monkeypatch.setattr(probes, "PROBES", {
        "broken": lambda: (False, "broken: down"),
    })
    monkeypatch.setattr("meta_monitor.healthcheck.send_alert", lambda msg: sent.append(msg))

    _alert_state.clear()  # 测试隔离
    run_once()  # 第一次：发
    run_once()  # 第二次：去重不发
    run_once()  # 第三次：去重不发

    assert len(sent) == 1


def test_healthcheck_recovery_sends_recovery_alert(monkeypatch):
    """组件从 FAIL 恢复到 OK 时，发一条恢复消息"""
    from meta_monitor import probes
    from meta_monitor.healthcheck import run_once, _alert_state

    sent = []
    state = {"broken": False}

    def flaky():
        return (state["broken"], "broken: down" if not state["broken"] else "broken ok")

    monkeypatch.setattr(probes, "PROBES", {"broken": flaky})
    monkeypatch.setattr("meta_monitor.healthcheck.send_alert", lambda msg: sent.append(msg))

    _alert_state.clear()
    state["broken"] = False  # 失败
    run_once()
    assert len(sent) == 1
    assert "down" in sent[0].lower() or "broken" in sent[0]

    state["broken"] = True  # 恢复
    run_once()
    assert len(sent) == 2
    assert "recover" in sent[1].lower() or "ok" in sent[1].lower()
```

- [ ] **Step 2: 跑测试确认 fail**

- [ ] **Step 3: 实现 healthcheck**

`meta_monitor/healthcheck.py`:

```python
"""单次探测 + 状态去重

性能要点（python-performance-optimization）：
- _alert_state 是 module-level dict，不引入 Redis/数据库
- run_once() 是普通同步函数，可以被任何调度器（cron/systemd/while-True-sleep）调起
- 失败窗口去重用本进程 dict + monotonic timestamp，O(1) 查询
"""

import time
import sys
from typing import Any

from meta_monitor import probes
from meta_monitor.feishu_alert import send_alert

# 状态机：每个 probe 名 → {"failed_at": float, "alerted_at": float}
# failed_at = 第一次失败时间；alerted_at = 最近发告警的时间
# 用 module-level 单例避免在多次 run_once 之间丢状态
_alert_state: dict[str, dict[str, float]] = {}

# 重复告警去重窗口（秒）：5 分钟内同一组件不再发告警
_DEDUP_WINDOW = 300.0


def run_once() -> list[str]:
    """跑一轮 probe，返回失败的组件名列表"""
    now = time.monotonic()
    failures: list[str] = []
    for name, probe in probes.PROBES.items():
        try:
            ok, msg = probe()
        except Exception as exc:  # noqa: BLE001
            ok, msg = False, f"probe crashed: {exc}"

        if not ok:
            failures.append(name)
            _handle_failure(name, msg, now)
        else:
            _handle_recovery(name, msg, now)

    return failures


def _handle_failure(name: str, msg: str, now: float) -> None:
    state = _alert_state.get(name) or {}
    last_alerted = state.get("alerted_at", 0.0)
    if now - last_alerted < _DEDUP_WINDOW:
        return  # 刚刚已告警，去重
    send_alert(f"⚠️ AIOps healthcheck FAIL [{name}]: {msg}")
    _alert_state[name] = {
        "failed_at": state.get("failed_at") or now,
        "alerted_at": now,
    }


def _handle_recovery(name: str, msg: str, now: float) -> None:
    if name not in _alert_state:
        return  # 之前就 OK，不发恢复
    duration = int(now - _alert_state[name].get("failed_at", now))
    send_alert(f"✅ AIOps healthcheck RECOVERED [{name}] after {duration}s: {msg}")
    _alert_state.pop(name)


def main_loop(interval_sec: int = 60) -> None:
    """阻塞 loop，docker container 入口点"""
    print(f"meta_monitor started, probing every {interval_sec}s", file=sys.stderr)
    while True:
        run_once()
        time.sleep(interval_sec)


if __name__ == "__main__":
    import os
    main_loop(int(os.environ.get("META_INTERVAL_SEC", "60")))
```

- [ ] **Step 4: 跑测试确认 pass**

- [ ] **Step 5: Commit**

```bash
git add meta_monitor/healthcheck.py tests/test_meta_monitor.py
git commit -m "feat(meta-monitor): healthcheck loop with dedup + recovery alerts"
```

---

## Task 4: docker-compose service + Dockerfile

**Files:**
- Create: `meta_monitor/Dockerfile`
- Modify: `docker-compose.yml`
- Create: `meta_monitor/.env.example`
- Modify: `.env.example`

- [ ] **Step 1: 写 Dockerfile**

`meta_monitor/Dockerfile`:

```dockerfile
# 故意用 slim 而不是从 src/ 共用镜像——元监控镜像独立
FROM python:3.14-slim

WORKDIR /app

# 只装 httpx 一个依赖
RUN pip install --no-cache-dir 'httpx>=0.28' && \
    rm -rf /root/.cache

COPY meta_monitor /app/meta_monitor

CMD ["python", "-m", "meta_monitor.healthcheck"]
```

- [ ] **Step 2: 改 docker-compose.yml 加 meta-monitor service**

新增到 services 块：

```yaml
  meta-monitor:
    build:
      context: .
      dockerfile: meta_monitor/Dockerfile
    environment:
      META_AIOPS_URL: http://aiops:8000
      META_TEMPORAL_ADDR: temporal:7233
      META_REDIS_ADDR: redis:6379
      META_INTERVAL_SEC: "60"
      META_FEISHU_WEBHOOK_URL: ${META_FEISHU_WEBHOOK_URL}
      LLM_PRIMARY_BASE_URL: ${LLM_PRIMARY_BASE_URL}
    # 故意不加 depends_on：meta-monitor 应该比 aiops 更早启动，
    # 在 aiops 没起来时也要能告警
    restart: unless-stopped
```

- [ ] **Step 3: .env.example 加配置说明**

追加：

```bash
# ========== Phase 3: 元监控（独立通道） ==========
# 必须用一个**独立的飞书机器人**！跟运维主告警群（FEISHU_RECEIVE_ID）分开
# 在飞书运维群里点 "添加机器人 → 自定义机器人"，复制 webhook URL 填这里
# 这个机器人专门收 AIOps 自身故障告警，绝不能跟 AIOps 主流程共用
META_FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/your-meta-token
```

- [ ] **Step 4: 验证 docker compose 起得来**

```bash
sudo docker compose -f docker-compose.yml build meta-monitor
sudo docker compose -f docker-compose.yml up -d meta-monitor

# 看 logs
sudo docker compose logs meta-monitor --tail 30
# 期望：meta_monitor started, probing every 60s
```

- [ ] **Step 5: 故意打挂一个组件验证告警**

```bash
# 停 redis 看 meta-monitor 是否报警
sudo docker compose stop redis
# 等 1 分钟，飞书运维群应收到 "⚠️ AIOps healthcheck FAIL [redis]: redis tcp fail: ..."

# 恢复
sudo docker compose start redis
# 1 分钟后飞书应收到 "✅ AIOps healthcheck RECOVERED [redis] after Xs: ..."
```

- [ ] **Step 6: Commit**

```bash
git add meta_monitor/Dockerfile docker-compose.yml .env.example meta_monitor/.env.example
git commit -m "feat(meta-monitor): docker service + .env config"
```

---

## Task 5: 运维操作手册

**Files:**
- Create: `docs/meta-monitoring.md`

- [ ] **Step 1: 写文档**

`docs/meta-monitoring.md`:

````markdown
# 元监控运维手册

> 配套架构文档 [docs/生产级 AIOps 架构设计.md](生产级 AIOps 架构设计.md) §10

## 设计原则：不能用自己监控自己

AIOps 自身故障必须由**完全独立**的链路告警：
- 独立 Docker container（meta-monitor）
- 独立飞书机器人（与 AIOps 主告警群分开）
- 不复用 src/* 任何代码、不连 Temporal/PG

## 5 个 Probe

| Probe | 目标 | 失败条件 |
|---|---|---|
| fastapi | `GET http://aiops:8000/health` | HTTP != 200 或 connect refused |
| temporal | TCP 7233 三次握手 | 连接拒绝/超时 |
| redis | TCP 6379 三次握手 | 连接拒绝/超时 |
| lark | `GET https://open.feishu.cn/open-apis/` | HTTP >= 500 或 timeout |
| llm | `GET $LLM_PRIMARY_BASE_URL` | HTTP >= 500（401/403/404 算在线） |

## 设置独立飞书机器人

1. 飞书 → 找一个**独立的运维告警群**（不是 AIOps 主审批群）
2. 群设置 → 群机器人 → 添加机器人 → **自定义机器人**（不要选应用机器人）
3. 复制 webhook URL，填到 `.env` 的 `META_FEISHU_WEBHOOK_URL`

## 配置项

| env | 默认 | 说明 |
|---|---|---|
| `META_AIOPS_URL` | `http://aiops:8000` | FastAPI 地址 |
| `META_TEMPORAL_ADDR` | `temporal:7233` | Temporal grpc 地址 |
| `META_REDIS_ADDR` | `redis:6379` | Redis 地址 |
| `META_INTERVAL_SEC` | `60` | 探测间隔 |
| `META_FEISHU_WEBHOOK_URL` | （必填） | 独立飞书机器人 webhook |
| `LLM_PRIMARY_BASE_URL` | （继承主 .env） | LLM 探测目标 |

## 告警去重

- 同组件 5 分钟内只发 1 次失败告警
- 组件恢复时发 1 次"RECOVERED"提示

## 验证元监控本身可用

```bash
sudo docker compose stop redis
# 1 分钟内飞书运维群应收 "⚠️ ... [redis] ..."
sudo docker compose start redis
# 1 分钟内飞书运维群应收 "✅ ... RECOVERED [redis] after Xs"
```

如果**没收到任何消息**，meta-monitor 自己挂了——这种最坏情况架构上无解（除非引入第二层元元监控）。建议每周手动 `docker compose ps meta-monitor` 抽查。
````

- [ ] **Step 2: Commit**

```bash
git add docs/meta-monitoring.md
git commit -m "docs(meta-monitor): operations manual"
```

---

## Done definition

- [ ] 11 个测试 PASS
- [ ] meta-monitor container 能 start + 持续 logging
- [ ] 手动停 redis 后飞书运维群收到 FAIL 告警
- [ ] redis 恢复后飞书运维群收到 RECOVERED 告警
- [ ] 同一故障 5 分钟内只发 1 次告警（不刷屏）
- [ ] 元监控本身**没** import 任何 `from src import ...`（用 `grep -r "from src" meta_monitor/` 应空）

```bash
# 最终确认元监控独立性
grep -r "from src" meta_monitor/ tests/test_meta_monitor.py 2>&1 | grep -v "^Binary" || echo "OK: no src/ deps"
```
