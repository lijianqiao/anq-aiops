"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: policy.py
@DateTime: 2026-05-08 14:33:00
@Docs: 将 Policy 规则评估封装为 Temporal Activity

Policy 评估 Activity

把 evaluate_policy 包装成 Temporal Activity：
  - 输入/输出全用 JSON 字符串（Temporal 友好）
  - YAML 损坏 / 文件不存在 / 输入非法等异常一律降级为 APPROVAL_REQUIRED
    （绝不能因为 policy 出问题就把告警丢了，必须有兜底）
"""

import json
import logging
from typing import Any

from temporalio import activity

from src.models import Decision, PolicyResult
from src.policy.engine import evaluate_policy

logger = logging.getLogger(__name__)


def _safe_json_loads(text: str | None, default: Any) -> Any:
    """解析 JSON，失败或空字符串 / "null" 时返回 default"""
    if not text:
        return default
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        return default
    return result if result is not None else default


@activity.defn
async def evaluate_policy_activity(
    runbook_id: str,
    runbook_params_json: str,
    alert_json: str,
    plan_json: str,
) -> str:
    """Policy 评估，返回 PolicyResult JSON

    plan_json 可以是 "null"（agent 失败时），此时按空 plan 评估，
    通常会落到 default APPROVAL_REQUIRED。
    """
    try:
        params = _safe_json_loads(runbook_params_json, {})
        alert = _safe_json_loads(alert_json, {})
        plan = _safe_json_loads(plan_json, {})
        # 防御：上游传 list / int 等非 dict 也能继续
        if not isinstance(params, dict):
            params = {}
        if not isinstance(alert, dict):
            alert = {}
        if not isinstance(plan, dict):
            plan = {}

        result = evaluate_policy(runbook_id=runbook_id, params=params, alert=alert, plan=plan)
        return result.model_dump_json()
    except Exception as exc:  # noqa: BLE001
        logger.exception("policy evaluation failed, falling back to APPROVAL_REQUIRED")
        fallback = PolicyResult(
            decision=Decision.APPROVAL_REQUIRED,
            matched_policy="default",
            reason=f"policy evaluation error, falling back to manual approval: {exc}",
        )
        return fallback.model_dump_json()
