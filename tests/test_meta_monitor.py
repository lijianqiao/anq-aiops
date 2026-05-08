"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: test_meta_monitor.py
@DateTime: 2026-05-08 14:10:43 UTC
@Docs: meta_monitor 元监控测试
"""

import httpx
import pytest
import respx
from httpx import Response


@pytest.mark.asyncio
async def test_feishu_alert_posts_text_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """正常路径：调用 send_alert 后，httpx 收到带正确 payload 的 POST"""
    from meta_monitor.feishu_alert import send_alert

    monkeypatch.setenv(
        "META_FEISHU_WEBHOOK_URL",
        "https://open.feishu.cn/open-apis/bot/v2/hook/test-token",
    )

    with respx.mock(base_url="https://open.feishu.cn") as mock:
        route = mock.post("/open-apis/bot/v2/hook/test-token").mock(
            return_value=Response(200, json={"StatusCode": 0, "code": 0})
        )
        send_alert("⚠️ AIOps healthcheck failed: temporal unreachable")

    assert route.called
    payload = route.calls.last.request.read().decode()
    assert "msg_type" in payload
    assert "AIOps healthcheck failed" in payload


def test_feishu_alert_silently_skips_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """未配置 META_FEISHU_WEBHOOK_URL 时不抛错，仅输出跳过信息"""
    from meta_monitor.feishu_alert import send_alert

    monkeypatch.delenv("META_FEISHU_WEBHOOK_URL", raising=False)
    send_alert("test message")
    captured = capsys.readouterr()
    assert "META_FEISHU_WEBHOOK_URL not configured" in captured.err


def test_feishu_alert_swallows_network_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """网络错误时输出错误信息但不抛异常，避免元监控自身成为故障源"""
    from meta_monitor.feishu_alert import send_alert

    monkeypatch.setenv("META_FEISHU_WEBHOOK_URL", "https://invalid.example.com/hook")

    with respx.mock() as mock:
        mock.post("https://invalid.example.com/hook").mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        send_alert("test")

    captured = capsys.readouterr()
    assert "feishu alert failed" in captured.err.lower()


class _DummySocket:
    """用于 TCP probe 测试的假 socket"""

    def close(self) -> None:
        """关闭假 socket"""


def test_probe_fastapi_ok() -> None:
    """FastAPI /health 返回 200 且 status=ok 时判定健康"""
    from meta_monitor.probes import probe_fastapi

    with respx.mock(base_url="http://aiops") as mock:
        mock.get("/health").mock(return_value=Response(200, json={"status": "ok"}))
        ok, msg = probe_fastapi("http://aiops")

    assert ok is True
    assert "ok" in msg


def test_probe_fastapi_down() -> None:
    """FastAPI 连接失败时返回失败和错误原因"""
    from meta_monitor.probes import probe_fastapi

    with respx.mock() as mock:
        mock.get("http://aiops/health").mock(side_effect=httpx.ConnectError("refused"))
        ok, msg = probe_fastapi("http://aiops")

    assert ok is False
    assert "refused" in msg.lower() or "connect" in msg.lower()


def test_probe_temporal_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Temporal TCP 端口可连接时判定健康"""
    import socket

    from meta_monitor.probes import probe_temporal

    def fake_create(addr: tuple[str, int], timeout: float | None = None) -> _DummySocket:
        assert addr == ("temporal", 7233)
        assert timeout is not None
        return _DummySocket()

    monkeypatch.setattr(socket, "create_connection", fake_create)
    ok, msg = probe_temporal("tem" + "poral:7233")
    assert ok is True
    assert "ok" in msg


def test_probe_temporal_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """Temporal TCP 端口不可连接时返回失败"""
    import socket

    from meta_monitor.probes import probe_temporal

    def fake_create(addr: tuple[str, int], timeout: float | None = None) -> _DummySocket:
        raise ConnectionRefusedError("temporal down")

    monkeypatch.setattr(socket, "create_connection", fake_create)
    ok, msg = probe_temporal("tem" + "poral:7233")
    assert ok is False
    assert "temporal" in msg


def test_probe_redis_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redis TCP 端口可连接时判定健康"""
    import socket

    from meta_monitor.probes import probe_redis

    def fake_create(addr: tuple[str, int], timeout: float | None = None) -> _DummySocket:
        assert addr == ("redis", 6379)
        return _DummySocket()

    monkeypatch.setattr(socket, "create_connection", fake_create)
    ok, msg = probe_redis("redis:6379")
    assert ok is True
    assert "ok" in msg


def test_probe_redis_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redis TCP 端口不可连接时返回失败"""
    import socket

    from meta_monitor.probes import probe_redis

    def fake_create(addr: tuple[str, int], timeout: float | None = None) -> _DummySocket:
        raise TimeoutError("redis timeout")

    monkeypatch.setattr(socket, "create_connection", fake_create)
    ok, msg = probe_redis("redis:6379")
    assert ok is False
    assert "redis" in msg


def test_probe_lark_ws_ok() -> None:
    """飞书开放平台 HTTP 返回小于 500 时判定可达"""
    from meta_monitor.probes import probe_lark_ws

    with respx.mock() as mock:
        mock.get("https://open.feishu.cn/open-apis/").mock(return_value=Response(404))
        ok, msg = probe_lark_ws()

    assert ok is True
    assert "404" in msg


def test_probe_lark_ws_down() -> None:
    """飞书开放平台请求超时时返回失败"""
    from meta_monitor.probes import probe_lark_ws

    with respx.mock() as mock:
        mock.get("https://open.feishu.cn/open-apis/").mock(
            side_effect=httpx.ConnectTimeout("timeout")
        )
        ok, msg = probe_lark_ws()

    assert ok is False
    assert "timeout" in msg.lower()


def test_probe_llm_skipped_when_no_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """未配置 LLM_PRIMARY_BASE_URL 时跳过 LLM 探测并返回健康"""
    from meta_monitor.probes import probe_llm

    monkeypatch.delenv("LLM_PRIMARY_BASE_URL", raising=False)
    ok, msg = probe_llm()
    assert ok is True
    assert "skipped" in msg.lower()


def test_probe_llm_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 端点 5xx 时返回失败"""
    from meta_monitor.probes import probe_llm

    monkeypatch.setenv("LLM_PRIMARY_BASE_URL", "https://llm.example.com/v1")
    with respx.mock() as mock:
        mock.get("https://llm.example.com/v1").mock(return_value=Response(503))
        ok, msg = probe_llm()

    assert ok is False
    assert "503" in msg
