from contextlib import suppress
from datetime import datetime

import pytest
import redis.asyncio as aioredis
from redis.exceptions import ResponseError

from src.bus.consumer import consume_alert
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
