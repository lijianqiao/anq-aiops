"""ReAct agent_diagnose activity 测试"""

import json
from unittest.mock import patch

import pytest

from src.llm.agent import AgentResult
from src.models import ActionPlan, Alert


def _alert_json() -> str:
    alert = Alert(
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
    return alert.model_dump_json()


@pytest.mark.asyncio
async def test_agent_diagnose_returns_plan_and_trace():
    from src.activities import llm as llm_activity

    plan = ActionPlan(
        runbook_id="disk_cleanup",
        params={"target_host": "192.168.198.130", "path": "/tmp", "min_age_days": 7},
        risk_level="low",
        requires_approval=True,
        reasoning="/tmp 占用最大",
        confidence=0.9,
    )
    trace = [
        {"turn": 0, "tool": "get_disk_usage", "args": {"host": "192.168.198.130"}, "result_preview": "..."},
        {"turn": 1, "tool": "get_directory_sizes", "args": {"paths": ["/tmp", "/var/log"]}, "result_preview": "..."},
    ]

    async def fake_diagnose(_self, _alert):
        return AgentResult(plan=plan, trace=trace)

    # 给 module 级 llm_router 一个非 None 占位（否则会被 RuntimeError 拦截）
    with patch.object(llm_activity, "llm_router", object()), \
         patch("src.llm.agent.DiagnosticAgent.diagnose", new=fake_diagnose):
        result_json = await llm_activity.agent_diagnose(_alert_json())

    out = json.loads(result_json)
    assert out["plan"]["runbook_id"] == "disk_cleanup"
    assert out["plan"]["params"]["path"] == "/tmp"
    assert len(out["trace"]) == 2
    assert out["trace"][0]["tool"] == "get_disk_usage"


@pytest.mark.asyncio
async def test_agent_diagnose_handles_none_plan():
    from src.activities import llm as llm_activity

    async def fake_diagnose(_self, _alert):
        return AgentResult(plan=None, trace=[{"turn": 0, "tool": "list_failed_services"}])

    with patch.object(llm_activity, "llm_router", object()), \
         patch("src.llm.agent.DiagnosticAgent.diagnose", new=fake_diagnose):
        result_json = await llm_activity.agent_diagnose(_alert_json())

    out = json.loads(result_json)
    assert out["plan"] is None
    assert out["trace"]


@pytest.mark.asyncio
async def test_agent_diagnose_handles_agent_failure():
    """Agent 5 轮没收敛时 activity 不应抛错，而是返回 agent_failed 标记"""
    from src.activities import llm as llm_activity
    from src.llm.agent import AgentLimitExceeded

    async def failing_diagnose(_self, _alert):
        raise AgentLimitExceeded("did not converge")

    with patch.object(llm_activity, "llm_router", object()), \
         patch("src.llm.agent.DiagnosticAgent.diagnose", new=failing_diagnose):
        result_json = await llm_activity.agent_diagnose(_alert_json())

    out = json.loads(result_json)
    assert out["plan"] is None
    assert out.get("agent_failed") is True
