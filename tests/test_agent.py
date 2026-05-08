"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: test_agent.py
@DateTime: 2026-05-08 23:26:00
@Docs: 测试 ReAct DiagnosticAgent 工具调用、终止条件和 Hermes prompt 注入
"""

import json
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.llm.agent import AgentLimitExceeded, DiagnosticAgent
from src.models import Alert


def _alert() -> Alert:
    return Alert(
        event_id="evt-1",
        event_name="Disk usage > 90%",
        severity="high",
        hostname="aiops-target",
        host_ip="192.168.198.130",
        trigger_id="t-1",
        message="Disk usage is 95% on /tmp",
        timestamp=datetime.fromisoformat("2026-05-07T10:00:00+00:00"),
        status="problem",
    )


def _tool_call(name: str, args: dict[str, Any], call_id: str = "call_1") -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


@pytest.mark.asyncio
async def test_agent_terminates_on_propose_action():
    """LLM 第一轮就调 propose_action 应该立刻返回"""
    client = AsyncMock()
    client.chat_with_tools.return_value = {
        "content": None,
        "tool_calls": [
            _tool_call(
                "propose_action",
                {
                    "runbook_id": "disk_cleanup",
                    "params": {"target_host": "192.168.198.130", "path": "/tmp", "min_age_days": 7},
                    "reasoning": "test",
                    "confidence": 0.9,
                    "risk_level": "low",
                },
            ),
        ],
    }
    agent = DiagnosticAgent(client, max_turns=5)
    result = await agent.diagnose(_alert())

    plan = result.plan
    assert plan is not None
    assert plan.runbook_id == "disk_cleanup"
    assert plan.params["path"] == "/tmp"
    assert client.chat_with_tools.call_count == 1


@pytest.mark.asyncio
async def test_agent_calls_tool_then_proposes():
    """LLM 先调诊断工具 → 看到结果 → 第二轮才 propose"""
    from src.llm import diagnostic_tools

    # 第一轮调 get_disk_usage，第二轮 propose
    client = AsyncMock()
    client.chat_with_tools.side_effect = [
        {"content": None, "tool_calls": [_tool_call("get_disk_usage", {"host": "192.168.198.130"}, "c1")]},
        {
            "content": None,
            "tool_calls": [
                _tool_call(
                    "propose_action",
                    {
                        "runbook_id": "disk_cleanup",
                        "params": {"target_host": "192.168.198.130", "path": "/tmp", "min_age_days": 7},
                        "reasoning": "/tmp 91%",
                        "confidence": 0.9,
                        "risk_level": "low",
                    },
                    "c2",
                )
            ],
        },
    ]

    async def fake_get_disk_usage(host):
        assert host == "192.168.198.130"
        return "/dev/sda1   14G   12G   1.3G   91%   /"

    diagnostic_tools.TOOL_HANDLERS["get_disk_usage"] = fake_get_disk_usage
    try:
        agent = DiagnosticAgent(client, max_turns=5)
        result = await agent.diagnose(_alert())
    finally:
        from src.llm.diagnostic_tools import get_disk_usage

        diagnostic_tools.TOOL_HANDLERS["get_disk_usage"] = get_disk_usage

    plan = result.plan
    assert plan is not None
    assert plan.runbook_id == "disk_cleanup"
    # trace 应包含两步：get_disk_usage + propose_action
    tool_names = [t["tool"] for t in result.trace]
    assert "get_disk_usage" in tool_names
    assert "propose_action" in tool_names


@pytest.mark.asyncio
async def test_agent_raises_when_no_tool_call():
    """LLM 只回文本不调工具应该被认为是失败"""
    client = AsyncMock()
    client.chat_with_tools.return_value = {"content": "I think it is /var/log", "tool_calls": []}
    agent = DiagnosticAgent(client, max_turns=5)
    with pytest.raises(AgentLimitExceeded):
        await agent.diagnose(_alert())


@pytest.mark.asyncio
async def test_agent_raises_on_max_turns():
    """LLM 一直调诊断工具不收敛应在 max_turns 后报错"""
    from src.llm import diagnostic_tools

    client = AsyncMock()
    client.chat_with_tools.return_value = {
        "content": None,
        "tool_calls": [_tool_call("get_disk_usage", {"host": "192.168.198.130"})],
    }

    async def fake_get_disk_usage(host):
        return "ok"

    original = diagnostic_tools.TOOL_HANDLERS["get_disk_usage"]
    diagnostic_tools.TOOL_HANDLERS["get_disk_usage"] = fake_get_disk_usage
    try:
        agent = DiagnosticAgent(client, max_turns=3)
        with pytest.raises(AgentLimitExceeded):
            await agent.diagnose(_alert())
        assert client.chat_with_tools.call_count == 3
    finally:
        diagnostic_tools.TOOL_HANDLERS["get_disk_usage"] = original


@pytest.mark.asyncio
async def test_agent_handles_none_runbook():
    """LLM 选 'none' 表示无合适修复 → result.plan is None"""
    client = AsyncMock()
    client.chat_with_tools.return_value = {
        "content": None,
        "tool_calls": [
            _tool_call(
                "propose_action",
                {
                    "runbook_id": "none",
                    "params": {},
                    "reasoning": "需要人工介入",
                    "confidence": 0.7,
                    "risk_level": "high",
                },
            )
        ],
    }
    agent = DiagnosticAgent(client, max_turns=5)
    result = await agent.diagnose(_alert())
    assert result.plan is None


@pytest.mark.asyncio
async def test_agent_includes_past_cases_in_system_prompt():
    """Hermes 历史案例应注入 system prompt 第一条消息。"""
    client = AsyncMock()
    client.chat_with_tools.return_value = {
        "content": None,
        "tool_calls": [
            _tool_call(
                "propose_action",
                {
                    "runbook_id": "disk_cleanup",
                    "params": {},
                    "reasoning": "参考历史但仍需工具事实",
                    "confidence": 0.9,
                    "risk_level": "low",
                },
            ),
        ],
    }
    past_cases_text = "1. [2026-05-01] `Disk usage > 90%` on 1.1.1.1\n   - Runbook: disk_cleanup\n"
    agent = DiagnosticAgent(client, max_turns=2, past_cases_text=past_cases_text)

    await agent.diagnose(_alert())

    sent_messages = client.chat_with_tools.call_args.kwargs["messages"]
    system_message = sent_messages[0]
    assert system_message["role"] == "system"
    assert "Past Experiences" in system_message["content"]
    assert "Disk usage > 90%" in system_message["content"]


@pytest.mark.asyncio
async def test_agent_includes_negative_cases_in_system_prompt():
    """Hermes 反例应注入 system prompt 的反例反馈段。"""
    client = AsyncMock()
    client.chat_with_tools.return_value = {
        "content": None,
        "tool_calls": [
            _tool_call(
                "propose_action",
                {
                    "runbook_id": "disk_cleanup",
                    "params": {},
                    "reasoning": "避免历史错误",
                    "confidence": 0.9,
                    "risk_level": "low",
                },
            ),
        ],
    }
    agent = DiagnosticAgent(client, max_turns=2, negative_cases_text="避坑案例：不要清理 /etc")

    await agent.diagnose(_alert())

    system_message = client.chat_with_tools.call_args.kwargs["messages"][0]
    assert "反例反馈" in system_message["content"]
    assert "不要清理 /etc" in system_message["content"]
