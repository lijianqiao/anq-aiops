import json
from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

# 白名单与 src/runbooks/__init__.py 的 RUNBOOK_REGISTRY 保持同步。
# 不直接 import RUNBOOK_REGISTRY 是为了让 workflow 模块保持轻量、可被 sandbox 检查。
_KNOWN_RUNBOOKS = frozenset({"disk_cleanup", "service_restart"})

# 通知/审计类活动的统一重试策略，避免飞书暂时 5xx 让 workflow 永远卡住
_NOTIFY_RETRY = RetryPolicy(maximum_attempts=5)


def _select_runbook(alert: dict) -> str | None:
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
        alert = json.loads(alert_json)
        event_id = alert["event_id"]
        workflow_id = workflow.info().workflow_id

        # 1. ReAct agent 诊断（一次 activity 内自带多轮 tool calling）
        agent_output_json = await self._safe_agent_call(alert_json)
        plan_dict, trace = self._parse_agent_output(agent_output_json)

        # 2. 推送飞书卡片
        if plan_dict is not None:
            feishu_msg_id = await workflow.execute_activity(
                "send_feishu_alert_with_agent",
                args=[alert_json, workflow_id, agent_output_json],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_NOTIFY_RETRY,
            )
        else:
            # Agent 失败 / 选了 none：发普通卡片
            feishu_msg_id = await workflow.execute_activity(
                "send_feishu_alert",
                args=[alert_json, workflow_id],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_NOTIFY_RETRY,
            )

        # 3. 等审批信号（30 分钟超时）
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

        # 4. 决定 runbook + 参数
        runbook_id, runbook_params = self._resolve_runbook(plan_dict, alert)

        if runbook_id is None:
            await workflow.execute_activity(
                "write_audit",
                args=[alert_json, workflow_id, "unsupported", None, None, None, feishu_msg_id],
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

        # 5. 执行 Runbook
        exec_result_json = await workflow.execute_activity(
            "execute_runbook",
            args=[runbook_id, runbook_params],
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )

        # 6. 写审计
        await workflow.execute_activity(
            "write_audit",
            args=[alert_json, workflow_id, "approved", runbook_id, runbook_params, exec_result_json, feishu_msg_id],
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=_NOTIFY_RETRY,
        )

        # 7. 飞书通知结果
        exec_result = json.loads(exec_result_json)
        if exec_result.get("verify"):
            msg = f"✅ 告警 {event_id} 处理成功（Runbook: {runbook_id}）"
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

        return "approved"

    # ---------- helpers ----------

    async def _safe_agent_call(self, alert_json: str) -> str:
        """调用 agent_diagnose，失败时返回标记 agent_failed=True 的占位 JSON"""
        try:
            return await workflow.execute_activity(
                "agent_diagnose",
                args=[alert_json],
                start_to_close_timeout=timedelta(minutes=5),  # agent 多轮 + 远端 ansible 调用，给足时间
                retry_policy=RetryPolicy(maximum_attempts=2),
            )
        except Exception:
            workflow.logger.warning("agent_diagnose failed, falling back to keyword-match runbook selection")
            return json.dumps({"plan": None, "trace": [], "agent_failed": True})

    def _parse_agent_output(self, agent_output_json: str) -> tuple[dict | None, list]:
        try:
            data = json.loads(agent_output_json)
            return data.get("plan"), data.get("trace") or []
        except Exception:
            return None, []

    def _resolve_runbook(self, plan_dict: dict | None, alert: dict) -> tuple[str | None, str]:
        """根据 agent plan 决定 runbook_id + 序列化后的 params

        三层兜底：
          1. agent 给了合法 plan → 用它，但强制 target_host = alert.host_ip
          2. agent 失败 → 关键词匹配 runbook，构造最小默认 params
          3. 关键词也匹配不上 → 返回 (None, ...)
        """
        if plan_dict and plan_dict.get("runbook_id") in _KNOWN_RUNBOOKS:
            runbook_id = plan_dict["runbook_id"]
            raw_params = plan_dict.get("params") if isinstance(plan_dict.get("params"), dict) else {}
            raw_params = dict(raw_params)
            # target_host 永远以 alert 真实 IP 为准，不信 LLM
            raw_params["target_host"] = alert["host_ip"]
            return runbook_id, json.dumps(raw_params)

        # 降级路径
        runbook_id = _select_runbook(alert)
        if runbook_id is None:
            return None, json.dumps({})

        # 提供最小默认参数让 runbook 能跑（path / service_name 走 schema 默认值或抽取）
        raw_params: dict = {"target_host": alert["host_ip"]}
        if runbook_id == "service_restart":
            name_lower = alert.get("event_name", "").lower()
            for svc in ("nginx", "redis-server", "redis", "mysql", "postgresql",
                        "postgres", "apache2", "docker", "ssh", "sshd"):
                if svc in name_lower:
                    raw_params["service_name"] = svc
                    break
        return runbook_id, json.dumps(raw_params)

    @workflow.signal
    def approve(self, decision: ApprovalDecision) -> None:
        """接收飞书审批回调信号"""
        # 只接受第一次决策，避免重复点击改主意
        if self._approval_received:
            return
        self._approval_received = True
        self._approved = decision.approved
