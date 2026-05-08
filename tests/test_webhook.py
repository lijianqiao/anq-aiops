from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.config import settings
from src.main import app


@pytest.fixture
def zabbix_payload() -> dict[str, Any]:
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
def _mock_app_state(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    monkeypatch.setattr(settings, "zabbix_webhook_token", "test-zabbix-token")
    app.state.redis = MagicMock()
    app.state.temporal = MagicMock()

    class AllowLimiter:
        async def try_acquire(self) -> bool:
            return True

    class HealthyGuard:
        async def is_overloaded(self) -> bool:
            return False

    monkeypatch.setattr("src.api.webhook.RateLimiter", lambda *args, **kwargs: AllowLimiter())
    monkeypatch.setattr("src.api.webhook.SystemOverloadGuard", lambda *args, **kwargs: HealthyGuard())
    yield
    del app.state.redis
    del app.state.temporal


def _zabbix_headers() -> dict[str, str]:
    return {"X-Zabbix-Token": "test-zabbix-token"}


@pytest.mark.asyncio
async def test_zabbix_webhook_success(zabbix_payload: dict[str, Any]) -> None:
    with patch("src.api.webhook.produce_alert", new_callable=AsyncMock) as mock_produce:
        mock_produce.return_value = "1234567890-0"

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/webhook/zabbix", json=zabbix_payload, headers=_zabbix_headers())

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "accepted"
        assert data["event_id"] == "12345"


@pytest.mark.asyncio
async def test_zabbix_webhook_duplicate(zabbix_payload: dict[str, Any]) -> None:
    with patch("src.api.webhook.produce_alert", new_callable=AsyncMock) as mock_produce:
        mock_produce.return_value = None

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/webhook/zabbix", json=zabbix_payload, headers=_zabbix_headers())

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "duplicate"


@pytest.mark.asyncio
async def test_zabbix_webhook_invalid_payload() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhook/zabbix", json={"bad": "data"}, headers=_zabbix_headers())

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_zabbix_webhook_rejects_invalid_token(zabbix_payload: dict[str, Any]) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhook/zabbix", json=zabbix_payload, headers={"X-Zabbix-Token": "bad"})

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_webhook_rejects_when_rate_limit_exceeded(zabbix_payload: dict[str, Any]) -> None:
    """超过入口限流时返回 429，且不写入告警流。"""
    with patch("src.api.webhook.RateLimiter") as mock_limiter_cls:
        limiter = mock_limiter_cls.return_value
        limiter.try_acquire = AsyncMock(return_value=False)

        with patch("src.api.webhook.produce_alert", new_callable=AsyncMock) as mock_produce:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/webhook/zabbix", json=zabbix_payload, headers=_zabbix_headers())

    assert resp.status_code == 429
    assert "限流" in resp.text
    mock_produce.assert_not_called()


@pytest.mark.asyncio
async def test_webhook_rejects_when_overloaded(zabbix_payload: dict[str, Any]) -> None:
    """pending workflow 过多时返回 503，入口退回人工。"""
    with patch("src.api.webhook.SystemOverloadGuard") as mock_guard_cls:
        guard = mock_guard_cls.return_value
        guard.is_overloaded = AsyncMock(return_value=True)

        with patch("src.api.webhook.produce_alert", new_callable=AsyncMock) as mock_produce:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/webhook/zabbix", json=zabbix_payload, headers=_zabbix_headers())

    assert resp.status_code == 503
    assert "过载" in resp.text
    mock_produce.assert_not_called()
