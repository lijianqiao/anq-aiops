from datetime import datetime

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
    snapshot: dict
    rolled_back: bool = False


class AuditRecord(BaseModel):
    """全链路审计记录"""

    alert: Alert
    workflow_id: str
    decision: str  # approved / rejected / timeout
    runbook_id: str | None
    runbook_params: dict | None
    execution_result: ExecutionResult | None
    feishu_message_id: str | None
    created_at: datetime
    completed_at: datetime | None


class RCAResult(BaseModel):
    """LLM 根因分析结果"""

    root_cause: str
    confidence: float
    recommended_runbook: str
    params: dict
    reasoning: str


class ActionPlan(BaseModel):
    """执行计划"""

    runbook_id: str
    params: dict
    risk_level: str
    requires_approval: bool
    reasoning: str


class RiskEvaluation(BaseModel):
    """风险评估结果"""

    approved: bool
    risk_score: float
    reason: str
    auto_execute_eligible: bool
