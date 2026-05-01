import json
from datetime import timedelta

import pytest
from temporalio import activity
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.models import Alert, RCAResult, RiskEvaluation
from src.workflows.alert_workflow import AlertWorkflow, ApprovalDecision

TASK_QUEUE = "test-alerts"


def _alert_json() -> str:
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
    ).model_dump_json()


@activity.defn(name="rca_analyze")
async def mock_rca_analyze(alert_json: str) -> str:
    return RCAResult(
        root_cause="/tmp 满了",
        confidence=0.9,
        recommended_runbook="disk_cleanup",
        params={"target_host": "192.168.1.13"},
        reasoning="磁盘 95%",
    ).model_dump_json()


@activity.defn(name="plan_action")
async def mock_plan_action(alert_json: str, rca_json: str) -> str:
    return json.dumps({"runbook_id": "disk_cleanup", "params": {"target_host": "192.168.1.13"}})


@activity.defn(name="evaluate_risk")
async def mock_evaluate_risk(alert_json: str, plan_json: str) -> str:
    return RiskEvaluation(approved=True, risk_score=0.2, reason="低风险", auto_execute_eligible=True).model_dump_json()


@activity.defn(name="send_feishu_alert_with_ai")
async def mock_send_feishu_alert_with_ai(alert_json: str, workflow_id: str, rca_json: str, risk_json: str) -> str:
    return "msg_with_ai_123"


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
    return json.dumps({"dry_run": {"success": True}, "execute": {"success": True}, "verify": True, "snapshot": {}, "rolled_back": False})


ALL_ACTIVITIES = [
    mock_rca_analyze, mock_plan_action, mock_evaluate_risk,
    mock_send_feishu_alert_with_ai, mock_send_feishu_alert,
    mock_send_feishu_result, mock_write_audit, mock_execute_runbook,
]


@pytest.mark.asyncio
async def test_workflow_approved_with_ai():
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
                id="test-approved-ai",
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
async def test_workflow_degrades_when_llm_fails():
    """LLM 全部失败时应降级到纯人工模式"""

    @activity.defn(name="rca_analyze")
    async def failing_rca(alert_json: str) -> str:
        raise RuntimeError("LLM down")

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[AlertWorkflow],
            activities=[
                failing_rca, mock_plan_action, mock_evaluate_risk,
                mock_send_feishu_alert_with_ai, mock_send_feishu_alert,
                mock_send_feishu_result, mock_write_audit, mock_execute_runbook,
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
