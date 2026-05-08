"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: test_correlator.py
@DateTime: 2026-05-08 22:42:00
@Docs: 测试告警关联模型、快筛、LLM 判定和 Redis 关联组存储
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
import redis.asyncio as aioredis

from src.models import Alert, AlertGroup


def _alert(
    event_id: str = "1",
    host_ip: str = "192.168.1.10",
    event_name: str = "Disk usage > 90%",
    t: datetime | None = None,
) -> Alert:
    return Alert(
        event_id=event_id,
        event_name=event_name,
        severity="high",
        hostname=f"host-{host_ip}",
        host_ip=host_ip,
        trigger_id=event_id,
        message="测试告警",
        timestamp=t or datetime.now(UTC),
        status="problem",
    )


@pytest.fixture
async def redis_client() -> Any:
    redis = aioredis.from_url("redis://localhost:6379/15")
    await redis.flushdb()
    yield redis
    await redis.flushdb()
    await redis.aclose()


def test_alert_group_initial_state() -> None:
    """新建 group 必有 root，衍生告警默认为空。"""
    root = _alert("1")
    group = AlertGroup(root_alert=root)

    assert group.root_alert.event_id == "1"
    assert group.derived_alerts == []
    assert group.created_at is not None
    assert group.group_id == "1"
    assert group.root_host == root.host_ip


def test_alert_group_add_derived() -> None:
    """加衍生告警后能正确读取。"""
    group = AlertGroup(root_alert=_alert("1"))
    group.derived_alerts.append(_alert("2"))

    assert len(group.derived_alerts) == 1
    assert group.derived_alerts[0].event_id == "2"


def test_alert_group_summary_text() -> None:
    """group.summary() 返回简短描述用于 LLM prompt。"""
    group = AlertGroup(root_alert=_alert("1", host_ip="10.0.0.1", event_name="Disk full"))
    group.derived_alerts.append(_alert("2", host_ip="10.0.0.1", event_name="App down"))

    summary = group.summary()

    assert "10.0.0.1" in summary
    assert "Disk full" in summary
    assert "App down" in summary


def test_alert_group_serialization_roundtrip() -> None:
    """Pydantic JSON 序列化和反序列化应保留根告警与衍生告警。"""
    group = AlertGroup(root_alert=_alert("1"))
    group.derived_alerts.append(_alert("2"))

    restored = AlertGroup.model_validate_json(group.model_dump_json())

    assert restored.root_alert.event_id == "1"
    assert restored.derived_alerts[0].event_id == "2"


def test_quick_filter_definitely_not_when_time_diff_over_5min() -> None:
    from src.correlator.quick_filter import Verdict, quick_filter

    new = _alert("99", t=datetime.now(UTC))
    root = _alert("1", t=datetime.now(UTC) - timedelta(minutes=10))

    assert quick_filter(new, AlertGroup(root_alert=root)) == Verdict.DEFINITELY_NOT


def test_quick_filter_definitely_related_same_ip() -> None:
    from src.correlator.quick_filter import Verdict, quick_filter

    new = _alert("99", host_ip="10.0.0.1")
    root = _alert("1", host_ip="10.0.0.1")

    assert quick_filter(new, AlertGroup(root_alert=root)) == Verdict.DEFINITELY_RELATED


def test_quick_filter_definitely_not_different_subnet_and_service() -> None:
    from src.correlator.quick_filter import Verdict, quick_filter

    new = _alert("99", host_ip="172.16.0.1", event_name="redis OOM")
    root = _alert("1", host_ip="10.0.0.1", event_name="nginx down")

    assert quick_filter(new, AlertGroup(root_alert=root)) == Verdict.DEFINITELY_NOT


def test_quick_filter_uncertain_otherwise() -> None:
    """同子网但不同主机和不同服务时交给 LLM。"""
    from src.correlator.quick_filter import Verdict, quick_filter

    new = _alert("99", host_ip="10.0.0.1", event_name="nginx down")
    root = _alert("1", host_ip="10.0.0.2", event_name="redis OOM")

    assert quick_filter(new, AlertGroup(root_alert=root)) == Verdict.UNCERTAIN


@pytest.mark.asyncio
async def test_llm_judge_returns_related_or_not() -> None:
    """LLM 返回 related=true 时应返回关联。"""
    from src.correlator.llm_judge import llm_judge

    with patch(
        "src.correlator.llm_judge._call_llm",
        new=AsyncMock(return_value={"related": True, "reason": "服务调用关系"}),
    ):
        ok, reason = await llm_judge(
            alert_summary="cache-01 redis OOM",
            group_summary="db-01 mysql conn timeout",
        )

    assert ok is True
    assert "服务" in reason


