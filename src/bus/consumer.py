import contextlib
import logging

import redis.asyncio as aioredis

from src.config import settings
from src.models import Alert

STREAM_KEY = "aiops:alerts"
logger = logging.getLogger(__name__)


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
    await client.xack(STREAM_KEY, group, msg_id)
    return alert, msg_id


async def start_consumer_loop(app) -> None:
    """持续消费 Redis Stream，触发 Temporal Workflow"""
    redis = app.state.redis
    temporal = app.state.temporal
    with contextlib.suppress(Exception):
        await redis.xgroup_create("aiops:alerts", "aiops-workers", id="0", mkstream=True)
    logger.info("Consumer loop started")
    while True:
        result = await consume_alert(redis, "aiops-workers", "worker-1", block_ms=5000)
        if result is None:
            continue
        alert, msg_id = result
        workflow_id = f"alert-{alert.event_id}"
        try:
            await temporal.start_workflow(
                "AlertWorkflow",
                alert.model_dump_json(),
                id=workflow_id,
                task_queue=settings.temporal_task_queue,
            )
            logger.info(f"Workflow started: {workflow_id}")
        except Exception as e:
            logger.error(f"Failed to start workflow for {alert.event_id}: {e}")
