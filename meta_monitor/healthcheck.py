"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: healthcheck.py
@DateTime: 2026-05-08 14:10:43 UTC
@Docs: 元监控单轮探测、失败去重与恢复告警
"""

import os
import sys
import time

from meta_monitor import probes
from meta_monitor.feishu_alert import send_alert

_alert_state: dict[str, dict[str, float]] = {}
_DEDUP_WINDOW = 300.0


def run_once() -> list[str]:
    """
    执行一轮所有 probe

    Returns:
        当前失败的组件名称列表
    """
    now = time.monotonic()
    failures: list[str] = []

    for name, probe in probes.PROBES.items():
        try:
            ok, msg = probe()
        except Exception as exc:  # noqa: BLE001
            ok = False
            msg = f"probe crashed: {exc}"

        if ok:
            _handle_recovery(name, msg, now)
            continue

        failures.append(name)
        _handle_failure(name, msg, now)

    return failures


def _handle_failure(name: str, msg: str, now: float) -> None:
    """
    处理组件失败状态

    Args:
        name: 组件名称
        msg: 失败诊断信息
        now: 当前 monotonic 时间
    """
    state = _alert_state.get(name) or {}
    last_alerted = state.get("alerted_at", 0.0)
    if now - last_alerted < _DEDUP_WINDOW:
        return

    alert_message = f"⚠️ AIOps healthcheck FAIL [{name}]: {msg}"
    print(alert_message, file=sys.stderr)
    send_alert(alert_message)
    _alert_state[name] = {
        "failed_at": state.get("failed_at") or now,
        "alerted_at": now,
    }


def _handle_recovery(name: str, msg: str, now: float) -> None:
    """
    处理组件恢复状态

    Args:
        name: 组件名称
        msg: 恢复诊断信息
        now: 当前 monotonic 时间
    """
    if name not in _alert_state:
        return

    duration = int(now - _alert_state[name].get("failed_at", now))
    alert_message = f"✅ AIOps healthcheck RECOVERED [{name}] after {duration}s: {msg}"
    print(alert_message, file=sys.stderr)
    send_alert(alert_message)
    _alert_state.pop(name)


def main_loop(interval_sec: int = 60) -> None:
    """
    阻塞式主循环，作为容器入口点使用

    Args:
        interval_sec: 每轮探测间隔秒数
    """
    print(f"meta_monitor started, probing every {interval_sec}s", file=sys.stderr)
    while True:
        run_once()
        time.sleep(interval_sec)


if __name__ == "__main__":
    main_loop(int(os.environ.get("META_INTERVAL_SEC", "60")))
