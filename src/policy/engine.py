"""Policy 规则引擎

YAML schema:
  policies:
    - name: <规则名>
      description: <人类可读描述，会写到决策 reason>
      effect: <allow | require_approval | deny>
      conditions:
        - <field>: <value>          # 默认 eq
        - <field>: { in: [...] }    # 显式 operator
        - ...

field 支持点分路径（如 params.path），多个 condition 之间是 AND。
评估顺序：所有 deny → 所有 require_approval → 所有 allow → 默认 approval。
同阶段内顺序遍历，第一个匹配的规则即返回。

设计要点：不用 eval()，避免 YAML 配置错让远程主机执行任意代码。
"""

from typing import Any


def _safe_compare(a: Any, b: Any, op):
    """a / b 任一为 None 时返回 False，避免 TypeError。"""
    if a is None or b is None:
        return False
    try:
        return op(a, b)
    except TypeError:
        return False


_OPERATORS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "in": lambda a, b: a in b if b is not None else False,
    "not_in": lambda a, b: a not in b if b is not None else False,
    "gte": lambda a, b: _safe_compare(a, b, lambda x, y: x >= y),
    "lte": lambda a, b: _safe_compare(a, b, lambda x, y: x <= y),
    "gt": lambda a, b: _safe_compare(a, b, lambda x, y: x > y),
    "lt": lambda a, b: _safe_compare(a, b, lambda x, y: x < y),
}


def _get_value(ctx: dict, path: str) -> Any:
    """按点分路径取值。中间不是 dict 或 key 不存在 → None"""
    cur: Any = ctx
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _match_condition(condition: dict, ctx: dict) -> bool:
    """评估一个 condition dict（多 key 之间 AND）"""
    for field, expected in condition.items():
        actual = _get_value(ctx, field)

        if isinstance(expected, dict):
            # operator 形式: {op: value}
            if len(expected) != 1:
                raise ValueError(f"condition for {field!r} must have exactly one operator")
            op_name, op_value = next(iter(expected.items()))
            if op_name not in _OPERATORS:
                raise ValueError(f"unknown operator {op_name!r} for field {field!r}")
            if not _OPERATORS[op_name](actual, op_value):
                return False
        else:
            # 默认 eq
            if actual != expected:
                return False
    return True


# ---- evaluate_policy 主流程 ----

import yaml  # noqa: E402

from src.config import settings  # noqa: E402
from src.models import Decision, PolicyResult  # noqa: E402
from src.policy.host_tiers import lookup_tier  # noqa: E402


def _build_context(*, runbook_id: str, params: dict, alert: dict, plan: dict) -> dict:
    """组装 condition 评估时的扁平 ctx"""
    params = params or {}
    alert = alert or {}
    plan = plan or {}
    return {
        "runbook_id": runbook_id,
        "params": params,
        "alert": alert,
        "plan": plan,
        # 顶层快捷字段（规则常用，避免每次写 plan.xxx）
        "risk_level": plan.get("risk_level"),
        "confidence": plan.get("confidence", 0),
        "host_ip": params.get("target_host"),
        "host_tier": lookup_tier(params.get("target_host")),
    }


def _load_policies() -> list[dict]:
    """读 yaml 配置，返回 policies 列表"""
    with open(settings.policy_config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("policies") or []


def _match_all_conditions(conditions: list[dict], ctx: dict) -> bool:
    """conditions 列表里每条都要匹配（AND）"""
    return all(_match_condition(c, ctx) for c in conditions)


def evaluate_policy(*, runbook_id: str, params: dict, alert: dict, plan: dict) -> PolicyResult:
    """三段式评估：deny → require_approval → allow → default(approval)

    任何阶段第一个匹配的规则即返回。
    """
    policies = _load_policies()
    ctx = _build_context(runbook_id=runbook_id, params=params, alert=alert, plan=plan)

    # 阶段 1: DENY 优先
    for p in policies:
        if p.get("effect") == "deny" and _match_all_conditions(p.get("conditions") or [], ctx):
            return PolicyResult(
                decision=Decision.DENY,
                matched_policy=p.get("name", "unnamed"),
                reason=p.get("description", ""),
            )

    # 阶段 2: APPROVAL_REQUIRED
    for p in policies:
        if p.get("effect") == "require_approval" and _match_all_conditions(p.get("conditions") or [], ctx):
            return PolicyResult(
                decision=Decision.APPROVAL_REQUIRED,
                matched_policy=p.get("name", "unnamed"),
                reason=p.get("description", ""),
            )

    # 阶段 3: ALLOW
    for p in policies:
        if p.get("effect") == "allow" and _match_all_conditions(p.get("conditions") or [], ctx):
            return PolicyResult(
                decision=Decision.ALLOW,
                matched_policy=p.get("name", "unnamed"),
                reason=p.get("description", ""),
            )

    # 默认：保守审批
    return PolicyResult(
        decision=Decision.APPROVAL_REQUIRED,
        matched_policy="default",
        reason="no allow rule matched, falling back to manual approval",
    )
