from src.activities.feishu import build_feishu_card_with_ai
from src.models import Alert, RCAResult, RiskEvaluation


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


def test_build_feishu_card_with_ai():
    alert = _make_alert()
    rca = RCAResult(
        root_cause="/tmp 目录过期文件过多",
        confidence=0.85,
        recommended_runbook="disk_cleanup",
        params={"target_host": "192.168.1.13", "target_path": "/tmp"},
        reasoning="磁盘 95%",
    )
    risk = RiskEvaluation(
        approved=True,
        risk_score=0.2,
        reason="低风险",
        auto_execute_eligible=True,
    )

    card = build_feishu_card_with_ai(alert, "wf-123", rca, risk)

    assert card["msg_type"] == "interactive"
    card_str = str(card)
    assert "AI 分析" in card_str
    assert "/tmp 目录过期文件过多" in card_str
    assert "disk_cleanup" in card_str
    assert "85%" in card_str


def test_build_feishu_card_with_ai_high_risk():
    alert = _make_alert()
    rca = RCAResult(
        root_cause="数据库连接池耗尽",
        confidence=0.7,
        recommended_runbook="service_restart",
        params={"target_host": "192.168.1.13", "service_name": "mysql"},
        reasoning="连接数异常",
    )
    risk = RiskEvaluation(
        approved=False,
        risk_score=0.8,
        reason="重启数据库风险高",
        auto_execute_eligible=False,
    )

    card = build_feishu_card_with_ai(alert, "wf-456", rca, risk)
    card_str = str(card)
    assert "高风险" in card_str or "风险" in card_str
