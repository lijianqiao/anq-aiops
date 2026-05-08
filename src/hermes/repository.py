"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: repository.py
@DateTime: 2026-05-08 23:15:00
@Docs: 提供 Hermes audit_records 的写入和相似案例检索
"""

import json
import re
from typing import Any

import asyncpg

from src.hermes.models import AuditRecordRead, AuditRecordWrite

_STDOUT_TRUNCATE = 4000
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class AuditRepository:
    """Hermes audit_records 数据访问层。"""

    def __init__(self, pool: asyncpg.Pool, schema: str = "public") -> None:
        _validate_identifier(schema)
        self.pool = pool
        self.schema = schema

    @property
    def _table(self) -> str:
        """返回带 schema 的表名。"""
        return f'"{self.schema}".audit_records'

    async def save(self, record: AuditRecordWrite) -> int:
        """写入一条审计记录，返回自增 ID。"""
        stdout = (record.exec_stdout or "")[-_STDOUT_TRUNCATE:]
        async with self.pool.acquire() as conn:
            record_id = await conn.fetchval(
                f"""
                INSERT INTO {self._table}
                  (event_id, workflow_id, decision, runbook_id, runbook_params,
                   hostname, host_ip, severity, event_name, message,
                   verify, execute_success, exec_stdout,
                   agent_reasoning, agent_confidence, completed_at)
                VALUES
                  ($1, $2, $3, $4, $5::jsonb,
                   $6, $7, $8, $9, $10,
                   $11, $12, $13,
                   $14, $15, NOW())
                RETURNING id
                """,
                record.event_id,
                record.workflow_id,
                record.decision,
                record.runbook_id,
                _to_json(record.runbook_params),
                record.hostname,
                record.host_ip,
                record.severity,
                record.event_name,
                record.message,
                record.verify,
                record.execute_success,
                stdout,
                record.agent_reasoning,
                record.agent_confidence,
            )
        return int(record_id)

    async def find_similar(
        self,
        query: str,
        host_ip: str | None,
        limit: int = 3,
        only_successful: bool = False,
    ) -> list[AuditRecordRead]:
        """全文检索 Top-N 相似告警，同 host 结果优先。"""
        cleaned_query = query.strip()
        if not cleaned_query:
            return []

        success_clause = "AND verify = TRUE" if only_successful else ""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT id, event_id, workflow_id, decision, runbook_id, runbook_params,
                       hostname, host_ip, severity, event_name, message,
                       verify, execute_success, exec_stdout,
                       agent_reasoning, agent_confidence, created_at, completed_at,
                       ts_rank(fts, websearch_to_tsquery('simple', $1)) AS rank
                FROM {self._table}
                WHERE fts @@ websearch_to_tsquery('simple', $1)
                {success_clause}
                ORDER BY
                    CASE WHEN $2::text IS NOT NULL AND host_ip = $2 THEN 1 ELSE 0 END DESC,
                    rank DESC,
                    created_at DESC
                LIMIT $3
                """,
                cleaned_query,
                host_ip,
                max(1, int(limit)),
            )
        return [_row_to_read(row) for row in rows]

    async def count_total(self) -> int:
        """返回审计记录总数。"""
        async with self.pool.acquire() as conn:
            value = await conn.fetchval(f"SELECT count(*) FROM {self._table}")
        return int(value or 0)


def _to_json(value: dict[str, Any] | None) -> str | None:
    """将 dict 转为 JSONB 参数。"""
    return json.dumps(value, ensure_ascii=False) if value is not None else None


def _row_to_read(row: asyncpg.Record) -> AuditRecordRead:
    """将 asyncpg Record 转为 Pydantic 读取模型。"""
    raw_params = row["runbook_params"]
    params = json.loads(raw_params) if isinstance(raw_params, str) else raw_params
    return AuditRecordRead(
        id=row["id"],
        event_id=row["event_id"],
        workflow_id=row["workflow_id"],
        decision=row["decision"],
        runbook_id=row["runbook_id"],
        runbook_params=params,
        hostname=row["hostname"],
        host_ip=row["host_ip"],
        severity=row["severity"],
        event_name=row["event_name"],
        message=row["message"],
        verify=row["verify"],
        execute_success=row["execute_success"],
        exec_stdout=row["exec_stdout"],
        agent_reasoning=row["agent_reasoning"],
        agent_confidence=row["agent_confidence"],
        created_at=row["created_at"],
        completed_at=row["completed_at"],
    )


def _validate_identifier(value: str) -> None:
    """校验 schema 名是否合法。"""
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"非法 schema 名：{value}")
