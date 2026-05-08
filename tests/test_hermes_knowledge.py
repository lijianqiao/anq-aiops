"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: test_hermes_knowledge.py
@DateTime: 2026-05-08 23:17:00
@Docs: 测试 Hermes 相似案例查询和 prompt 格式化
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest

from src.hermes.db import HermesDB
from src.hermes.knowledge import (
    format_cases_for_prompt,
    format_negative_cases,
    query_negative_cases,
    query_similar_cases,
)
from src.hermes.models import AuditRecordRead
from src.models import Alert

TEST_DSN = "postgresql://temporal:temporal@localhost:5432/temporal"
TEST_SCHEMA = "test_hermes_knowledge"


@pytest.fixture
async def hermes_db() -> AsyncIterator[HermesDB]:
    """创建知识查询测试用 schema；PostgreSQL 不可用时跳过。"""
    db = HermesDB(dsn=TEST_DSN)
    try:
        await db.connect(min_size=1, max_size=2)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"PostgreSQL 不可用，跳过 Hermes 集成测试：{exc}")

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


def _alert(host_ip: str = "1.1.1.1", event_name: str = "Disk full") -> Alert:
    return Alert(
        event_id="x",
        event_name=event_name,
        severity="high",
        hostname="h",
        host_ip=host_ip,
        trigger_id="t",
        message="tmp full",
        timestamp=datetime.now(UTC),
        status="problem",
    )


def _case(verify: bool | None = True) -> AuditRecordRead:
    return AuditRecordRead(
        id=1,
        event_id="e1",
        workflow_id="wf",
        decision="approved",
        runbook_id="disk_cleanup",
        runbook_params={"path": "/tmp"},
        hostname="h",
        host_ip="1.1.1.1",
        severity="high",
        event_name="Disk full",
        message="tmp full",
        verify=verify,
        execute_success=True,
        exec_stdout="",
        agent_reasoning="/tmp 占用 95%",
        agent_confidence=0.9,
        created_at=datetime(2026, 5, 1, tzinfo=UTC),
    )


def test_format_cases_empty() -> None:
    """无历史案例时返回中文提示。"""
    assert "未找到" in format_cases_for_prompt([])


def test_format_cases_with_records() -> None:
    """成功案例应包含 runbook、参数、结果标记和告警名。"""
    text = format_cases_for_prompt([_case()])

    assert "disk_cleanup" in text
    assert "/tmp" in text
    assert "✅" in text
    assert "Disk full" in text


def test_format_cases_marks_failed_attempts() -> None:
    """失败案例要明显标记，让 LLM 避免重复错误。"""
    text = format_cases_for_prompt([_case(verify=False)])

    assert "❌" in text


def test_format_negative_cases_renders_feedback_reason() -> None:
    """反例格式化应包含人工反馈原因。"""
    case = _case(verify=False)
    case.feedback_label = "rejected_wrongly"
    case.feedback_reason = "path 应该是 /tmp"

    text = format_negative_cases([case])

    assert "避坑案例" in text
    assert "rejected_wrongly" in text
    assert "/tmp" in text


@pytest.mark.asyncio
async def test_query_similar_cases_uses_alert_content() -> None:
    """query_similar_cases 应用 alert 的事件名、消息和 host_ip 检索。"""

    class FakeRepo:
        async def find_similar(
            self,
            query: str,
            host_ip: str | None,
            limit: int,
            only_successful: bool,
        ) -> list[AuditRecordRead]:
            assert "Disk full" in query
            assert "tmp full" in query
            assert host_ip == "1.1.1.1"
            assert limit == 3
            assert only_successful is False
            return [_case()]

    cases = await query_similar_cases(FakeRepo(), _alert(), limit=3)  # type: ignore[arg-type]

    assert len(cases) == 1


@pytest.mark.asyncio
async def test_query_negative_cases_uses_feedback_repo() -> None:
    """query_negative_cases 应使用 feedback repository 检索反例。"""

    class FakeFeedbackRepo:
        async def find_negative_cases(self, query: str, host_ip: str | None, limit: int) -> list[AuditRecordRead]:
            assert "Disk full" in query
            assert host_ip == "1.1.1.1"
            assert limit == 2
            return [_case(verify=False)]

    cases = await query_negative_cases(FakeFeedbackRepo(), _alert(), limit=2)  # type: ignore[arg-type]

    assert len(cases) == 1


@pytest.mark.asyncio
async def test_query_similar_cases_with_real_repo(hermes_db: Any) -> None:
    """真实 PG + repository 查询可返回相似历史案例。"""
    from src.hermes.models import AuditRecordWrite
    from src.hermes.repository import AuditRepository

    assert hermes_db.pool is not None
    repo = AuditRepository(hermes_db.pool, schema=TEST_SCHEMA)
    await repo.save(
        AuditRecordWrite(
            event_id="e1",
            workflow_id="wf",
            decision="approved",
            runbook_id="disk_cleanup",
            runbook_params={"path": "/tmp"},
            hostname="aiops-target",
            host_ip="192.168.198.130",
            severity="high",
            event_name="Disk usage high",
            message="tmp full",
            verify=True,
            execute_success=True,
            exec_stdout="",
        )
    )

    cases = await query_similar_cases(
        repo,
        alert=_alert(host_ip="192.168.198.130", event_name="Disk usage high"),
        limit=3,
    )

    assert len(cases) == 1
    assert cases[0].host_ip == "192.168.198.130"
