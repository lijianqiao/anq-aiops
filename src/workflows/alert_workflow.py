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
    """告警处理主工作流（Phase 2: 集成 LLM 分析）"""

    def __init__(self) -> None:
        self._approval_received = False
        self._approved = False

    @workflow.run
    async def run(self, alert_json: str) -> str:
        alert = json.loads(alert_json)
        event_id = alert["event_id"]
        workflow_id = workflow.info().workflow_id

        # 1. LLM RCA 分析
        rca_json = await self._safe_llm_call("rca_analyze", alert_json)

        # 2. LLM Action Plan
        plan_json = None
        risk_json = None
        if rca_json:
            plan_json = await self._safe_llm_call("plan_action", alert_json, rca_json)

        # 3. LLM Risk Evaluation
        if plan_json:
            risk_json = await self._safe_llm_call("evaluate_risk", alert_json, plan_json)

        # 4. 推送飞书卡片（带或不带 AI 分析）
        if rca_json and risk_json:
            feishu_msg_id = await workflow.execute_activity(
                "send_feishu_alert_with_ai",
                args=[alert_json, workflow_id, rca_json, risk_json],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
        else:
            feishu_msg_id = await workflow.execute_activity(
                "send_feishu_alert",
                args=[alert_json, workflow_id],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )

        # 5. 等待审批信号（30 分钟超时）
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

        # 6. 执行 Runbook（优先用 AI 推荐的，fallback 到关键词匹配）
        if plan_json:
            plan = json.loads(plan_json)
            runbook_id = plan.get("runbook_id", _select_runbook(alert))
            runbook_params = json.dumps(plan.get("params", {"target_host": alert["host_ip"]}))
        else:
            runbook_id = _select_runbook(alert)
            runbook_params = json.dumps({"target_host": alert["host_ip"]})

        exec_result_json = await workflow.execute_activity(
            "execute_runbook",
            args=[runbook_id, runbook_params],
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )

        # 7. 写审计
        await workflow.execute_activity(
            "write_audit",
            args=[alert_json, workflow_id, "approved", runbook_id, runbook_params, exec_result_json, feishu_msg_id],
            start_to_close_timeout=timedelta(seconds=10),
        )

        # 8. 飞书通知结果
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

    async def _safe_llm_call(self, activity_name: str, *args: str) -> str | None:
        """安全调用 LLM Activity，失败返回 None（降级模式）"""
        try:
            return await workflow.execute_activity(
                activity_name,
                args=list(args),
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )
        except Exception:
            workflow.logger.warning(f"LLM activity {activity_name} failed, degrading to non-AI mode")
            return None

    @workflow.signal
    def approve(self, decision: ApprovalDecision) -> None:
        """接收飞书审批回调信号"""
        self._approval_received = True
        self._approved = decision.approved
