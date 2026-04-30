from datetime import datetime

from src.models import Alert, AuditRecord, ExecutionResult, RunbookResult


class TestAlert:
    def test_create_from_dict(self) -> None:
        alert = Alert(
            event_id="12345",
            event_name="Disk usage > 90%",
            severity="high",
            hostname="web-server-01",
            host_ip="192.168.1.13",
            trigger_id="10001",
            message="Disk usage is 95% on /tmp",
            timestamp=datetime(2026, 4, 30, 14, 30, 0),
            status="problem",
        )
        assert alert.event_id == "12345"
        assert alert.severity == "high"
        assert alert.status == "problem"

    def test_roundtrip_json(self) -> None:
        alert = Alert(
            event_id="12345",
            event_name="test",
            severity="high",
            hostname="host1",
            host_ip="10.0.0.1",
            trigger_id="100",
            message="msg",
            timestamp=datetime(2026, 1, 1),
            status="problem",
        )
        data = alert.model_dump_json()
        restored = Alert.model_validate_json(data)
        assert restored == alert


class TestRunbookResult:
    def test_success(self) -> None:
        result = RunbookResult(success=True, stdout="ok", stderr="", duration_sec=1.5)
        assert result.success
        assert result.duration_sec == 1.5


class TestExecutionResult:
    def test_full_result(self) -> None:
        dry = RunbookResult(success=True, stdout="dry", stderr="", duration_sec=0.5)
        exec_ = RunbookResult(success=True, stdout="done", stderr="", duration_sec=2.0)
        result = ExecutionResult(dry_run=dry, execute=exec_, verify=True, snapshot={"disk": "80%"})
        assert result.verify
        assert result.rolled_back is False


class TestAuditRecord:
    def test_create(self) -> None:
        alert = Alert(
            event_id="1",
            event_name="test",
            severity="low",
            hostname="h",
            host_ip="1.1.1.1",
            trigger_id="1",
            message="m",
            timestamp=datetime(2026, 1, 1),
            status="problem",
        )
        record = AuditRecord(
            alert=alert,
            workflow_id="wf-1",
            decision="approved",
            runbook_id="disk_cleanup",
            runbook_params={"path": "/tmp"},
            execution_result=None,
            feishu_message_id="msg-1",
            created_at=datetime(2026, 1, 1),
            completed_at=None,
        )
        assert record.decision == "approved"
        assert record.execution_result is None
