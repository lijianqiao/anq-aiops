"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: incident_summary.py
@DateTime: 2026-05-08 23:45:00
@Docs: 生成并推送 workflow 完成后的飞书故障简报
"""

import logging
from typing import cast

from pydantic import BaseModel
from temporalio import activity

import src.activities.llm as llm_activities
from src.activities.feishu import _post_im_message

logger = logging.getLogger(__name__)


class _Summary(BaseModel):
    """LLM 故障简报摘要输出。"""

    summary: str = ""


def _render_summary(
    *,
    event_id: str,
    decision: str,
    runbook_id: str | None,
    verify: bool | None,
    host_ip: str,
    event_name: str,
    agent_reasoning: str,
    llm_summary: str,
) -> str:
    """渲染飞书 markdown 故障简报。"""
    icon = "✅" if verify else ("❌" if verify is False else "⚠️")
    verify_text = "通过 ✅" if verify else ("未通过 ❌" if verify is False else "无")
    return f"""**{icon} 故障简报 #{event_id}**

- **告警**: {event_name} on `{host_ip}`
- **决策**: {decision}
- **Runbook**: `{runbook_id or "无"}`
- **验证**: {verify_text}

**根因/决策依据**: {agent_reasoning[:300] or "无"}

**经验沉淀**: {llm_summary}
"""


@activity.defn
async def post_incident_summary(
    event_id: str,
    decision: str,
    runbook_id: str | None,
    verify_str: str,
    host_ip: str,
    event_name: str,
    agent_reasoning: str,
) -> None:
    """生成并发送故障简报；失败只记录日志，不影响主流程。"""
    verify = _parse_verify(verify_str)
    llm_summary = "LLM 总结不可用"

    if llm_activities.llm_router is not None:
        prompt = f"""请用 1 到 2 句话总结这次 AIOps 告警处置经验，少于 80 字，返回 JSON。

告警: {event_name} on {host_ip}
决策: {decision}
Runbook: {runbook_id}
验证: {verify}
Agent 推理: {agent_reasoning[:500]}

输出格式：{{"summary": "中文总结"}}
"""
        try:
            result = cast(_Summary, await llm_activities.llm_router.invoke(prompt, _Summary))
            llm_summary = result.summary[:200] or llm_summary
        except Exception as exc:
            logger.warning(f"故障简报 LLM 总结失败：{exc}")

    markdown = _render_summary(
        event_id=event_id,
        decision=decision,
        runbook_id=runbook_id,
        verify=verify,
        host_ip=host_ip,
        event_name=event_name,
        agent_reasoning=agent_reasoning,
        llm_summary=llm_summary,
    )
    try:
        await _post_im_message(msg_type="text", content={"text": markdown})
    except Exception as exc:
        logger.warning(f"故障简报飞书发送失败：{exc}")


def _parse_verify(value: str) -> bool | None:
    """解析 workflow 传入的 verify 字符串。"""
    if value == "true":
        return True
    if value == "false":
        return False
    return None
