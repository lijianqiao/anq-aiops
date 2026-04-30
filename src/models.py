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
