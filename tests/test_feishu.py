"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: test_feishu.py
@DateTime: 2026-05-08 23:48:00
@Docs: 测试飞书告警卡片内容、Policy 标签和拒绝原因表单
"""

import concurrent.futures
from datetime import datetime
from typing import Any

from src.activities.feishu import build_feishu_card, build_feishu_card_with_agent
from src.feishu_listener import _build_card_action_handler
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
        timestamp=datetime.fromisoformat("2026-04-30T14:30:00+00:00"),
        status="problem",
    )


def _basic_plan() -> dict[str, Any]:
    return {
        "runbook_id": "disk_cleanup",
        "params": {"target_host": "192.168.198.130", "path": "/tmp", "min_age_days": 7},
        "risk_level": "low",
        "reasoning": "/tmp 占用 91%，最大",
        "confidence": 0.9,
    }


def _basic_trace() -> list[dict[str, Any]]:
    return [
        {"turn": 0, "tool": "get_disk_usage", "args": {"host": "192.168.198.130"}, "result_preview": "/tmp 91%"},
        {
            "turn": 1,
            "tool": "get_directory_sizes",
            "args": {"paths": ["/tmp", "/var/log"]},
            "result_preview": "5.5G /tmp",
        },
    ]


def _approval_policy() -> dict[str, Any]:
    return {"decision": "approval_required", "matched_policy": "default", "reason": ""}


def test_build_feishu_card_simple():
    """无 agent 输出时的简卡"""
    card = build_feishu_card(_make_alert(), "wf-1")
    assert "header" in card
    assert "elements" in card
    s = str(card)
    assert "aiops-target" in s
    assert "192.168.198.130" in s
    assert "拒绝原因" in s
    assert "reject_with_reason" in s


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


def test_card_reject_action_requires_reason():
    """审批卡片拒绝操作应携带原因输入表单。"""
    card = build_feishu_card_with_agent(_make_alert(), "wf-1", _basic_plan(), [], _approval_policy())
    text = str(card)
    assert "拒绝原因" in text
    assert "reject_with_reason" in text
    assert _action_tags(card) <= {"button"}
    assert any(element.get("tag") == "input" and element.get("name") == "reason" for element in card["elements"])


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


def test_card_action_signal_failure_returns_error_toast(monkeypatch):
    """Temporal signal 发送失败时，不应向用户返回成功提示。"""
    future: concurrent.futures.Future[None] = concurrent.futures.Future()
    future.set_exception(RuntimeError("workflow not found"))

    def fake_run_coroutine_threadsafe(coro, loop):
        coro.close()
        return future

    monkeypatch.setattr("asyncio.run_coroutine_threadsafe", fake_run_coroutine_threadsafe)
    handler = _build_card_action_handler(object(), object(), _FakeCardActionResponse)

    response = handler(_FakeCardActionData("wf-missing", "approve"))

    assert response.body["toast"]["type"] == "error"
    assert "workflow not found" in response.body["toast"]["content"]


def test_card_action_signal_timeout_cancels_future(monkeypatch):
    """Temporal signal 等待超时时，应取消 future 并返回错误提示。"""
    future = _TimeoutFuture()

    def fake_run_coroutine_threadsafe(coro, loop):
        coro.close()
        return future

    monkeypatch.setattr("asyncio.run_coroutine_threadsafe", fake_run_coroutine_threadsafe)
    handler = _build_card_action_handler(object(), object(), _FakeCardActionResponse)

    response = handler(_FakeCardActionData("wf-timeout", "approve"))

    assert future.cancelled
    assert response.body["toast"]["type"] == "error"
    assert "处理超时" in response.body["toast"]["content"]


def _action_tags(card: dict[str, Any]) -> set[str]:
    """返回所有 action block 内的 tag，防止把 form/input 放进 actions。"""
    tags: set[str] = set()
    for element in card["elements"]:
        if element.get("tag") == "action":
            tags.update(str(action.get("tag")) for action in element.get("actions", []))
    return tags


class _FakeCardActionResponse:
    """保存飞书回调响应体，便于断言 toast 内容。"""

    def __init__(self, body: dict[str, Any]) -> None:
        self.body = body


class _FakeCardActionData:
    """构造飞书卡片回调数据。"""

    def __init__(self, workflow_id: str, action: str) -> None:
        self.event = _FakeEvent(workflow_id, action)


class _FakeEvent:
    """构造飞书事件对象。"""

    def __init__(self, workflow_id: str, action: str) -> None:
        self.action = _FakeAction(workflow_id, action)
        self.operator = _FakeOperator()


class _FakeAction:
    """构造飞书卡片 action 对象。"""

    def __init__(self, workflow_id: str, action: str) -> None:
        self.value = {"workflow_id": workflow_id, "action": action}
        self.form_value: dict[str, str] = {}


class _FakeOperator:
    """构造飞书操作者对象。"""

    open_id = "ou_test"
    user_id = "user_test"


class _TimeoutFuture:
    """模拟跨线程 future 等待超时。"""

    cancelled = False

    def result(self, timeout: int) -> None:
        raise concurrent.futures.TimeoutError

    def cancel(self) -> None:
        self.cancelled = True
