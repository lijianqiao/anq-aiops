"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: correlate.py
@DateTime: 2026-05-08 22:46:00
@Docs: 提供告警到关联组的新建或合并主入口
"""

import asyncio
import logging

from src.coordination.mutex import acquire_action_mutex, release_action_mutex
from src.correlator.groups import GroupStore
from src.correlator.llm_judge import llm_judge
from src.correlator.quick_filter import Verdict, quick_filter
from src.models import Alert, AlertGroup

logger = logging.getLogger(__name__)


async def correlate(alert: Alert, store: GroupStore) -> AlertGroup:
    """决定新告警加入现有 group，还是新建独立 group。"""
    lock_target = f"correlator:host:{alert.host_ip}"
    lock_token = await _acquire_correlation_lock(store, lock_target)
    try:
        return await _correlate_locked(alert, store)
    finally:
        if lock_token is not None:
            await release_action_mutex(store.redis, lock_target, lock_token)


async def _acquire_correlation_lock(store: GroupStore, target: str) -> str | None:
    """短暂等待同 host 关联锁，避免多 consumer 同时创建根因组。"""
    for _ in range(20):
        token = await acquire_action_mutex(store.redis, target, ttl=10)
        if token is not None:
            return token
        await asyncio.sleep(0.05)
    logger.warning(f"获取告警关联锁超时：{target}")
    return None


async def _correlate_locked(alert: Alert, store: GroupStore) -> AlertGroup:
    """在同 host 关联锁内执行实际关联判断。"""
    for group in await store.active_groups():
        verdict = quick_filter(alert, group)
        if verdict == Verdict.DEFINITELY_RELATED:
            group.derived_alerts.append(alert)
            await store.save(group)
            return group
        if verdict == Verdict.DEFINITELY_NOT:
            continue

        related, reason = await llm_judge(
            alert_summary=f"{alert.host_ip} {alert.event_name}",
            group_summary=group.summary(),
        )
        if related:
            logger.info(f"LLM 判定告警 {alert.event_id} 合并到组 {group.group_id}：{reason}")
            group.derived_alerts.append(alert)
            await store.save(group)
            return group

    new_group = AlertGroup(root_alert=alert)
    await store.save(new_group)
    return new_group
