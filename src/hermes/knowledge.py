"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: knowledge.py
@DateTime: 2026-05-08 23:16:00
@Docs: 查询相似历史案例并格式化为 Agent prompt 片段
"""

from typing import TYPE_CHECKING

from src.hermes.models import AuditRecordRead
from src.hermes.repository import AuditRepository
from src.models import Alert

if TYPE_CHECKING:
    from src.hermes.feedback import FeedbackRepository


async def query_similar_cases(
    repo: AuditRepository,
    alert: Alert,
    limit: int = 3,
) -> list[AuditRecordRead]:
    """根据当前告警查询历史相似案例。"""
    query = f"{alert.event_name} {alert.message}"
    return await repo.find_similar(
        query=query,
        host_ip=alert.host_ip,
        limit=limit,
        only_successful=False,
    )


def format_cases_for_prompt(cases: list[AuditRecordRead]) -> str:
    """将历史案例格式化为可注入 system prompt 的短文本。"""
    if not cases:
        return "未找到历史相似案例。"

    lines: list[str] = []
    for index, case in enumerate(cases, 1):
        verify_mark = "✅" if case.verify else ("❌" if case.verify is False else "？")
        lines.append(
            f"{index}. [{case.created_at.strftime('%Y-%m-%d')}] "
            f"`{case.event_name}` on {case.host_ip}\n"
            f"   - 决策: {case.decision} | Runbook: `{case.runbook_id}` | 参数: {case.runbook_params or {}}\n"
            f"   - 结果: {verify_mark} verify={case.verify}\n"
            f"   - 推理: {(case.agent_reasoning or '无')[:200]}"
        )
    return "\n".join(lines)


async def query_negative_cases(
    feedback_repo: FeedbackRepository,
    alert: Alert,
    limit: int = 2,
) -> list[AuditRecordRead]:
    """根据当前告警查询人工标注过的反例。"""
    query = f"{alert.event_name} {alert.message}"
    return await feedback_repo.find_negative_cases(query=query, host_ip=alert.host_ip, limit=limit)


def format_negative_cases(cases: list[AuditRecordRead]) -> str:
    """将反例格式化为 prompt 避坑段落。"""
    if not cases:
        return ""

    lines = ["**避坑案例**（运维标记 agent 之前判断错过的）："]
    for index, case in enumerate(cases, 1):
        reason = case.feedback_reason or ""
        label = case.feedback_label or "unknown"
        lines.append(
            f"{index}. [{case.created_at.strftime('%Y-%m-%d')}] {case.event_name} on {case.host_ip}\n"
            f"   - agent 当时提议: runbook=`{case.runbook_id}` params={case.runbook_params}\n"
            f"   - 运维反馈: {label} - {reason[:200]}"
        )
    return "\n".join(lines)
