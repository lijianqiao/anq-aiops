import json
from datetime import datetime

import pytest

from src.activities.audit import write_audit
from src.config import settings
from src.models import Alert


@pytest.mark.asyncio
async def test_write_audit_persists_jsonl(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(settings, "audit_log_path", str(audit_path))
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
