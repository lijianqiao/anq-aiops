"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: test_feedback.py
@DateTime: 2026-05-08 23:39:00
@Docs: 测试 Hermes Phase 8 反馈 schema、标注和反例检索
"""

from collections.abc import AsyncIterator

import asyncpg
import pytest

from src.hermes.db import HermesDB
from src.hermes.feedback import FeedbackLabel, FeedbackRepository
from src.hermes.models import AuditRecordWrite
from src.hermes.repository import AuditRepository

TEST_DSN = "postgresql://temporal:temporal@localhost:5432/temporal"
TEST_SCHEMA = "test_hermes_feedback"


@pytest.fixture
async def hermes_db() -> AsyncIterator[HermesDB]:
    """创建 feedback 测试 schema；PostgreSQL 不可用时跳过。"""
    db = HermesDB(dsn=TEST_DSN)
    try:
        await db.connect(min_size=1, max_size=2)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"PostgreSQL 不可用，跳过 Hermes feedback 测试：{exc}")

    assert db.pool is not None
    async with db.pool.acquire() as conn:
        await conn.execute(f'DROP SCHEMA IF EXISTS "{TEST_SCHEMA}" CASCADE')
        await conn.execute(f'CREATE SCHEMA "{TEST_SCHEMA}"')
    await db.init_schema(schema_name=TEST_SCHEMA)

    try:
        yield db
    finally:
        if db.pool is not None:
            async with db.pool.acquire() as conn:
                await conn.execute(f'DROP SCHEMA IF EXISTS "{TEST_SCHEMA}" CASCADE')
        await db.close()


@pytest.mark.asyncio
async def test_schema_phase8_adds_feedback_columns(hermes_db: HermesDB) -> None:
    """schema 初始化应包含 feedback 字段。"""
    assert hermes_db.pool is not None
    async with hermes_db.pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'audit_records' AND table_schema = $1
            """,
            TEST_SCHEMA,
        )

    names = {row["column_name"] for row in rows}
    assert {"feedback_label", "feedback_reason", "feedback_at"} <= names


@pytest.mark.asyncio
async def test_label_rejected_wrongly(hermes_db: HermesDB) -> None:
    """运维拒绝时应记录 rejected_wrongly 反馈。"""
    assert hermes_db.pool is not None
    repo = AuditRepository(hermes_db.pool, schema=TEST_SCHEMA)
    feedback = FeedbackRepository(hermes_db.pool, schema=TEST_SCHEMA)
    record_id = await repo.save(_record(event_id="e1", decision="rejected", runbook_params={"path": "/var/log"}))

    await feedback.label(record_id, FeedbackLabel.REJECTED_WRONGLY, "path 应该是 /tmp 不是 /var/log")
    record = await feedback.get_with_feedback(record_id)

    assert record is not None
    assert record.feedback_label == FeedbackLabel.REJECTED_WRONGLY
    assert record.feedback_reason is not None
    assert "/tmp" in record.feedback_reason


@pytest.mark.asyncio
async def test_query_negative_cases(hermes_db: HermesDB) -> None:
    """find_negative_cases 只返回已标注反馈的记录。"""
    assert hermes_db.pool is not None
    repo = AuditRepository(hermes_db.pool, schema=TEST_SCHEMA)
    feedback = FeedbackRepository(hermes_db.pool, schema=TEST_SCHEMA)
    bad_id = await repo.save(_record(event_id="e1", decision="rejected", event_name="Disk full etc"))
    await repo.save(_record(event_id="e2", decision="approved", event_name="Disk full tmp"))

    await feedback.label(bad_id, FeedbackLabel.REJECTED_WRONGLY, "path 错")
    negatives = await feedback.find_negative_cases(query="disk", host_ip="1.1.1.1", limit=5)

    assert len(negatives) == 1
    assert negatives[0].id == bad_id


def _record(
    event_id: str,
    decision: str,
    event_name: str = "Disk full",
    runbook_params: dict[str, str] | None = None,
) -> AuditRecordWrite:
    """构造测试审计写入模型。"""
    return AuditRecordWrite(
        event_id=event_id,
        workflow_id=f"wf-{event_id}",
        decision=decision,
        runbook_id="disk_cleanup",
        runbook_params=runbook_params or {"path": "/tmp"},
        hostname="h",
        host_ip="1.1.1.1",
        severity="high",
        event_name=event_name,
        message="disk full",
        verify=decision != "rejected",
        execute_success=decision != "rejected",
        exec_stdout="ok",
    )
