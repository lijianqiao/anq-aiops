from datetime import datetime

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.models import Alert, ExecutionResult, RunbookResult
from src.workflows.alert_workflow import AlertWorkflow, ApprovalDecision

# Mutable containers to control mock activity behavior per test
_calls: dict[str, list] = {}
_returns: dict[str, object] = {}


@activity.defn(name="send_feishu_alert")
async def mock_send_feishu_alert(alert_json: str, workflow_id: str) -> str:
    _calls.setdefault("send_feishu_alert", []).append((alert_json, workflow_id))
    return str(_returns.get("send_feishu_alert", "msg-default"))


@activity.defn(name="send_feishu_result")
async def mock_send_feishu_result(message: str) -> None:
    _calls.setdefault("send_feishu_result", []).append(message)


@activity.defn(name="execute_runbook")
async def mock_execute_runbook(runbook_id: str, params_json: str) -> str:
    _calls.setdefault("execute_runbook", []).append((runbook_id, params_json))
    return str(_returns.get("execute_runbook", "{}"))


@activity.defn(name="write_audit")
async def mock_write_audit(
    alert_json: str,
    workflow_id: str,
    decision: str,
    runbook_id: str | None,
    runbook_params_json: str | None,
    execution_result_json: str | None,
    feishu_message_id: str | None,
) -> str:
    _calls.setdefault("write_audit", []).append(decision)
    return "{}"


def _reset_mocks() -> None:
    _calls.clear()
    _returns.clear()


@pytest.fixture
def alert_json() -> str:
    alert = Alert(
        event_id="evt-test-001",
        event_name="Disk full",
        severity="high",
        hostname="web-01",
        host_ip="10.0.0.1",
        trigger_id="100",
        message="Disk usage 95%",
        timestamp=datetime(2026, 1, 1, 12, 0, 0),
        status="problem",
    )
    return alert.model_dump_json()


ALL_ACTIVITIES = [
    mock_send_feishu_alert,
    mock_send_feishu_result,
    mock_execute_runbook,
    mock_write_audit,
]


@pytest.mark.asyncio
async def test_workflow_approved(alert_json: str) -> None:
    _reset_mocks()
    mock_result = ExecutionResult(
        dry_run=RunbookResult(success=True, stdout="dry", stderr="", duration_sec=0.1),
        execute=RunbookResult(success=True, stdout="done", stderr="", duration_sec=1.0),
        verify=True,
        snapshot={},
    )
    _returns["send_feishu_alert"] = "msg-001"
    _returns["execute_runbook"] = mock_result.model_dump_json()

    async with await WorkflowEnvironment.start_time_skipping() as env, Worker(
        env.client,
        task_queue="test-queue",
        workflows=[AlertWorkflow],
        activities=ALL_ACTIVITIES,
    ):
        handle = await env.client.start_workflow(
            AlertWorkflow.run,
            alert_json,
            id="test-wf-001",
            task_queue="test-queue",
        )
        await handle.signal(AlertWorkflow.approve, ApprovalDecision(approved=True))
        result = await handle.result()
        assert result == "approved"


@pytest.mark.asyncio
async def test_workflow_rejected(alert_json: str) -> None:
    _reset_mocks()
    _returns["send_feishu_alert"] = "msg-002"

    async with await WorkflowEnvironment.start_time_skipping() as env, Worker(
        env.client,
        task_queue="test-queue",
        workflows=[AlertWorkflow],
        activities=ALL_ACTIVITIES,
    ):
        handle = await env.client.start_workflow(
            AlertWorkflow.run,
            alert_json,
            id="test-wf-002",
            task_queue="test-queue",
        )
        await handle.signal(AlertWorkflow.approve, ApprovalDecision(approved=False))
        result = await handle.result()
        assert result == "rejected"
