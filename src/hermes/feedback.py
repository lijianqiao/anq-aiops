"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: feedback.py
@DateTime: 2026-05-08 23:38:00
@Docs: 提供 Hermes 反馈标注和反例检索能力
"""

import re
from enum import StrEnum

import asyncpg

from src.hermes.models import AuditRecordRead
from src.hermes.repository import _row_to_read

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class FeedbackLabel(StrEnum):
    """人工反馈标签。"""

    REJECTED_WRONGLY = "rejected_wrongly"
    REJECTED_CORRECTLY = "rejected_correctly"
    MODIFIED = "modified"


class FeedbackRepository:
    """Hermes feedback 数据访问层。"""

    def __init__(self, pool: asyncpg.Pool, schema: str = "public") -> None:
        _validate_identifier(schema)
        self.pool = pool
        self.schema = schema

    @property
    def _table(self) -> str:
        """返回带 schema 的 audit_records 表名。"""
        return f'"{self.schema}".audit_records'

    async def label(self, record_id: int, label: FeedbackLabel, reason: str = "") -> None:
        """按记录 ID 写入反馈标签。"""
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"""
                UPDATE {self._table}
                SET feedback_label = $2,
                    feedback_reason = $3,
                    feedback_at = NOW()
                WHERE id = $1
                """,
                record_id,
                label.value,
                reason[:500],
            )

    async def label_latest_by_event_id(self, event_id: str, label: FeedbackLabel, reason: str = "") -> bool:
        """按 event_id 查最近审计记录并写入反馈标签。"""
        async with self.pool.acquire() as conn:
            record_id = await conn.fetchval(
                f"""
                SELECT id
                FROM {self._table}
                WHERE event_id = $1
                ORDER BY created_at DESC
                LIMIT 1
                """,
                event_id,
            )
        if record_id is None:
            return False
        await self.label(int(record_id), label, reason)
        return True

    async def get_with_feedback(self, record_id: int) -> AuditRecordRead | None:
        """读取带反馈字段的审计记录。"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(f"SELECT * FROM {self._table} WHERE id = $1", record_id)
        return _row_to_read(row) if row else None

    async def find_negative_cases(
        self,
        query: str,
        host_ip: str | None,
        limit: int = 3,
    ) -> list[AuditRecordRead]:
        """检索人工标注过的反例。"""
        cleaned_query = query.strip()
        if not cleaned_query:
            return []

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT id, event_id, workflow_id, decision, runbook_id, runbook_params,
                       hostname, host_ip, severity, event_name, message,
                       verify, execute_success, exec_stdout,
                       agent_reasoning, agent_confidence, created_at, completed_at,
                       feedback_label, feedback_reason, feedback_at,
                       ts_rank(fts, websearch_to_tsquery('simple', $1)) AS rank
                FROM {self._table}
                WHERE feedback_label IS NOT NULL
                  AND fts @@ websearch_to_tsquery('simple', $1)
                ORDER BY
                    CASE WHEN $2::text IS NOT NULL AND host_ip = $2 THEN 1 ELSE 0 END DESC,
                    rank DESC,
                    feedback_at DESC
                LIMIT $3
                """,
                cleaned_query,
                host_ip,
                max(1, int(limit)),
            )
        return [_row_to_read(row) for row in rows]


def _validate_identifier(value: str) -> None:
    """校验 schema 名是否合法。"""
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"非法 schema 名：{value}")
