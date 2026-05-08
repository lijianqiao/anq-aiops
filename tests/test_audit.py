"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: test_audit.py
@DateTime: 2026-05-08 23:20:00
@Docs: 测试审计 JSONL 写入和 Hermes PG 双写降级
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from src.activities import audit as audit_mod
from src.activities.audit import write_audit
from src.config import settings
from src.models import Alert, ExecutionResult, RunbookResult


@pytest.mark.asyncio
async def test_write_audit_persists_jsonl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(settings, "audit_log_path", str(audit_path))
    monkeypatch.setattr(audit_mod, "_repo", None)
    alert_json = Alert(
        event_id="evt-001",
        event_name="Disk full",
        severity="high",
        hostname="web-01",
        host_ip="10.0.0.1",
        trigger_id="100",
        message="Disk usage 95%",
        timestamp=datetime.fromisoformat("2026-01-01T12:00:00+00:00"),
        status="problem",
    ).model_dump_json()

    result = await write_audit(alert_json, "wf-001", "approved", "disk_cleanup", "{}", None, "msg-001")

    lines = audit_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == json.loads(result)


@pytest.mark.asyncio
async def test_write_audit_writes_to_pg_and_jsonl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同时写 JSONL 和 Hermes repository。"""
    audit_path = tmp_path / "audit.jsonl"
    saved: list[Any] = []

    class FakeRepo:
        async def save(self, record: Any) -> int:
            saved.append(record)
            return 1

    monkeypatch.setattr(settings, "audit_log_path", str(audit_path))
    monkeypatch.setattr(audit_mod, "_repo", FakeRepo())

    await write_audit(
        _alert_json(),
        "wf-001",
        "approved",
        "disk_cleanup",
        '{"path":"/tmp"}',
        _execution_result_json(),
        "msg-001",
    )

    assert audit_path.exists()
    assert len(audit_path.read_text(encoding="utf-8").splitlines()) == 1
    assert len(saved) == 1
    assert saved[0].event_id == "evt-001"
    assert saved[0].verify is True
    assert saved[0].execute_success is True


@pytest.mark.asyncio
async def test_write_audit_pg_failure_fallback_to_jsonl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hermes 写入失败时不影响 JSONL 审计。"""
    audit_path = tmp_path / "audit.jsonl"

    class BrokenRepo:
        async def save(self, record: Any) -> int:
            raise RuntimeError("PG 不可用")

    monkeypatch.setattr(settings, "audit_log_path", str(audit_path))
    monkeypatch.setattr(audit_mod, "_repo", BrokenRepo())

    await write_audit(_alert_json(), "wf-001", "approved", None, None, None, None)

    assert audit_path.exists()
    assert len(audit_path.read_text(encoding="utf-8").splitlines()) == 1


def _alert_json() -> str:
    """构造测试告警 JSON。"""
    return Alert(
        event_id="evt-001",
        event_name="Disk full",
        severity="high",
        hostname="web-01",
        host_ip="10.0.0.1",
        trigger_id="100",
        message="Disk usage 95%",
        timestamp=datetime.fromisoformat("2026-01-01T12:00:00+00:00"),
        status="problem",
    ).model_dump_json()


def _execution_result_json() -> str:
    """构造测试执行结果 JSON。"""
    return ExecutionResult(
        dry_run=RunbookResult(success=True, stdout="", stderr="", duration_sec=0.1),
        execute=RunbookResult(success=True, stdout="ok", stderr="", duration_sec=0.2),
        verify=True,
        snapshot={},
    ).model_dump_json()
