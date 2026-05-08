"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: audit.py
@DateTime: 2026-05-08 23:19:00
@Docs: 写入 JSONL 审计日志并双写 Hermes PostgreSQL 知识库
"""

import datetime as dt  # noqa: UP017
import json
import logging
from pathlib import Path

from temporalio import activity

from src.config import settings
from src.hermes.models import AuditRecordWrite
from src.hermes.repository import AuditRepository
from src.models import Alert, AuditRecord, ExecutionResult

logger = logging.getLogger(__name__)
_repo: AuditRepository | None = None


def set_repo(repo: AuditRepository | None) -> None:
    """设置 Hermes 审计仓储；None 表示只写 JSONL。"""
    global _repo
    _repo = repo


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
    audit_json = record.model_dump_json()
    audit_path = Path(settings.audit_log_path)
    if audit_path.parent != Path("."):
        audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as audit_file:
        audit_file.write(audit_json + "\n")

    if _repo is not None:
        try:
            await _repo.save(_to_hermes_record(record))
        except Exception as exc:
            logger.warning(f"Hermes 审计写入失败，已保留 JSONL 兜底：{exc}")

    print(f"[AUDIT] {record.alert.event_id} | {record.decision} | {record.runbook_id}")
    return audit_json


def _to_hermes_record(record: AuditRecord) -> AuditRecordWrite:
    """将应用审计模型转换为 Hermes 写入模型。"""
    execution_result = record.execution_result
    return AuditRecordWrite(
        event_id=record.alert.event_id,
        workflow_id=record.workflow_id,
        decision=record.decision,
        runbook_id=record.runbook_id,
        runbook_params=record.runbook_params,
        hostname=record.alert.hostname,
        host_ip=record.alert.host_ip,
        severity=record.alert.severity,
        event_name=record.alert.event_name,
        message=record.alert.message,
        verify=execution_result.verify if execution_result else None,
        execute_success=execution_result.execute.success if execution_result else None,
        exec_stdout=execution_result.execute.stdout if execution_result else None,
    )
