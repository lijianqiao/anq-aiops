"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: coordination.py
@DateTime: 2026-05-08 22:52:00
@Docs: 提供 workflow 协同相关 Temporal Activities
"""

import logging
from typing import Any

from temporalio import activity

logger = logging.getLogger(__name__)
redis_client: Any | None = None


@activity.defn
async def decr_pending_gauge() -> None:
    """workflow 完成时减少 pending workflow 计数。"""
    if redis_client is None:
        return
    try:
        from src.coordination.rate_limit import PendingWorkflowGauge

        await PendingWorkflowGauge(redis_client).decr()
    except Exception as exc:
        logger.warning(f"减少 pending workflow 计数失败：{exc}")


@activity.defn
async def try_acquire_mutex(target: str, ttl: int = 600) -> str:
    """尝试获取 Action Mutex，失败返回空字符串。"""
    if redis_client is None:
        return "skip"

    from src.coordination.mutex import acquire_action_mutex

    token = await acquire_action_mutex(redis_client, target, ttl)
    return token or ""


@activity.defn
async def release_mutex(target: str, token: str) -> None:
    """释放 Action Mutex。"""
    if not token or token == "skip" or redis_client is None:
        return

    from src.coordination.mutex import release_action_mutex

    await release_action_mutex(redis_client, target, token)
