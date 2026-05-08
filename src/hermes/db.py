"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: db.py
@DateTime: 2026-05-08 23:14:00
@Docs: 管理 Hermes 知识层 asyncpg 连接池和数据库 schema 初始化
"""

import logging
import re
from pathlib import Path

import asyncpg

logger = logging.getLogger(__name__)
_SCHEMA_PATH = Path(__file__).parent / "schema.sql"
_SCHEMA_PHASE8_PATH = Path(__file__).parent / "schema_phase8.sql"
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class HermesDB:
    """Hermes PostgreSQL 连接池。"""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.pool: asyncpg.Pool | None = None

    async def connect(self, min_size: int = 1, max_size: int = 10) -> None:
        """创建 asyncpg 连接池，已创建时直接复用。"""
        if self.pool is not None:
            return
        self.pool = await asyncpg.create_pool(
            dsn=self.dsn,
            min_size=min_size,
            max_size=max_size,
            command_timeout=10,
        )

    async def close(self) -> None:
        """关闭连接池。"""
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    async def init_schema(self, schema_name: str = "public") -> None:
        """幂等初始化 audit_records 表和索引。"""
        if self.pool is None:
            raise RuntimeError("Hermes 数据库未连接")
        _validate_identifier(schema_name)

        sqls = [
            _SCHEMA_PATH.read_text(encoding="utf-8"),
            _SCHEMA_PHASE8_PATH.read_text(encoding="utf-8"),
        ]
        async with self.pool.acquire() as conn, conn.transaction():
            if schema_name != "public":
                await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"')
                await conn.execute(f'SET LOCAL search_path TO "{schema_name}", public')
            for sql in sqls:
                await conn.execute(sql)
        logger.info(f"Hermes schema 已初始化（Phase 7+8）：{schema_name}")


def _validate_identifier(value: str) -> None:
    """校验 PostgreSQL identifier，避免 schema 名拼接带来注入风险。"""
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"非法 schema 名：{value}")
