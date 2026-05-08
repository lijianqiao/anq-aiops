from contextlib import suppress
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
import redis.asyncio as aioredis
from redis.exceptions import ResponseError

from src.bus.consumer import STREAM_KEY, ack_alert, consume_alert, reclaim_pending_alert
from src.bus.producer import produce_alert
from src.models import Alert


@pytest.fixture
def alert() -> Alert:
    return Alert(
        event_id="evt-001",
        event_name="Disk full",
        severity="high",
        hostname="web-01",
        host_ip="10.0.0.1",
        trigger_id="100",
        message="Disk usage 95%",
        timestamp=datetime(2026, 1, 1, 12, 0, 0),
        status="problem",
    )


@pytest.fixture
async def redis_client():
    client = aioredis.from_url("redis://localhost:6379/0")
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.mark.asyncio
async def test_produce_and_consume(alert: Alert, redis_client: aioredis.Redis) -> None:
    msg_id = await produce_alert(redis_client, alert)
    assert msg_id is not None
    with suppress(ResponseError):
        await redis_client.xgroup_create("aiops:alerts", "aiops-workers", id="0", mkstream=True)
    result = await consume_alert(redis_client, "aiops-workers", "test-consumer")
    assert result is not None
    consumed_alert, _ = result
    assert consumed_alert.event_id == "evt-001"
    assert consumed_alert.hostname == "web-01"


@pytest.mark.asyncio
async def test_produce_duplicate_rejected(alert: Alert, redis_client: aioredis.Redis) -> None:
    msg_id1 = await produce_alert(redis_client, alert)
    assert msg_id1 is not None
    msg_id2 = await produce_alert(redis_client, alert)
    assert msg_id2 is None


@pytest.mark.asyncio
async def test_consume_empty(redis_client: aioredis.Redis) -> None:
    with suppress(ResponseError):
        await redis_client.xgroup_create("aiops:alerts", "aiops-workers", id="0", mkstream=True)
    result = await consume_alert(redis_client, "aiops-workers", "test-consumer", block_ms=100)
    assert result is None


@pytest.mark.asyncio
async def test_consume_does_not_ack_before_workflow_start(alert: Alert) -> None:
    client = MagicMock()
    client.xreadgroup = AsyncMock(return_value=[(STREAM_KEY, [(b"1-0", {b"data": alert.model_dump_json().encode()})])])
    client.xack = AsyncMock()

    result = await consume_alert(client, "aiops-workers", "test-consumer", block_ms=100)

    assert result is not None
    client.xack.assert_not_called()


@pytest.mark.asyncio
async def test_ack_alert_acks_after_workflow_start() -> None:
    client = MagicMock()
    client.xack = AsyncMock()

    await ack_alert(client, "aiops-workers", "1-0")

    client.xack.assert_awaited_once_with(STREAM_KEY, "aiops-workers", "1-0")


@pytest.mark.asyncio
async def test_reclaim_pending_alert(alert: Alert) -> None:
    client = MagicMock()
    client.xautoclaim = AsyncMock(return_value=[b"0-0", [(b"1-0", {b"data": alert.model_dump_json().encode()})], []])

    result = await reclaim_pending_alert(client, "aiops-workers", "test-consumer", min_idle_ms=100)

    assert result is not None
    reclaimed_alert, msg_id = result
    assert reclaimed_alert.event_id == alert.event_id
    assert msg_id == "1-0"
