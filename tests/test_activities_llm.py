"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: test_activities_llm.py
@DateTime: 2026-05-08 14:36:00
@Docs: 测试 LLM Activity 对 ReAct Agent 结果和失败降级的处理
"""

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.llm.agent import AgentLimitExceeded, AgentResult
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
        timestamp=datetime.fromisoformat("2026-04-30T14:30:00+00:00"),
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

    fake_router = MagicMock()
    fake_router.diagnose_with_agent = AsyncMock(return_value=AgentResult(plan=plan, trace=trace))
    with patch.object(llm_activity, "llm_router", fake_router):
        result_json = await llm_activity.agent_diagnose(_alert_json())

    out = json.loads(result_json)
    assert out["plan"]["runbook_id"] == "disk_cleanup"
    assert out["plan"]["params"]["path"] == "/tmp"
    assert len(out["trace"]) == 2
    assert out["trace"][0]["tool"] == "get_disk_usage"


@pytest.mark.asyncio
async def test_agent_diagnose_handles_none_plan():
    from src.activities import llm as llm_activity

    fake_router = MagicMock()
    fake_router.diagnose_with_agent = AsyncMock(
        return_value=AgentResult(plan=None, trace=[{"turn": 0, "tool": "list_failed_services"}])
    )
    with patch.object(llm_activity, "llm_router", fake_router):
        result_json = await llm_activity.agent_diagnose(_alert_json())

    out = json.loads(result_json)
    assert out["plan"] is None
    assert out["trace"]


@pytest.mark.asyncio
async def test_agent_diagnose_handles_agent_failure():
    """Agent 5 轮没收敛时 activity 不应抛错，而是返回 agent_failed 标记"""
    from src.activities import llm as llm_activity

    fake_router = MagicMock()
    fake_router.diagnose_with_agent = AsyncMock(side_effect=AgentLimitExceeded("did not converge"))
    with patch.object(llm_activity, "llm_router", fake_router):
        result_json = await llm_activity.agent_diagnose(_alert_json())

    out = json.loads(result_json)
    assert out["plan"] is None
    assert out.get("agent_failed") is True


@pytest.mark.asyncio
async def test_agent_diagnose_injects_hermes_cases(monkeypatch: pytest.MonkeyPatch) -> None:
    """agent_diagnose 应查询 Hermes 并把历史案例文本传给 router。"""
    from src.activities import llm as llm_activity

    plan = ActionPlan(
        runbook_id="disk_cleanup",
        params={"target_host": "192.168.198.130", "path": "/tmp", "min_age_days": 7},
        risk_level="low",
        requires_approval=True,
        reasoning="参考历史案例后仍基于事实执行",
        confidence=0.9,
    )
    fake_router = MagicMock()
    fake_router.diagnose_with_agent = AsyncMock(return_value=AgentResult(plan=plan, trace=[]))

    async def fake_query(repo, alert, limit):
        assert limit == 3
        return ["case-1"]

    def fake_format(cases):
        assert cases == ["case-1"]
        return "历史案例：Disk usage > 90%"

    monkeypatch.setattr(llm_activity, "llm_router", fake_router)
    monkeypatch.setattr(llm_activity, "hermes_repo", object())
    monkeypatch.setattr("src.activities.llm.hermes_knowledge.query_similar_cases", fake_query)
    monkeypatch.setattr("src.activities.llm.hermes_knowledge.format_cases_for_prompt", fake_format)

    await llm_activity.agent_diagnose(_alert_json())

    fake_router.diagnose_with_agent.assert_awaited_once()
    assert fake_router.diagnose_with_agent.call_args.kwargs["past_cases_text"] == "历史案例：Disk usage > 90%"
