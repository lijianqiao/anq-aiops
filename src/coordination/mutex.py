"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: mutex.py
@DateTime: 2026-05-08 22:46:00
@Docs: 基于 Redis SET NX EX 和 Lua CAS 的 Action Mutex
"""

import secrets
from collections.abc import Awaitable
from typing import Any, Final, cast

import redis.asyncio as aioredis

_RELEASE_LUA: Final[str] = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


def _lock_key(target: str) -> str:
    """返回互斥目标对应的 Redis key。"""
    return f"coord:action_mutex:{target}"


async def acquire_action_mutex(redis: aioredis.Redis, target: str, ttl: int = 300) -> str | None:
    """原子获取互斥锁，成功返回 token，失败返回 None。"""
    token = secrets.token_urlsafe(16)
    ok = await redis.set(_lock_key(target), token, nx=True, ex=ttl)
    return token if ok else None


async def release_action_mutex(redis: aioredis.Redis, target: str, token: str) -> bool:
    """释放互斥锁，只有 token 匹配时才删除锁。"""
    result = await cast(Awaitable[Any], redis.eval(_RELEASE_LUA, 1, _lock_key(target), token))
    return bool(result)
