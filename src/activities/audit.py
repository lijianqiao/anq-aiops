import json
from datetime import datetime

from temporalio import activity

from src.models import Alert, AuditRecord, ExecutionResult


@activity.defn
async def write_audit(
    alert_json: str,
    workflow_id: str,
    decision: str,
    runbook_id: str | None,
    runbook_params_json: str | None,
    execution_result_json: str | None,
    feishu_message_id: str | None,
) -> str:
    """写入审计日志，返回 AuditRecord JSON"""
    alert = Alert.model_validate_json(alert_json)
    execution_result = ExecutionResult.model_validate_json(execution_result_json) if execution_result_json else None
    runbook_params = json.loads(runbook_params_json) if runbook_params_json else None
    record = AuditRecord(
        alert=alert,
        workflow_id=workflow_id,
        decision=decision,
        runbook_id=runbook_id,
        runbook_params=runbook_params,
        execution_result=execution_result,
        feishu_message_id=feishu_message_id,
        created_at=datetime.now(datetime.UTC),
        completed_at=datetime.now(datetime.UTC),
    )
    # Phase 1: 先写日志，后续接 PostgreSQL
    print(f"[AUDIT] {record.alert.event_id} | {record.decision} | {record.runbook_id}")
    return record.model_dump_json()
