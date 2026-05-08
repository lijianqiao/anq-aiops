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
