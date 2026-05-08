"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: models.py
@DateTime: 2026-05-08 23:15:00
@Docs: 定义 Hermes 知识层审计记录读写模型
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AuditRecordWrite(BaseModel):
    """写入 Hermes audit_records 的数据。"""

    event_id: str
    workflow_id: str
    decision: str
    runbook_id: str | None
    runbook_params: dict[str, Any] | None
    hostname: str
    host_ip: str
    severity: str
    event_name: str
    message: str
    verify: bool | None = None
    execute_success: bool | None = None
    exec_stdout: str | None = None
    agent_reasoning: str | None = None
    agent_confidence: float | None = None


class AuditRecordRead(AuditRecordWrite):
    """从 Hermes audit_records 读取的数据。"""

    id: int
    created_at: datetime
    completed_at: datetime | None = None
