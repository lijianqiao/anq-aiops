"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: quick_filter.py
@DateTime: 2026-05-08 22:45:00
@Docs: 提供告警关联启发式快筛规则
"""

from datetime import timedelta
from enum import StrEnum

from src.models import Alert, AlertGroup

_TIME_WINDOW = timedelta(seconds=30)


class Verdict(StrEnum):
    """启发式快筛结论。"""

    DEFINITELY_RELATED = "definitely_related"
    DEFINITELY_NOT = "definitely_not"
    UNCERTAIN = "uncertain"


def _same_subnet_16(ip_a: str, ip_b: str) -> bool:
    """用前两段 IP 粗略判断是否同 /16 子网。"""
    parts_a = ip_a.split(".")
    parts_b = ip_b.split(".")
    if len(parts_a) < 2 or len(parts_b) < 2:
        return False
    return parts_a[0] == parts_b[0] and parts_a[1] == parts_b[1]


def _first_word(text: str) -> str:
    """取事件名第一个词作为粗略服务名。"""
    words = text.lower().split()
    return words[0] if words else ""


def quick_filter(alert: Alert, group: AlertGroup) -> Verdict:
    """执行 4 条启发式规则，尽量避免不必要的 LLM 调用。"""
    root = group.root_alert

    if abs(alert.timestamp - root.timestamp) > _TIME_WINDOW:
        return Verdict.DEFINITELY_NOT

    if alert.host_ip == root.host_ip or alert.hostname == root.hostname:
        return Verdict.DEFINITELY_RELATED

    if not _same_subnet_16(alert.host_ip, root.host_ip) and _first_word(alert.event_name) != _first_word(root.event_name):
        return Verdict.DEFINITELY_NOT

    return Verdict.UNCERTAIN
