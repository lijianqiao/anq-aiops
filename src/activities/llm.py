"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: llm.py
@DateTime: 2026-05-08 14:31:00
@Docs: 封装 Temporal LLM Activity，调用 ReAct Agent 完成告警诊断

LLM Activity：现在统一走 ReAct agent，一次 activity 完成 RCA + plan + risk

旧的 rca_analyze / plan_action / evaluate_risk 三个独立 activity 已废弃，
agent 在多轮 tool calling 中自己完成观察 → 推理 → 决策。
"""

import json
import logging

from temporalio import activity

from src.hermes import knowledge as hermes_knowledge
from src.hermes.feedback import FeedbackRepository
from src.hermes.repository import AuditRepository
from src.llm.agent import AgentLimitExceeded
from src.llm.router import LLMRouter, LLMUnavailable
from src.models import Alert

logger = logging.getLogger(__name__)

# 模块级 router，由 main.py lifespan 初始化
llm_router: LLMRouter | None = None
hermes_repo: AuditRepository | None = None
hermes_feedback: FeedbackRepository | None = None


@activity.defn
async def agent_diagnose(alert_json: str) -> str:
    """ReAct agent 诊断 + 给执行计划

    返回 JSON：
    {
      "plan": ActionPlan JSON 或 null（null 表示不修复仅通知）,
      "trace": [...]  # 工具调用轨迹，飞书卡片要展示
    }
    """
    alert = Alert.model_validate_json(alert_json)
    if llm_router is None:
        raise RuntimeError("llm_router not initialized")

    past_cases_text = ""
    negative_cases_text = ""
    if hermes_repo is not None:
        try:
            cases = await hermes_knowledge.query_similar_cases(hermes_repo, alert, limit=3)
            past_cases_text = hermes_knowledge.format_cases_for_prompt(cases)
            logger.info(f"Hermes 为告警 {alert.event_id} 注入 {len(cases)} 条历史案例")
        except Exception as exc:
            logger.warning(f"Hermes 历史案例查询失败，继续无历史诊断：{exc}")
            past_cases_text = ""

    if hermes_feedback is not None:
        try:
            negative_cases = await hermes_knowledge.query_negative_cases(hermes_feedback, alert, limit=2)
            negative_cases_text = hermes_knowledge.format_negative_cases(negative_cases)
            logger.info(f"Hermes 为告警 {alert.event_id} 注入 {len(negative_cases)} 条反例")
        except Exception as exc:
            logger.warning(f"Hermes 反例查询失败，继续无反例诊断：{exc}")
            negative_cases_text = ""

    try:
        result = await llm_router.diagnose_with_agent(
            alert,
            max_turns=5,
            past_cases_text=past_cases_text,
            negative_cases_text=negative_cases_text,
        )
    except AgentLimitExceeded, LLMUnavailable:
        # 让 workflow 走 fallback（关键词匹配）路径
        return json.dumps({"plan": None, "trace": [], "agent_failed": True})
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"Agent 诊断异常，降级到关键词匹配: {exc}")
        return json.dumps({"plan": None, "trace": [], "agent_failed": True})

    plan_dict = result.plan.model_dump() if result.plan else None
    return json.dumps({"plan": plan_dict, "trace": result.trace}, ensure_ascii=False)
