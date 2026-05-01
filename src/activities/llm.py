from temporalio import activity

from src.llm.prompts import build_plan_prompt, build_rca_prompt, build_risk_prompt
from src.models import ActionPlan, Alert, RCAResult, RiskEvaluation
from src.runbooks import RUNBOOK_REGISTRY

# 模块级 router，由 main.py lifespan 初始化
llm_router = None


def _runbook_list() -> str:
    lines = []
    for name, cls in RUNBOOK_REGISTRY.items():
        lines.append(f"- {name}: {cls.__doc__ or '无描述'}")
    return "\n".join(lines)


@activity.defn
async def rca_analyze(alert_json: str) -> str:
    """分析告警根因，返回 RCAResult JSON"""
    alert = Alert.model_validate_json(alert_json)
    prompt = build_rca_prompt(alert, runbook_list=_runbook_list())
    result = await llm_router.invoke(prompt, RCAResult)
    return result.model_dump_json()


@activity.defn
async def plan_action(alert_json: str, rca_json: str) -> str:
    """基于 RCA 结果生成执行计划，返回 ActionPlan JSON"""
    alert = Alert.model_validate_json(alert_json)
    rca = RCAResult.model_validate_json(rca_json)
    prompt = build_plan_prompt(alert, rca, runbook_list=_runbook_list())
    result = await llm_router.invoke(prompt, ActionPlan)
    return result.model_dump_json()


@activity.defn
async def evaluate_risk(alert_json: str, plan_json: str) -> str:
    """评估执行计划的风险，返回 RiskEvaluation JSON"""
    alert = Alert.model_validate_json(alert_json)
    plan = ActionPlan.model_validate_json(plan_json)
    prompt = build_risk_prompt(alert, plan)
    result = await llm_router.invoke(prompt, RiskEvaluation)
    return result.model_dump_json()
