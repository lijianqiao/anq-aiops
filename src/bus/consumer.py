"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: consumer.py
@DateTime: 2026-05-08 14:33:00
@Docs: 消费 Redis Stream 告警并启动 Temporal Workflow
"""

import asyncio
import contextlib
import logging
from typing import Any

import redis.asyncio as aioredis
from fastapi import FastAPI
from temporalio.exceptions import WorkflowAlreadyStartedError

from src.config import settings
from src.coordination.rate_limit import PendingWorkflowGauge
from src.correlator.correlate import correlate
from src.correlator.groups import GroupStore
from src.models import Alert

STREAM_KEY = "aiops:alerts"
logger = logging.getLogger(__name__)


def _decode_message_id(msg_id: str | bytes) -> str:
    """Redis 返回 bytes 时统一解码为字符串，便于后续 ack 与 workflow 记录。"""
    if isinstance(msg_id, bytes):
        return msg_id.decode("utf-8")
    return str(msg_id)


async def consume_alert(
    client: aioredis.Redis,
    group: str,
    consumer: str,
    block_ms: int = 5000,
) -> tuple[Alert, str] | None:
    """从 Redis Stream 消费一条告警。返回 (Alert, message_id) 或 None"""
    results = await client.xreadgroup(
        groupname=group,
        consumername=consumer,
        streams={STREAM_KEY: ">"},
        count=1,
        block=block_ms,
    )
    if not results:
        return None
    _stream, messages = results[0]
    msg_id, fields = messages[0]
    raw = fields[b"data"].decode("utf-8")
    alert = Alert.model_validate_json(raw)
    return alert, _decode_message_id(msg_id)


async def reclaim_pending_alert(
    client: aioredis.Redis,
    group: str,
    consumer: str,
    min_idle_ms: int = 60000,
) -> tuple[Alert, str] | None:
    """Claim one stale pending message so crashed consumers do not strand alerts."""
    result = await client.xautoclaim(
        name=STREAM_KEY,
        groupname=group,
        consumername=consumer,
        min_idle_time=min_idle_ms,
        start_id="0-0",
        count=1,
    )
    messages = result[1] if len(result) > 1 else []
    if not messages:
        return None
    msg_id, fields = messages[0]
    raw = fields[b"data"].decode("utf-8")
    alert = Alert.model_validate_json(raw)
    return alert, _decode_message_id(msg_id)


async def ack_alert(client: aioredis.Redis, group: str, msg_id: str) -> None:
    await client.xack(STREAM_KEY, group, msg_id)


async def handle_alert_message(
    redis: aioredis.Redis,
    temporal: Any,
    alert: Alert,
    msg_id: str,
    store: GroupStore,
    gauge: PendingWorkflowGauge,
) -> str:
    """处理单条告警消息：关联、抑制衍生告警或启动根因 workflow。"""
    group = await correlate(alert, store)
    if alert.event_id != group.root_alert.event_id:
        await ack_alert(redis, "aiops-workers", msg_id)
        logger.info(f"衍生告警 {alert.event_id} 已抑制，根因告警为 {group.root_alert.event_id}")
        return "suppressed"

    workflow_id = f"alert-{alert.event_id}"
    gauge_incremented = False
    try:
        await gauge.incr()
        gauge_incremented = True
        await temporal.start_workflow(
            "AlertWorkflow",
            alert.model_dump_json(),
            id=workflow_id,
            task_queue=settings.temporal_task_queue,
        )
        await ack_alert(redis, "aiops-workers", msg_id)
        logger.info(f"Workflow started: {workflow_id}")
        return "started"
    except WorkflowAlreadyStartedError:
        if gauge_incremented:
            await gauge.decr()
        await ack_alert(redis, "aiops-workers", msg_id)
        logger.info(f"Workflow already exists, acked message: {workflow_id}")
        return "already_started"
    except Exception:
        if gauge_incremented:
            await gauge.decr()
        raise


async def start_consumer_loop(app: FastAPI) -> None:
    """持续消费 Redis Stream，触发 Temporal Workflow"""
    redis = app.state.redis
    temporal = app.state.temporal
    store = GroupStore(redis, window_sec=settings.correlator_window_sec)
    gauge = PendingWorkflowGauge(redis)
    with contextlib.suppress(Exception):
        await redis.xgroup_create("aiops:alerts", "aiops-workers", id="0", mkstream=True)
    logger.info("Consumer loop started")
    while True:
        result = await reclaim_pending_alert(redis, "aiops-workers", "worker-1")
        if result is None:
            result = await consume_alert(redis, "aiops-workers", "worker-1", block_ms=5000)
        if result is None:
            continue
        alert, msg_id = result
        try:
            await handle_alert_message(redis, temporal, alert, msg_id, store, gauge)
        except Exception as e:
            logger.error(f"Failed to start workflow for {alert.event_id}: {e}")
            # 不 ack，让 reclaim 重投；sleep 避免紧循环打满 CPU
            await asyncio.sleep(5)
