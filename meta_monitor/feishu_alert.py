"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: feishu_alert.py
@DateTime: 2026-05-08 14:10:43 UTC
@Docs: 独立飞书 webhook 文本告警
"""

import os
import sys

import httpx


def send_alert(message: str) -> None:
    """
    发文本到独立飞书运维告警群

    Args:
        message: 告警文本，会被包成飞书自定义机器人文本消息
    """
    url = os.environ.get("META_FEISHU_WEBHOOK_URL", "").strip()
    if not url:
        print("META_FEISHU_WEBHOOK_URL not configured, skipping alert", file=sys.stderr)
        return

    payload = {"msg_type": "text", "content": {"text": message}}
    try:
        with httpx.Client(timeout=5) as client:
            resp = client.post(url, json=payload)
        if resp.status_code != 200:
            print(
                f"feishu alert failed: HTTP {resp.status_code} body={resp.text[:200]}",
                file=sys.stderr,
            )
    except Exception as exc:  # noqa: BLE001
        print(f"feishu alert failed: {exc}", file=sys.stderr)
