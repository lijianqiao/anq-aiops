from datetime import datetime

from src.models import ActionPlan, Alert, AuditRecord, ExecutionResult, RCAResult, RiskEvaluation, RunbookResult


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


def test_rca_result():
    rca = RCAResult(
        root_cause="/tmp 目录有大量过期文件",
        confidence=0.85,
        recommended_runbook="disk_cleanup",
        params={"target_host": "192.168.1.12", "target_path": "/tmp"},
        reasoning="磁盘使用率 95%，/tmp 目录占用最多",
    )
    assert rca.confidence == 0.85
    assert rca.recommended_runbook == "disk_cleanup"
    json_str = rca.model_dump_json()
    rca2 = RCAResult.model_validate_json(json_str)
    assert rca2.root_cause == rca.root_cause


def test_action_plan():
    plan = ActionPlan(
        runbook_id="disk_cleanup",
        params={"target_host": "192.168.1.12"},
        risk_level="low",
        requires_approval=True,
        reasoning="磁盘清理为低风险操作",
    )
    assert plan.risk_level == "low"
    assert plan.requires_approval is True


def test_risk_evaluation():
    risk = RiskEvaluation(
        approved=True,
        risk_score=0.2,
        reason="磁盘清理是低风险操作",
        auto_execute_eligible=True,
    )
    assert risk.approved is True
    assert risk.auto_execute_eligible is True
