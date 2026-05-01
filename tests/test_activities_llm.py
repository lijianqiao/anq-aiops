from unittest.mock import AsyncMock, patch

import pytest

from src.models import ActionPlan, Alert, RCAResult, RiskEvaluation


def _alert_json() -> str:
    alert = Alert(
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
    return alert.model_dump_json()


@pytest.mark.asyncio
async def test_rca_analyze():
    from src.activities.llm import rca_analyze

    rca = RCAResult(
        root_cause="/tmp 满了",
        confidence=0.9,
        recommended_runbook="disk_cleanup",
        params={"target_host": "192.168.1.13"},
        reasoning="磁盘 95%",
    )
    mock_router = AsyncMock()
    mock_router.invoke.return_value = rca

    with patch("src.activities.llm.llm_router", mock_router):
        result_json = await rca_analyze(_alert_json())
        result = RCAResult.model_validate_json(result_json)

    assert result.root_cause == "/tmp 满了"
    assert result.recommended_runbook == "disk_cleanup"


@pytest.mark.asyncio
async def test_plan_action():
    from src.activities.llm import plan_action

    rca = RCAResult(
        root_cause="/tmp 满了",
        confidence=0.9,
        recommended_runbook="disk_cleanup",
        params={"target_host": "192.168.1.13"},
        reasoning="磁盘 95%",
    )
    plan = ActionPlan(
        runbook_id="disk_cleanup",
        params={"target_host": "192.168.1.13"},
        risk_level="low",
        requires_approval=True,
        reasoning="低风险",
    )
    mock_router = AsyncMock()
    mock_router.invoke.return_value = plan

    with patch("src.activities.llm.llm_router", mock_router):
        result_json = await plan_action(_alert_json(), rca.model_dump_json())
        result = ActionPlan.model_validate_json(result_json)

    assert result.runbook_id == "disk_cleanup"
    assert result.risk_level == "low"


@pytest.mark.asyncio
async def test_evaluate_risk():
    from src.activities.llm import evaluate_risk

    plan = ActionPlan(
        runbook_id="disk_cleanup",
        params={"target_host": "192.168.1.13"},
        risk_level="low",
        requires_approval=True,
        reasoning="低风险",
    )
    risk = RiskEvaluation(
        approved=True,
        risk_score=0.2,
        reason="低风险操作",
        auto_execute_eligible=True,
    )
    mock_router = AsyncMock()
    mock_router.invoke.return_value = risk

    with patch("src.activities.llm.llm_router", mock_router):
        result_json = await evaluate_risk(_alert_json(), plan.model_dump_json())
        result = RiskEvaluation.model_validate_json(result_json)

    assert result.approved is True
    assert result.auto_execute_eligible is True
