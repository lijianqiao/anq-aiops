"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: producer.py
@DateTime: 2026-05-08 14:33:00
@Docs: 将 Zabbix 告警写入 Redis Stream 并按事件 ID 去重
"""

from typing import cast

import redis.asyncio as aioredis

from src.models import Alert

STREAM_KEY = "aiops:alerts"


async def produce_alert(client: aioredis.Redis, alert: Alert) -> str | None:
    """写入 Redis Stream，同一 event_id 去重。返回消息 ID 或 None（重复）"""
    dedup_key = f"aiops:dedup:{alert.event_id}"
    is_new = await client.set(dedup_key, "1", nx=True, ex=3600)
    if not is_new:
        return None
    data = alert.model_dump_json()
    msg_id = await client.xadd(STREAM_KEY, {"data": data}, maxlen=10000)
    return cast(str, msg_id)
