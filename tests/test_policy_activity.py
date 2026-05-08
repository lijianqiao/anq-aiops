"""evaluate_policy_activity Temporal activity 测试"""

from unittest.mock import patch

import pytest

from src.activities.policy import evaluate_policy_activity
from src.models import Decision, PolicyResult


@pytest.mark.asyncio
async def test_evaluate_policy_activity_returns_json():
    """activity 接收 json 输入，返回 json 输出（Temporal 友好）"""
    fake_result = PolicyResult(
        decision=Decision.ALLOW,
        matched_policy="test_rule",
        reason="test",
    )
    with patch("src.activities.policy.evaluate_policy", return_value=fake_result):
        result_json = await evaluate_policy_activity(
            runbook_id="disk_cleanup",
            runbook_params_json='{"target_host": "1.1.1.1", "path": "/tmp"}',
            alert_json='{"severity": "high"}',
            plan_json='{"risk_level": "low", "confidence": 0.95}',
        )

    decoded = PolicyResult.model_validate_json(result_json)
    assert decoded.decision == Decision.ALLOW
    assert decoded.matched_policy == "test_rule"


@pytest.mark.asyncio
async def test_evaluate_policy_activity_handles_null_plan():
    """plan_json 可以是 'null'（agent 失败时），应转成空 dict 评估"""
    fake_result = PolicyResult(
        decision=Decision.APPROVAL_REQUIRED,
        matched_policy="default",
        reason="",
    )
    with patch("src.activities.policy.evaluate_policy", return_value=fake_result) as mock_eval:
        await evaluate_policy_activity(
            runbook_id="disk_cleanup",
            runbook_params_json='{"target_host": "1.1.1.1"}',
            alert_json='{"severity": "high"}',
            plan_json="null",
        )

    # 确认 evaluate_policy 被调用时 plan 是空 dict 而非 None
    call_kwargs = mock_eval.call_args.kwargs
    assert call_kwargs["plan"] == {}


@pytest.mark.asyncio
async def test_evaluate_policy_activity_handles_empty_strings():
    """空字符串输入时也不应崩"""
    fake_result = PolicyResult(
        decision=Decision.APPROVAL_REQUIRED,
        matched_policy="default",
        reason="",
    )
    with patch("src.activities.policy.evaluate_policy", return_value=fake_result):
        result_json = await evaluate_policy_activity(
            runbook_id="disk_cleanup",
            runbook_params_json="",
            alert_json="",
            plan_json="",
        )
    decoded = PolicyResult.model_validate_json(result_json)
    assert decoded.decision == Decision.APPROVAL_REQUIRED


@pytest.mark.asyncio
async def test_evaluate_policy_activity_safe_on_yaml_failure(monkeypatch):
    """yaml 文件不存在时不抛错，降级返回 APPROVAL_REQUIRED 让 workflow 走人工"""
    from src.config import settings
    monkeypatch.setattr(settings, "policy_config_path", "/nonexistent/policies.yaml")

    result_json = await evaluate_policy_activity(
        runbook_id="disk_cleanup",
        runbook_params_json='{"target_host": "1.1.1.1"}',
        alert_json="{}",
        plan_json='{"risk_level": "low", "confidence": 0.9}',
    )
    decoded = PolicyResult.model_validate_json(result_json)
    assert decoded.decision == Decision.APPROVAL_REQUIRED
    assert "policy evaluation error" in decoded.reason.lower()


@pytest.mark.asyncio
async def test_evaluate_policy_activity_safe_on_invalid_json():
    """params/alert/plan 输入是非法 JSON 时降级，不能让告警因为 policy 卡住"""
    result_json = await evaluate_policy_activity(
        runbook_id="disk_cleanup",
        runbook_params_json="{not valid json",
        alert_json="{}",
        plan_json="{}",
    )
    decoded = PolicyResult.model_validate_json(result_json)
    assert decoded.decision == Decision.APPROVAL_REQUIRED
