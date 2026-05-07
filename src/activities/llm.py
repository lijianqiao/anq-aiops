"""LLM Activity：现在统一走 ReAct agent，一次 activity 完成 RCA + plan + risk

旧的 rca_analyze / plan_action / evaluate_risk 三个独立 activity 已废弃，
agent 在多轮 tool calling 中自己完成观察 → 推理 → 决策。
"""

import json

from temporalio import activity

from src.llm.agent import AgentLimitExceeded, DiagnosticAgent
from src.models import Alert

# 模块级 router，由 main.py lifespan 初始化
llm_router = None


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

    client = llm_router.select_client_for_agent()
    agent = DiagnosticAgent(llm_client=client, max_turns=5)

    try:
        result = await agent.diagnose(alert)
    except AgentLimitExceeded:
        # 让 workflow 走 fallback（关键词匹配）路径
        return json.dumps({"plan": None, "trace": [], "agent_failed": True})

    plan_dict = result.plan.model_dump() if result.plan else None
    return json.dumps({"plan": plan_dict, "trace": result.trace}, ensure_ascii=False)
