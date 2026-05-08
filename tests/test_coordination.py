"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: test_coordination.py
@DateTime: 2026-05-08 22:42:00
@Docs: 测试协同互斥锁、风暴限流和 pending workflow 计数
"""

import asyncio
from typing import Any

import pytest
import redis.asyncio as aioredis

from src.coordination.mutex import acquire_action_mutex, release_action_mutex


@pytest.fixture
async def redis_client() -> Any:
    redis = aioredis.from_url("redis://localhost:6379/15")
    await redis.flushdb()
    yield redis
    await redis.flushdb()
    await redis.aclose()


@pytest.mark.asyncio
async def test_mutex_acquire_release(redis_client: Any) -> None:
    """同 target 第一次获锁成功，释放后再次成功。"""
    token1 = await acquire_action_mutex(redis_client, "host:1.1.1.1", ttl=10)

    assert token1 is not None

    await release_action_mutex(redis_client, "host:1.1.1.1", token1)
    token2 = await acquire_action_mutex(redis_client, "host:1.1.1.1", ttl=10)

    assert token2 is not None


@pytest.mark.asyncio
async def test_mutex_blocks_second_acquire(redis_client: Any) -> None:
    """没释放前第二次获锁返回 None。"""
    token1 = await acquire_action_mutex(redis_client, "host:1.1.1.1", ttl=10)

    assert token1 is not None

    token2 = await acquire_action_mutex(redis_client, "host:1.1.1.1", ttl=10)

    assert token2 is None


@pytest.mark.asyncio
async def test_mutex_release_safe_with_wrong_token(redis_client: Any) -> None:
    """错误 token 释放不应影响别人的锁。"""
    token = await acquire_action_mutex(redis_client, "host:1.1.1.1", ttl=10)

    assert token is not None

    await release_action_mutex(redis_client, "host:1.1.1.1", "wrong-token")
    token2 = await acquire_action_mutex(redis_client, "host:1.1.1.1", ttl=10)

    assert token2 is None


@pytest.mark.asyncio
async def test_mutex_ttl_auto_expires(redis_client: Any) -> None:
    """TTL 到了自动释放，避免 worker crash 后死锁。"""
    token = await acquire_action_mutex(redis_client, "host:1.1.1.1", ttl=1)

    assert token is not None

    await asyncio.sleep(2)
    token2 = await acquire_action_mutex(redis_client, "host:1.1.1.1", ttl=10)

    assert token2 is not None


@pytest.mark.asyncio
async def test_rate_limiter_allows_within_limit(redis_client: Any) -> None:
    """每分钟 5 个 token，前 5 次允许。"""
    from src.coordination.rate_limit import RateLimiter

    limiter = RateLimiter(redis_client, key="test", limit=5, window_sec=60)

    for index in range(5):
        ok = await limiter.try_acquire()
        assert ok is True, f"第 {index} 次应允许"


@pytest.mark.asyncio
async def test_rate_limiter_blocks_over_limit(redis_client: Any) -> None:
    from src.coordination.rate_limit import RateLimiter

    limiter = RateLimiter(redis_client, key="test", limit=3, window_sec=60)

    for _ in range(3):
        await limiter.try_acquire()

    assert await limiter.try_acquire() is False


@pytest.mark.asyncio
async def test_rate_limiter_resets_after_window(redis_client: Any) -> None:
    from src.coordination.rate_limit import RateLimiter

    limiter = RateLimiter(redis_client, key="test", limit=2, window_sec=1)

    await limiter.try_acquire()
    await limiter.try_acquire()
    assert await limiter.try_acquire() is False

    await asyncio.sleep(1.5)

    assert await limiter.try_acquire() is True


@pytest.mark.asyncio
async def test_pending_workflow_counter(redis_client: Any) -> None:
    from src.coordination.rate_limit import PendingWorkflowGauge

    gauge = PendingWorkflowGauge(redis_client)

    assert await gauge.count() == 0

    await gauge.incr()
    await gauge.incr()
    assert await gauge.count() == 2

    await gauge.decr()
    assert await gauge.count() == 1


@pytest.mark.asyncio
async def test_pending_workflow_counter_never_negative(redis_client: Any) -> None:
    """计数误减时应归零，避免过载保护被负数削弱。"""
    from src.coordination.rate_limit import PendingWorkflowGauge

    gauge = PendingWorkflowGauge(redis_client)

    assert await gauge.decr() == 0
    assert await gauge.count() == 0


@pytest.mark.asyncio
async def test_system_overloaded_when_too_many_pending(redis_client: Any) -> None:
    from src.coordination.rate_limit import PendingWorkflowGauge, SystemOverloadGuard

    gauge = PendingWorkflowGauge(redis_client)
    for _ in range(50):
        await gauge.incr()

    guard = SystemOverloadGuard(redis_client, max_pending=50)

    assert await guard.is_overloaded() is True
