import datetime as dt  # noqa: UP017
import json
from pathlib import Path

from temporalio import activity

from src.config import settings
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
        created_at=dt.datetime.now(dt.UTC),
        completed_at=dt.datetime.now(dt.UTC),
    )
    # Keep a durable JSONL audit trail until a database sink is introduced.
    audit_json = record.model_dump_json()
    audit_path = Path(settings.audit_log_path)
    if audit_path.parent != Path("."):
        audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as audit_file:
        audit_file.write(audit_json + "\n")
    print(f"[AUDIT] {record.alert.event_id} | {record.decision} | {record.runbook_id}")
    return audit_json
