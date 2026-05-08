"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: models.py
@DateTime: 2026-05-08 14:10:00
@Docs: 定义告警、执行结果、审计、LLM 输出和策略决策等数据模型
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class Alert(BaseModel):
    """Zabbix Webhook 推送的告警"""

    event_id: str
    event_name: str
    severity: str
    hostname: str
    host_ip: str
    trigger_id: str
    message: str
    timestamp: datetime
    status: str  # "problem" | "recovery"


class RunbookResult(BaseModel):
    """单步执行结果"""

    success: bool
    stdout: str
    stderr: str
    duration_sec: float


class ExecutionResult(BaseModel):
    """完整执行结果"""

    dry_run: RunbookResult
    execute: RunbookResult
    verify: bool
    snapshot: dict[str, Any]
    rolled_back: bool = False


class AuditRecord(BaseModel):
    """全链路审计记录"""

    alert: Alert
    workflow_id: str
    decision: str  # approved / rejected / timeout
    runbook_id: str | None
    runbook_params: dict[str, Any] | None
    execution_result: ExecutionResult | None
    feishu_message_id: str | None
    created_at: datetime
    completed_at: datetime | None


class RCAResult(BaseModel):
    """LLM 根因分析结果"""

    root_cause: str
    confidence: float
    recommended_runbook: str
    params: dict[str, Any]
    reasoning: str


class ActionPlan(BaseModel):
    """执行计划（ReAct agent 输出）"""

    runbook_id: str
    params: dict[str, Any]
    risk_level: str
    requires_approval: bool
    reasoning: str
    # ReAct agent 调用工具的轨迹（每条含 turn / tool / args / result_preview 等）
    # 用于飞书卡片展示 + 审计回溯，不影响执行逻辑
    trace: list[dict[str, Any]] = []
    confidence: float = 0.0


class RiskEvaluation(BaseModel):
    """风险评估结果"""

    approved: bool
    risk_score: float
    reason: str
    auto_execute_eligible: bool


# ---- Phase 3: Policy 层 ----


class Decision(StrEnum):
    """Policy 评估决策

    继承 StrEnum 让 Pydantic 在 JSON 序列化时输出小写字符串而非枚举字面量，
    YAML 配置里也能直接写 "allow" / "deny" 而不用引用 Python 类。
    """

    ALLOW = "allow"  # 自动执行
    APPROVAL_REQUIRED = "approval_required"  # 转人工审批
    DENY = "deny"  # 拒绝执行


class PolicyResult(BaseModel):
    """Policy 评估结果"""

    decision: Decision
    matched_policy: str  # 命中的 rule name；默认决策时为 "default"
    reason: str  # 命中理由（来自 rule description）
