from src.activities.feishu import build_feishu_card, build_feishu_card_with_agent
from src.models import Alert


def _make_alert() -> Alert:
    return Alert(
        event_id="12345",
        event_name="Disk usage > 90%",
        severity="high",
        hostname="aiops-target",
        host_ip="192.168.198.130",
        trigger_id="10001",
        message="Disk usage is 95% on /tmp",
        timestamp="2026-04-30T14:30:00Z",
        status="problem",
    )


def test_build_feishu_card_simple():
    """无 agent 输出时的简卡"""
    card = build_feishu_card(_make_alert(), "wf-1")
    assert "header" in card
    assert "elements" in card
    s = str(card)
    assert "aiops-target" in s
    assert "192.168.198.130" in s


def test_build_feishu_card_with_agent_low_risk():
    plan = {
        "runbook_id": "disk_cleanup",
        "params": {"target_host": "192.168.198.130", "path": "/tmp", "min_age_days": 7},
        "risk_level": "low",
        "reasoning": "/tmp 占用 91%，最大",
        "confidence": 0.9,
    }
    trace = [
        {"turn": 0, "tool": "get_disk_usage", "args": {"host": "192.168.198.130"}, "result_preview": "/tmp 91%"},
        {"turn": 1, "tool": "get_directory_sizes", "args": {"paths": ["/tmp", "/var/log"]}, "result_preview": "5.5G /tmp"},
    ]
    card = build_feishu_card_with_agent(_make_alert(), "wf-1", plan, trace)
    s = str(card)
    assert "AI 诊断" in s
    assert "诊断步骤" in s
    assert "/tmp 占用 91%" in s
    assert "disk_cleanup" in s
    assert "get_disk_usage" in s
    assert "🟢 低风险" in s


def test_build_feishu_card_with_agent_high_risk_inserts_human_button():
    plan = {
        "runbook_id": "service_restart",
        "params": {"target_host": "192.168.198.130", "service_name": "mysql"},
        "risk_level": "high",
        "reasoning": "mysql 挂了",
        "confidence": 0.7,
    }
    card = build_feishu_card_with_agent(_make_alert(), "wf-2", plan, [])
    s = str(card)
    assert "🔴 高风险" in s
    assert "人工处理" in s
