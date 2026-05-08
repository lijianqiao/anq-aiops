"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: test_hermes_repository.py
@DateTime: 2026-05-08 23:16:00
@Docs: 测试 Hermes 数据库连接、schema 初始化和 audit repository 检索
"""

from collections.abc import AsyncIterator

import asyncpg
import pytest

from src.hermes.db import HermesDB
from src.hermes.models import AuditRecordWrite
from src.hermes.repository import AuditRepository

TEST_DSN = "postgresql://temporal:temporal@localhost:5432/temporal"
TEST_SCHEMA = "test_hermes"


@pytest.fixture
async def hermes_db() -> AsyncIterator[HermesDB]:
    """创建隔离 schema 的 HermesDB；PostgreSQL 不可用时跳过。"""
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


@pytest.mark.asyncio
async def test_db_connect_creates_pool() -> None:
    """connect 应创建连接池。"""
    db = HermesDB(dsn=TEST_DSN)
    try:
        await db.connect(min_size=1, max_size=2)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"PostgreSQL 不可用，跳过 Hermes 集成测试：{exc}")
    try:
        assert db.pool is not None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_db_init_schema_creates_table(hermes_db: HermesDB) -> None:
    """init_schema 应创建 audit_records 表。"""
    assert hermes_db.pool is not None
    async with hermes_db.pool.acquire() as conn:
        result = await conn.fetchval(f"SELECT to_regclass('{TEST_SCHEMA}.audit_records')")
    assert result is not None


@pytest.mark.asyncio
async def test_db_init_schema_idempotent(hermes_db: HermesDB) -> None:
    """重复初始化 schema 不应报错。"""
    await hermes_db.init_schema(schema_name=TEST_SCHEMA)
    await hermes_db.init_schema(schema_name=TEST_SCHEMA)


@pytest.mark.asyncio
async def test_save_audit_record(hermes_db: HermesDB) -> None:
    """repository.save 应写入一条审计记录。"""
    assert hermes_db.pool is not None
    repo = AuditRepository(hermes_db.pool, schema=TEST_SCHEMA)

    record_id = await repo.save(_record(event_id="e1"))

    assert record_id > 0
    assert await repo.count_total() == 1


@pytest.mark.asyncio
async def test_find_similar_by_keywords(hermes_db: HermesDB) -> None:
    """全文搜索应匹配相似告警。"""
    assert hermes_db.pool is not None
    repo = AuditRepository(hermes_db.pool, schema=TEST_SCHEMA)
    await repo.save(_record(event_id="e1", event_name="Disk usage high tmp", message="tmp full"))
    await repo.save(_record(event_id="e2", runbook_id="service_restart", event_name="nginx down", message="nginx 502"))
    await repo.save(_record(event_id="e3", host_ip="2.2.2.2", event_name="CPU high", message="cpu 95%"))

    results = await repo.find_similar(query="disk tmp", host_ip="1.1.1.1", limit=3)

    assert results
    assert results[0].event_id == "e1"


@pytest.mark.asyncio
async def test_find_similar_prefers_same_host(hermes_db: HermesDB) -> None:
    """同关键词时，同 host_ip 的历史案例应排在前面。"""
    assert hermes_db.pool is not None
    repo = AuditRepository(hermes_db.pool, schema=TEST_SCHEMA)
    await repo.save(_record(event_id="other-host", host_ip="9.9.9.9", event_name="Disk usage high"))
    await repo.save(_record(event_id="same-host", host_ip="1.1.1.1", event_name="Disk usage high"))

    results = await repo.find_similar(query="disk", host_ip="1.1.1.1", limit=3)

    assert results[0].event_id == "same-host"


@pytest.mark.asyncio
async def test_find_similar_returns_empty_on_no_match(hermes_db: HermesDB) -> None:
    """无匹配时返回空列表。"""
    assert hermes_db.pool is not None
    repo = AuditRepository(hermes_db.pool, schema=TEST_SCHEMA)

    assert await repo.find_similar(query="xyz nonexistent", host_ip=None, limit=3) == []


def _record(
    event_id: str,
    host_ip: str = "1.1.1.1",
    runbook_id: str | None = "disk_cleanup",
    event_name: str = "Disk usage high",
    message: str = "tmp full",
) -> AuditRecordWrite:
    """构造测试审计记录。"""
    return AuditRecordWrite(
        event_id=event_id,
        workflow_id=f"wf-{event_id}",
        decision="approved",
        runbook_id=runbook_id,
        runbook_params={"target_host": host_ip, "path": "/tmp"} if runbook_id else None,
        hostname=f"host-{host_ip}",
        host_ip=host_ip,
        severity="high",
        event_name=event_name,
        message=message,
        verify=True,
        execute_success=True,
        exec_stdout="ok",
        agent_reasoning="/tmp 占用最高",
        agent_confidence=0.9,
    )
