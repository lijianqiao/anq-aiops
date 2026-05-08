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


def _basic_plan() -> dict:
    return {
        "runbook_id": "disk_cleanup",
        "params": {"target_host": "192.168.198.130", "path": "/tmp", "min_age_days": 7},
        "risk_level": "low",
        "reasoning": "/tmp 占用 91%，最大",
        "confidence": 0.9,
    }


def _basic_trace() -> list[dict]:
    return [
        {"turn": 0, "tool": "get_disk_usage", "args": {"host": "192.168.198.130"}, "result_preview": "/tmp 91%"},
        {"turn": 1, "tool": "get_directory_sizes", "args": {"paths": ["/tmp", "/var/log"]}, "result_preview": "5.5G /tmp"},
    ]


def _approval_policy() -> dict:
    return {"decision": "approval_required", "matched_policy": "default", "reason": ""}


def test_build_feishu_card_simple():
    """无 agent 输出时的简卡"""
    card = build_feishu_card(_make_alert(), "wf-1")
    assert "header" in card
    assert "elements" in card
    s = str(card)
    assert "aiops-target" in s
    assert "192.168.198.130" in s


def test_build_feishu_card_with_agent_low_risk():
    card = build_feishu_card_with_agent(_make_alert(), "wf-1", _basic_plan(), _basic_trace(), _approval_policy())
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
    card = build_feishu_card_with_agent(_make_alert(), "wf-2", plan, [], _approval_policy())
    s = str(card)
    assert "🔴 高风险" in s
    assert "人工处理" in s


# ---- Phase 3 Task 9: Policy 标签 ----


def test_card_shows_policy_label_for_allow_live(monkeypatch):
    """live 模式 + allow → 卡片标 🤖 自动执行，不带审批按钮"""
    from src.config import settings

    monkeypatch.setattr(settings, "aiops_mode", "live")
    policy = {"decision": "allow", "matched_policy": "low_risk_disk_cleanup", "reason": "low risk auto"}
    card = build_feishu_card_with_agent(_make_alert(), "wf-1", _basic_plan(), [], policy)
    s = str(card)
    assert "🤖 自动执行" in s
    assert "low_risk_disk_cleanup" in s
    # ALLOW + live 不发按钮
    assert "按建议执行" not in s
    assert "拒绝" not in s


def test_card_shows_shadow_label_for_allow_shadow(monkeypatch):
    """shadow 模式 + allow → 卡片标 🌓 Shadow，仍带审批按钮"""
    from src.config import settings

    monkeypatch.setattr(settings, "aiops_mode", "shadow")
    policy = {"decision": "allow", "matched_policy": "low_risk_disk_cleanup", "reason": "low risk auto"}
    card = build_feishu_card_with_agent(_make_alert(), "wf-1", _basic_plan(), [], policy)
    s = str(card)
    assert "🌓" in s or "Shadow" in s
    # shadow 模式仍要审批
    assert "按建议执行" in s


def test_card_shows_approval_required_label():
    policy = {
        "decision": "approval_required",
        "matched_policy": "low_confidence_requires_approval",
        "reason": "agent 置信度 < 0.9 转人工",
    }
    card = build_feishu_card_with_agent(_make_alert(), "wf-1", _basic_plan(), [], policy)
    s = str(card)
    assert "👤" in s or "人工审批" in s
    assert "low_confidence_requires_approval" in s
    assert "按建议执行" in s


def test_card_shows_deny_label_no_buttons():
    """DENY 决策即便走到卡片渲染（一般不会，workflow 已早返回），也不应显示按钮"""
    policy = {"decision": "deny", "matched_policy": "deny_root_path_cleanup", "reason": "禁止根目录清理"}
    card = build_feishu_card_with_agent(_make_alert(), "wf-1", _basic_plan(), [], policy)
    s = str(card)
    assert "🚫" in s or "拒绝执行" in s
    assert "按建议执行" not in s
