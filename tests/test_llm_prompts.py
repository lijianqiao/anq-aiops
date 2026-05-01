from src.llm.prompts import build_plan_prompt, build_rca_prompt, build_risk_prompt
from src.models import ActionPlan, Alert, RCAResult


def _make_alert() -> Alert:
    return Alert(
        event_id="12345",
        event_name="Disk usage > 90%",
        severity="high",
        hostname="web-server-01",
        host_ip="192.168.1.13",
        trigger_id="10001",
        message="Disk usage is 95% on /tmp",
        timestamp="2026-04-30T14:30:00Z",
        status="problem",
    )


def test_build_rca_prompt_contains_alert_info():
    alert = _make_alert()
    prompt = build_rca_prompt(alert, runbook_list="disk_cleanup: ...\nservice_restart: ...")
    assert "web-server-01" in prompt
    assert "192.168.1.13" in prompt
    assert "Disk usage" in prompt
    assert "disk_cleanup" in prompt
    assert "<alert>" in prompt


def test_build_plan_prompt_contains_rca():
    alert = _make_alert()
    rca = RCAResult(
        root_cause="/tmp 满了",
        confidence=0.9,
        recommended_runbook="disk_cleanup",
        params={"target_host": "192.168.1.13"},
        reasoning="磁盘 95%",
    )
    prompt = build_plan_prompt(alert, rca, runbook_list="disk_cleanup: ...")
    assert "/tmp 满了" in prompt
    assert "disk_cleanup" in prompt


def test_build_risk_prompt_contains_plan():
    alert = _make_alert()
    plan = ActionPlan(
        runbook_id="disk_cleanup",
        params={"target_host": "192.168.1.13"},
        risk_level="low",
        requires_approval=True,
        reasoning="低风险",
    )
    prompt = build_risk_prompt(alert, plan)
    assert "disk_cleanup" in prompt
    assert "low" in prompt
