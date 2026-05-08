"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: groups.py
@DateTime: 2026-05-08 22:46:00
@Docs: 提供 AlertGroup 的 Redis 持久化和活跃窗口查询
"""

from collections.abc import Awaitable, Iterable
from typing import Any, cast

import redis.asyncio as aioredis

from src.models import AlertGroup


class GroupStore:
    """AlertGroup Redis 持久化仓储。"""

    _ACTIVE_SET = "correlator:groups:active"

    def __init__(self, redis: aioredis.Redis, window_sec: int = 30) -> None:
        self.redis = redis
        self.window_sec = window_sec

    @staticmethod
    def _group_key(group_id: str) -> str:
        """返回 group 数据 key。"""
        return f"correlator:group:{group_id}"

    async def save(self, group: AlertGroup) -> None:
        """保存 group，并让 Redis 在关联窗口结束后自动过期。"""
        async with self.redis.pipeline(transaction=False) as pipe:
            pipe.set(self._group_key(group.group_id), group.model_dump_json(), ex=self.window_sec)
            pipe.sadd(self._ACTIVE_SET, group.group_id)
            pipe.expire(self._ACTIVE_SET, self.window_sec)
            await pipe.execute()

    async def get(self, group_id: str) -> AlertGroup | None:
        """按 group_id 获取关联组。"""
        raw = await self.redis.get(self._group_key(group_id))
        if raw is None:
            return None
        return AlertGroup.model_validate_json(raw)

    async def active_groups(self) -> list[AlertGroup]:
        """返回当前关联窗口内仍活跃的所有 group。"""
        ids: Iterable[str | bytes] = await cast(Awaitable[Iterable[str | bytes]], self.redis.smembers(self._ACTIVE_SET))
        if not ids:
            return []

        id_list = [group_id.decode("utf-8") if isinstance(group_id, bytes) else group_id for group_id in ids]
        async with self.redis.pipeline(transaction=False) as pipe:
            for group_id in id_list:
                pipe.get(self._group_key(group_id))
            raws = await pipe.execute()

        groups: list[AlertGroup] = []
        for group_id, raw in zip(id_list, raws, strict=True):
            if raw is None:
                await cast(Awaitable[Any], self.redis.srem(self._ACTIVE_SET, group_id))
                continue
            groups.append(AlertGroup.model_validate_json(raw))
        return groups
