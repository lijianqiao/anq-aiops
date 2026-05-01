from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app


@pytest.fixture
def zabbix_payload() -> dict:
    return {
        "event_id": "12345",
        "event_name": "Disk usage > 90%",
        "severity": "high",
        "hostname": "web-server-01",
        "host_ip": "192.168.1.13",
        "trigger_id": "10001",
        "message": "Disk usage is 95% on /tmp",
        "timestamp": "2026-04-30T14:30:00Z",
        "status": "problem",
    }


@pytest.fixture(autouse=True)
def _mock_app_state():
    app.state.redis = MagicMock()
    yield
    del app.state.redis


@pytest.mark.asyncio
async def test_zabbix_webhook_success(zabbix_payload: dict) -> None:
    with patch("src.api.webhook.produce_alert", new_callable=AsyncMock) as mock_produce:
        mock_produce.return_value = "1234567890-0"

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/webhook/zabbix", json=zabbix_payload)

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "accepted"
        assert data["event_id"] == "12345"


@pytest.mark.asyncio
async def test_zabbix_webhook_duplicate(zabbix_payload: dict) -> None:
    with patch("src.api.webhook.produce_alert", new_callable=AsyncMock) as mock_produce:
        mock_produce.return_value = None

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/webhook/zabbix", json=zabbix_payload)

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "duplicate"


@pytest.mark.asyncio
async def test_zabbix_webhook_invalid_payload() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhook/zabbix", json={"bad": "data"})

    assert resp.status_code == 422
