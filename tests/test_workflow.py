import json

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.models import Alert
from src.workflows.alert_workflow import AlertWorkflow, ApprovalDecision

TASK_QUEUE = "test-alerts"


def _alert_json(event_name: str = "Disk usage > 90%") -> str:
    return Alert(
        event_id="12345",
        event_name=event_name,
        severity="high",
        hostname="aiops-target",
        host_ip="192.168.198.130",
        trigger_id="10001",
        message="Disk usage is 95% on /tmp",
        timestamp="2026-04-30T14:30:00Z",
        status="problem",
    ).model_dump_json()


@activity.defn(name="agent_diagnose")
async def mock_agent_diagnose(alert_json: str) -> str:
    return json.dumps({
        "plan": {
            "runbook_id": "disk_cleanup",
            "params": {"target_host": "192.168.198.130", "path": "/tmp", "min_age_days": 7},
            "risk_level": "low",
            "requires_approval": True,
            "reasoning": "/tmp 占用 91%",
            "trace": [],
            "confidence": 0.9,
        },
        "trace": [
            {"turn": 0, "tool": "get_disk_usage", "args": {}, "result_preview": "/tmp 91%"},
        ],
    })


@activity.defn(name="send_feishu_alert_with_agent")
async def mock_send_feishu_alert_with_agent(
    alert_json: str,
    workflow_id: str,
    agent_output_json: str,
    policy_result_json: str = "{}",
) -> str:
    return "msg_with_agent_123"


@activity.defn(name="evaluate_policy_activity")
async def mock_evaluate_policy(
    runbook_id: str, runbook_params_json: str, alert_json: str, plan_json: str
) -> str:
    """默认返回 APPROVAL_REQUIRED，让现有测试都走原审批路径"""
    return '{"decision":"approval_required","matched_policy":"default","reason":""}'


@activity.defn(name="send_feishu_alert")
async def mock_send_feishu_alert(alert_json: str, workflow_id: str) -> str:
    return "msg_123"


@activity.defn(name="send_feishu_result")
async def mock_send_feishu_result(message: str) -> None:
    pass


@activity.defn(name="write_audit")
async def mock_write_audit(
    alert_json: str, workflow_id: str, decision: str, runbook_id: str | None,
    runbook_params: str | None, exec_result_json: str | None, feishu_message_id: str | None,
) -> str:
    return "{}"


@activity.defn(name="execute_runbook")
async def mock_execute_runbook(runbook_id: str, params_json: str) -> str:
    return json.dumps({
        "dry_run": {"success": True, "stdout": "", "stderr": "", "duration_sec": 1.0},
        "execute": {"success": True, "stdout": "", "stderr": "", "duration_sec": 1.0},
        "verify": True,
        "snapshot": {},
        "rolled_back": False,
    })


ALL_ACTIVITIES = [
    mock_agent_diagnose, mock_evaluate_policy,
    mock_send_feishu_alert_with_agent, mock_send_feishu_alert,
    mock_send_feishu_result, mock_write_audit, mock_execute_runbook,
]


@pytest.mark.asyncio
async def test_workflow_approved_with_agent():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[AlertWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await env.client.start_workflow(
                AlertWorkflow.run,
                _alert_json(),
                id="test-approved-agent",
                task_queue=TASK_QUEUE,
            )
            await handle.signal(AlertWorkflow.approve, ApprovalDecision(approved=True))
            result = await handle.result()
            assert result == "approved"


@pytest.mark.asyncio
async def test_workflow_rejected():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[AlertWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await env.client.start_workflow(
                AlertWorkflow.run,
                _alert_json(),
                id="test-rejected",
                task_queue=TASK_QUEUE,
            )
            await handle.signal(AlertWorkflow.approve, ApprovalDecision(approved=False))
            result = await handle.result()
            assert result == "rejected"


@pytest.mark.asyncio
async def test_workflow_falls_back_when_agent_fails():
    """agent_diagnose 抛错时 workflow 应降级到关键词匹配，磁盘告警还能走 disk_cleanup"""

    @activity.defn(name="agent_diagnose")
    async def failing_agent(alert_json: str) -> str:
        raise RuntimeError("agent crashed")

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[AlertWorkflow],
            activities=[
                failing_agent,
                mock_send_feishu_alert_with_agent, mock_send_feishu_alert,
                mock_send_feishu_result, mock_write_audit, mock_execute_runbook,
                mock_evaluate_policy,
            ],
        ):
            handle = await env.client.start_workflow(
                AlertWorkflow.run,
                _alert_json(),
                id="test-degraded",
                task_queue=TASK_QUEUE,
            )
            await handle.signal(AlertWorkflow.approve, ApprovalDecision(approved=True))
            result = await handle.result()
            assert result == "approved"


@pytest.mark.asyncio
async def test_workflow_unsupported_when_no_runbook_match():
    """agent 失败 + 关键词也匹配不上 → unsupported

    构造完全不含 disk/service/磁盘/进程 等关键词的告警（注意 message 也要纯净，
    因为 _select_runbook 现在同时看 event_name + message）
    """

    @activity.defn(name="agent_diagnose")
    async def failing_agent(alert_json: str) -> str:
        raise RuntimeError("agent crashed")

    # 用一个完全不含磁盘/服务关键词的 alert
    no_match_alert = Alert(
        event_id="999",
        event_name="CPU temperature too high",
        severity="warning",
        hostname="aiops-target",
        host_ip="192.168.198.130",
        trigger_id="t-999",
        message="Sensor reading unusual",
        timestamp="2026-04-30T14:30:00Z",
        status="problem",
    ).model_dump_json()

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[AlertWorkflow],
            activities=[
                failing_agent,
                mock_send_feishu_alert_with_agent, mock_send_feishu_alert,
                mock_send_feishu_result, mock_write_audit, mock_execute_runbook,
                mock_evaluate_policy,
            ],
        ):
            handle = await env.client.start_workflow(
                AlertWorkflow.run,
                no_match_alert,
                id="test-unsupported",
                task_queue=TASK_QUEUE,
            )
            await handle.signal(AlertWorkflow.approve, ApprovalDecision(approved=True))
            result = await handle.result()
            assert result == "unsupported"


@pytest.mark.asyncio
async def test_workflow_handles_agent_choosing_none():
    """agent 选 'none' 时 plan 是 null，走 keyword fallback"""

    @activity.defn(name="agent_diagnose")
    async def none_agent(alert_json: str) -> str:
        return json.dumps({"plan": None, "trace": [{"turn": 0, "tool": "list_failed_services"}]})

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[AlertWorkflow],
            activities=[
                none_agent,
                mock_send_feishu_alert_with_agent, mock_send_feishu_alert,
                mock_send_feishu_result, mock_write_audit, mock_execute_runbook,
                mock_evaluate_policy,
            ],
        ):
            handle = await env.client.start_workflow(
                AlertWorkflow.run,
                _alert_json(),  # 磁盘告警，关键词降级到 disk_cleanup
                id="test-agent-none",
                task_queue=TASK_QUEUE,
            )
            await handle.signal(AlertWorkflow.approve, ApprovalDecision(approved=True))
            result = await handle.result()
            assert result == "approved"
