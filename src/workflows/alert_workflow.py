"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: alert_workflow.py
@DateTime: 2026-05-08 14:33:00
@Docs: 定义告警处置 Temporal Workflow 与审批信号处理流程
"""

import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, cast

from temporalio import workflow
from temporalio.common import RetryPolicy

# settings 实例化会读 .env（触发 pathlib.expanduser），Temporal sandbox 默认拦
# 用 imports_passed_through 让 sandbox 跳过对它的检查
with workflow.unsafe.imports_passed_through():
    from src.config import settings

# 白名单与 src/runbooks/__init__.py 的 RUNBOOK_REGISTRY 保持同步。
# 不直接 import RUNBOOK_REGISTRY 是为了让 workflow 模块保持轻量、可被 sandbox 检查。
_KNOWN_RUNBOOKS = frozenset({"disk_cleanup", "service_restart"})

# 通知/审计类活动的统一重试策略，避免飞书暂时 5xx 让 workflow 永远卡住
_NOTIFY_RETRY = RetryPolicy(maximum_attempts=5)


def _select_runbook(alert: dict[str, Any]) -> str | None:
    """Agent 失败时的 fallback：从 event_name + message 用关键词选 Runbook

    覆盖 Zabbix 7.0 模板默认触发器名（如 "Linux: FS [/]: Space is critically low"）
    """
    text = (alert.get("event_name", "") + " " + alert.get("message", "")).lower()

    # 磁盘类：Zabbix 模板叫 "FS", "Space", "filesystem"，自定义可能叫 "disk", "磁盘"
    disk_keywords = ("disk", "磁盘", " fs ", "filesystem", "vfs", "space", "pused", "/tmp", "/var")
    if any(k in text for k in disk_keywords):
        return "disk_cleanup"

    # 服务类
    svc_keywords = ("service", "进程", "process", " down ", "failed", "not running")
    if any(k in text for k in svc_keywords):
        return "service_restart"

    return None


@dataclass
class ApprovalDecision:
    """审批决策信号载荷"""

    approved: bool


@workflow.defn
class AlertWorkflow:
    """告警处理主工作流（Phase 3: ReAct agent 路线）

    主流程：
      agent_diagnose → 飞书卡片 → 等审批 → execute_runbook → 飞书结果
    Agent 失败时降级到关键词匹配 + alert.host_ip 的最小默认参数。
    """

    def __init__(self) -> None:
        self._approval_received = False
        self._approved = False

    @workflow.run
    async def run(self, alert_json: str) -> str:
        try:
            return await self._run_inner(alert_json)
        finally:
            await self._decr_pending_gauge()

    async def _run_inner(self, alert_json: str) -> str:
        alert = cast(dict[str, Any], json.loads(alert_json))
        event_id = alert["event_id"]
        workflow_id = workflow.info().workflow_id

        # 1. ReAct agent 诊断（一次 activity 内自带多轮 tool calling）
        agent_output_json = await self._safe_agent_call(alert_json)
        plan_dict, _trace = self._parse_agent_output(agent_output_json)

        # 2. 决定 runbook + 参数（提到等审批之前，因为 policy 评估需要它们）
        runbook_id, runbook_params = self._resolve_runbook(plan_dict, alert)

        # 3. 不支持的 runbook → 早返回（连 policy 都不用评估）
        if runbook_id is None:
            await workflow.execute_activity(
                "write_audit",
                args=[alert_json, workflow_id, "unsupported", None, None, None, None],
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=_NOTIFY_RETRY,
            )
            await workflow.execute_activity(
                "send_feishu_result",
                args=[f"Unsupported alert {event_id}: no matching runbook"],
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=_NOTIFY_RETRY,
            )
            return "unsupported"

        # 4. Policy 评估（Phase 3）
        plan_json_for_policy = json.dumps(plan_dict) if plan_dict else "null"
        policy_result_json = await workflow.execute_activity(
            "evaluate_policy_activity",
            args=[runbook_id, runbook_params, alert_json, plan_json_for_policy],
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=_NOTIFY_RETRY,
        )
        policy_result = json.loads(policy_result_json)
        policy_decision = policy_result.get("decision", "approval_required")

        # 5. DENY 分支：拒绝执行 + 通知 + 早返回
        if policy_decision == "deny":
            matched = policy_result.get("matched_policy", "unknown")
            reason = policy_result.get("reason", "")
            await workflow.execute_activity(
                "write_audit",
                args=[alert_json, workflow_id, "denied", runbook_id, runbook_params, None, None],
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=_NOTIFY_RETRY,
            )
            await workflow.execute_activity(
                "send_feishu_result",
                args=[f"🚫 告警 {event_id} 被 Policy 拒绝执行（规则 `{matched}`）：{reason}"],
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=_NOTIFY_RETRY,
            )
            return "denied"

        # 6. 推送飞书卡片
        if plan_dict is not None:
            feishu_msg_id = await workflow.execute_activity(
                "send_feishu_alert_with_agent",
                args=[alert_json, workflow_id, agent_output_json, policy_result_json],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_NOTIFY_RETRY,
            )
        else:
            feishu_msg_id = await workflow.execute_activity(
                "send_feishu_alert",
                args=[alert_json, workflow_id],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_NOTIFY_RETRY,
            )

        # 7. ALLOW + live 模式：跳过审批直接执行；其他场景走原审批流
        auto_execute = policy_decision == "allow" and settings.aiops_mode == "live"

        if not auto_execute:
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
                    retry_policy=_NOTIFY_RETRY,
                )
                await workflow.execute_activity(
                    "send_feishu_result",
                    args=[f"⏰ 告警 {event_id} 审批超时（30分钟），已跳过"],
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=_NOTIFY_RETRY,
                )
                return "timeout"

            if not self._approved:
                await workflow.execute_activity(
                    "write_audit",
                    args=[alert_json, workflow_id, "rejected", None, None, None, feishu_msg_id],
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=_NOTIFY_RETRY,
                )
                await workflow.execute_activity(
                    "send_feishu_result",
                    args=[f"❌ 告警 {event_id} 已被拒绝"],
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=_NOTIFY_RETRY,
                )
                return "rejected"

        # 8. 执行 Runbook：同 host 同时只允许一个修复动作，避免并发误操作
        mutex_target = f"host:{alert['host_ip']}"
        mutex_token = await workflow.execute_activity(
            "try_acquire_mutex",
            args=[mutex_target, 600],
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
        if not mutex_token:
            await workflow.execute_activity(
                "write_audit",
                args=[alert_json, workflow_id, "skipped_mutex", runbook_id, runbook_params, None, feishu_msg_id],
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=_NOTIFY_RETRY,
            )
            await workflow.execute_activity(
                "send_feishu_result",
                args=[f"⚠️ 告警 {event_id} 跳过自动执行：目标 {alert['host_ip']} 正被另一个告警处理"],
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=_NOTIFY_RETRY,
            )
            return "skipped_mutex"

        try:
            exec_result_json = await workflow.execute_activity(
                "execute_runbook",
                args=[runbook_id, runbook_params],
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
        finally:
            await workflow.execute_activity(
                "release_mutex",
                args=[mutex_target, mutex_token],
                start_to_close_timeout=timedelta(seconds=5),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )

        # 9. 写审计：自动执行用 auto_approved 标签和人工审批的 approved 区分开
        decision_label = "auto_approved" if auto_execute else "approved"
        await workflow.execute_activity(
            "write_audit",
            args=[alert_json, workflow_id, decision_label, runbook_id, runbook_params, exec_result_json, feishu_msg_id],
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=_NOTIFY_RETRY,
        )

        # 10. 飞书通知结果
        exec_result = json.loads(exec_result_json)
        prefix = "🤖 自动" if auto_execute else "✅"
        if exec_result.get("verify"):
            msg = f"{prefix} 告警 {event_id} 处理成功（Runbook: {runbook_id}）"
        elif not exec_result.get("dry_run", {}).get("success"):
            msg = f"⚠️ 告警 {event_id} Runbook 预检失败，未执行实际操作"
        elif not exec_result.get("execute", {}).get("success"):
            msg = f"⚠️ 告警 {event_id} Runbook 执行失败"
        else:
            msg = f"⚠️ 告警 {event_id} 执行完成但验证未通过，可能需要人工介入"

        await workflow.execute_activity(
            "send_feishu_result",
            args=[msg],
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=_NOTIFY_RETRY,
        )

        return decision_label

    # ---------- helpers ----------

    async def _decr_pending_gauge(self) -> None:
        """workflow 结束时清理 pending workflow 计数，失败不影响主流程。"""
        try:
            await workflow.execute_activity(
                "decr_pending_gauge",
                start_to_close_timeout=timedelta(seconds=5),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )
        except Exception:
            workflow.logger.warning("decr_pending_gauge failed")

    async def _safe_agent_call(self, alert_json: str) -> str:
        """调用 agent_diagnose，失败时返回标记 agent_failed=True 的占位 JSON"""
        try:
            return cast(str, await workflow.execute_activity(
                "agent_diagnose",
                args=[alert_json],
                start_to_close_timeout=timedelta(minutes=5),  # agent 多轮 + 远端 ansible 调用，给足时间
                retry_policy=RetryPolicy(maximum_attempts=2),
            ))
        except Exception:
            workflow.logger.warning("agent_diagnose failed, falling back to keyword-match runbook selection")
            return json.dumps({"plan": None, "trace": [], "agent_failed": True})

    def _parse_agent_output(self, agent_output_json: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        try:
            data = json.loads(agent_output_json)
            if not isinstance(data, dict):
                return None, []
            plan = data.get("plan")
            trace = data.get("trace") or []
            return (plan if isinstance(plan, dict) else None), (trace if isinstance(trace, list) else [])
        except Exception:
            return None, []

    def _resolve_runbook(
        self,
        plan_dict: dict[str, Any] | None,
        alert: dict[str, Any],
    ) -> tuple[str | None, str]:
        """根据 agent plan 决定 runbook_id + 序列化后的 params

        三层兜底：
          1. agent 给了合法 plan → 用它，但强制 target_host = alert.host_ip
          2. agent 失败 → 关键词匹配 runbook，构造最小默认 params
          3. 关键词也匹配不上 → 返回 (None, ...)
        """
        if plan_dict and plan_dict.get("runbook_id") in _KNOWN_RUNBOOKS:
            runbook_id = str(plan_dict["runbook_id"])
            params_from_plan = plan_dict.get("params")
            raw_params = dict(params_from_plan) if isinstance(params_from_plan, dict) else {}
            # target_host 永远以 alert 真实 IP 为准，不信 LLM
            raw_params["target_host"] = alert["host_ip"]
            return runbook_id, json.dumps(raw_params)

        # 降级路径
        fallback_runbook_id = _select_runbook(alert)
        if fallback_runbook_id is None:
            return None, json.dumps({})

        # 提供最小默认参数让 runbook 能跑（path / service_name 走 schema 默认值或抽取）
        fallback_params: dict[str, Any] = {"target_host": alert["host_ip"]}
        if fallback_runbook_id == "service_restart":
            name_lower = alert.get("event_name", "").lower()
            for svc in ("nginx", "redis-server", "redis", "mysql", "postgresql",
                        "postgres", "apache2", "docker", "ssh", "sshd"):
                if svc in name_lower:
                    fallback_params["service_name"] = svc
                    break
        return fallback_runbook_id, json.dumps(fallback_params)

    @workflow.signal
    def approve(self, decision: ApprovalDecision) -> None:
        """接收飞书审批回调信号"""
        # 只接受第一次决策，避免重复点击改主意
        if self._approval_received:
            return
        self._approval_received = True
        self._approved = decision.approved
