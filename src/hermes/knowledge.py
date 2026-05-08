"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: knowledge.py
@DateTime: 2026-05-08 23:16:00
@Docs: 查询相似历史案例并格式化为 Agent prompt 片段
"""

from src.hermes.models import AuditRecordRead
from src.hermes.repository import AuditRepository
from src.models import Alert


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
