import json
from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy


def _select_runbook(alert: dict) -> str:
    """Phase 1 简单匹配：按告警名称关键词选 Runbook"""
    name = alert.get("event_name", "").lower()
    if "disk" in name or "磁盘" in name:
        return "disk_cleanup"
    if "service" in name or "进程" in name or "process" in name:
        return "service_restart"
    return "disk_cleanup"


@dataclass
class ApprovalDecision:
    """审批决策信号载荷"""

    approved: bool


@workflow.defn
class AlertWorkflow:
    """告警处理主工作流"""

    def __init__(self) -> None:
        self._approval_received = False
        self._approved = False

    @workflow.run
    async def run(self, alert_json: str) -> str:
        alert = json.loads(alert_json)
        event_id = alert["event_id"]
        workflow_id = workflow.info().workflow_id

        # 1. 推送飞书告警卡片
        feishu_msg_id = await workflow.execute_activity(
            "send_feishu_alert",
            args=[alert_json, workflow_id],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        # 2. 等待审批信号（30 分钟超时）
        try:
            await workflow.wait_condition(
                lambda: self._approval_received,
                timeout=timedelta(minutes=30),
            )
        except TimeoutError:
            await workflow.execute_activity(
                "write_audit",
                args=[alert_json, workflow_id, "timeout", None, None, None, feishu_msg_id],
                start_to_close_timeout=timedelta(seconds=10),
            )
            await workflow.execute_activity(
                "send_feishu_result",
                args=[f"⏰ 告警 {event_id} 审批超时（30分钟），已跳过"],
                start_to_close_timeout=timedelta(seconds=10),
            )
            return "timeout"

        if not self._approved:
            await workflow.execute_activity(
                "write_audit",
                args=[alert_json, workflow_id, "rejected", None, None, None, feishu_msg_id],
                start_to_close_timeout=timedelta(seconds=10),
            )
            await workflow.execute_activity(
                "send_feishu_result",
                args=[f"❌ 告警 {event_id} 已被拒绝"],
                start_to_close_timeout=timedelta(seconds=10),
            )
            return "rejected"

        # 3. 执行 Runbook
        runbook_id = _select_runbook(alert)
        runbook_params = json.dumps({"target_host": alert["host_ip"]})

        exec_result_json = await workflow.execute_activity(
            "execute_runbook",
            args=[runbook_id, runbook_params],
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )

        # 4. 写审计
        await workflow.execute_activity(
            "write_audit",
            args=[alert_json, workflow_id, "approved", runbook_id, runbook_params, exec_result_json, feishu_msg_id],
            start_to_close_timeout=timedelta(seconds=10),
        )

        # 5. 飞书通知结果
        exec_result = json.loads(exec_result_json)
        if exec_result.get("verify"):
            msg = f"✅ 告警 {event_id} 处理成功（Runbook: {runbook_id}）"
        else:
            msg = f"⚠️ 告警 {event_id} 执行完成但验证未通过，可能需要人工介入"

        await workflow.execute_activity(
            "send_feishu_result",
            args=[msg],
            start_to_close_timeout=timedelta(seconds=10),
        )

        return "approved"

    @workflow.signal
    def approve(self, decision: ApprovalDecision) -> None:
        """接收飞书审批回调信号"""
        self._approval_received = True
        self._approved = decision.approved
