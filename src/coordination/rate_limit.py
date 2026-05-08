"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: rate_limit.py
@DateTime: 2026-05-08 22:46:00
@Docs: 提供告警风暴限流、pending workflow 计数和系统过载保护
"""

import time

import redis.asyncio as aioredis


class RateLimiter:
    """固定窗口计数器限流器。"""

    def __init__(self, redis: aioredis.Redis, key: str, limit: int, window_sec: int) -> None:
        self.redis = redis
        self._key_prefix = f"coord:rate:{key}"
        self.limit = limit
        self.window_sec = window_sec

    def _bucket_key(self, ts: int | None = None) -> str:
        """返回当前时间窗口对应的 Redis key。"""
        current_ts = int(time.time()) if ts is None else ts
        return f"{self._key_prefix}:{current_ts // self.window_sec}"

    async def try_acquire(self) -> bool:
        """尝试消耗一个 token，超过窗口上限时返回 False。"""
        key = self._bucket_key()
        async with self.redis.pipeline(transaction=False) as pipe:
            pipe.incr(key)
            pipe.expire(key, self.window_sec)
            count, _ = await pipe.execute()
        return int(count) <= self.limit


class PendingWorkflowGauge:
    """全局 in-flight workflow 计数器。"""

    KEY = "coord:pending_workflows"

    def __init__(self, redis: aioredis.Redis) -> None:
        self.redis = redis

    async def incr(self) -> int:
        """计数加一。"""
        return int(await self.redis.incr(self.KEY))

    async def decr(self) -> int:
        """计数减一，低于 0 时自动修正为 0。"""
        value = int(await self.redis.decr(self.KEY))
        if value < 0:
            await self.redis.set(self.KEY, 0)
            return 0
        return value

    async def count(self) -> int:
        """读取当前 pending workflow 数量。"""
        value = await self.redis.get(self.KEY)
        return int(value) if value else 0


class SystemOverloadGuard:
    """pending workflow 超过阈值时触发系统过载保护。"""

    def __init__(self, redis: aioredis.Redis, max_pending: int = 50) -> None:
        self.gauge = PendingWorkflowGauge(redis)
        self.max_pending = max_pending

    async def is_overloaded(self) -> bool:
        """判断系统是否已过载。"""
        return await self.gauge.count() >= self.max_pending