@pytest.mark.asyncio
async def test_llm_judge_cache_hit_avoids_llm_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """相同输入第二次调用应走缓存，避免重复 LLM 调用。"""
    from src.correlator import llm_judge as mod

    call_count = {"n": 0}

    async def counting_call(prompt: str) -> dict[str, Any]:
        call_count["n"] += 1
        return {"related": False, "reason": "无关"}

    monkeypatch.setattr(mod, "_call_llm", counting_call)
    cast(Any, mod.llm_judge).cache_clear()

    await mod.llm_judge("a", "b")
    await mod.llm_judge("a", "b")
    await mod.llm_judge("a", "c")

    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_llm_judge_returns_safe_default_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 抛错时保守返回不关联，避免误合并。"""
    from src.correlator import llm_judge as mod

    async def failing(prompt: str) -> dict[str, Any]:
        raise RuntimeError("LLM down")

    monkeypatch.setattr(mod, "_call_llm", failing)
    cast(Any, mod.llm_judge).cache_clear()

    ok, reason = await mod.llm_judge("x", "y")

    assert ok is False
    assert "失败" in reason


@pytest.mark.asyncio
async def test_group_store_save_and_get(redis_client: Any) -> None:
    from src.correlator.groups import GroupStore

    store = GroupStore(redis_client)
    group = AlertGroup(root_alert=_alert("1", host_ip="10.0.0.1"))

    await store.save(group)
    fetched = await store.get(group.group_id)

    assert fetched is not None
    assert fetched.root_alert.event_id == "1"


@pytest.mark.asyncio
async def test_group_store_active_groups_within_window(redis_client: Any) -> None:
    """active_groups 只返回当前窗口内创建的 group。"""
    from src.correlator.groups import GroupStore

    store = GroupStore(redis_client, window_sec=30)
    await store.save(AlertGroup(root_alert=_alert("1")))

    active = await store.active_groups()

    assert len(active) == 1
    assert active[0].group_id == "1"


@pytest.mark.asyncio
async def test_group_store_ttl_expires(redis_client: Any) -> None:
    """超出窗口的 group 应被 Redis TTL 自动清理。"""
    from src.correlator.groups import GroupStore

    store = GroupStore(redis_client, window_sec=1)
    await store.save(AlertGroup(root_alert=_alert("1")))

    await asyncio.sleep(2)
    active = await store.active_groups()

    assert active == []


@pytest.mark.asyncio
async def test_correlate_creates_new_group_when_no_active(redis_client: Any) -> None:
    from src.correlator.correlate import correlate
    from src.correlator.groups import GroupStore

    group = await correlate(_alert("1"), GroupStore(redis_client))

    assert group.group_id == "1"
    assert group.derived_alerts == []


@pytest.mark.asyncio
async def test_correlate_attaches_to_existing_group_same_host(redis_client: Any) -> None:
    """同 host 由 quick_filter 判定关联，应加入现有 group。"""
    from src.correlator.correlate import correlate
    from src.correlator.groups import GroupStore

    store = GroupStore(redis_client)
    group1 = await correlate(_alert("1", host_ip="10.0.0.1"), store)
    group2 = await correlate(_alert("2", host_ip="10.0.0.1"), store)

    assert group1.group_id == group2.group_id
    assert len(group2.derived_alerts) == 1
    assert group2.derived_alerts[0].event_id == "2"


@pytest.mark.asyncio
async def test_correlate_creates_independent_group_unrelated_hosts(redis_client: Any) -> None:
    from src.correlator.correlate import correlate
    from src.correlator.groups import GroupStore

    store = GroupStore(redis_client)
    group1 = await correlate(_alert("1", host_ip="10.0.0.1", event_name="nginx down"), store)
    group2 = await correlate(_alert("2", host_ip="172.16.0.1", event_name="redis OOM"), store)

    assert group1.group_id != group2.group_id


@pytest.mark.asyncio
async def test_correlate_uses_llm_for_uncertain(redis_client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """quick_filter 返回 uncertain 时调用 LLM 判定。"""
    from src.correlator import correlate as corr_mod
    from src.correlator.groups import GroupStore

    async def fake_judge(alert_summary: str, group_summary: str) -> tuple[bool, str]:
        return True, "LLM 判定关联"

    monkeypatch.setattr(corr_mod, "llm_judge", fake_judge)

    store = GroupStore(redis_client)
    group1 = await corr_mod.correlate(_alert("1", host_ip="10.0.0.1", event_name="db connection timeout"), store)
    group2 = await corr_mod.correlate(_alert("2", host_ip="10.0.0.2", event_name="db slow query"), store)

    assert group1.group_id == group2.group_id


@pytest.mark.asyncio
async def test_correlate_concurrent_same_host_creates_one_group(redis_client: Any) -> None:
    """并发消费同 host 告警时应通过关联锁避免创建两个根因组。"""
    from src.correlator.correlate import correlate
    from src.correlator.groups import GroupStore

    store = GroupStore(redis_client)
    alert1 = _alert("1", host_ip="10.0.0.1")
    alert2 = _alert("2", host_ip="10.0.0.1")

    group1, group2 = await asyncio.gather(correlate(alert1, store), correlate(alert2, store))
    active = await store.active_groups()

    assert group1.group_id == group2.group_id
    assert len(active) == 1
    assert {active[0].root_alert.event_id, *(alert.event_id for alert in active[0].derived_alerts)} == {"1", "2"}
