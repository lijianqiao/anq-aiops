"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: feishu.py
@DateTime: 2026-05-08 14:33:00
@Docs: 构建飞书告警卡片并通过飞书 IM 接口发送告警与处置结果
"""

import asyncio
import json
import time
from typing import Any, cast

import httpx
from temporalio import activity

from src.config import settings
from src.models import Alert

_FEISHU_BASE = "https://open.feishu.cn/open-apis"
FeishuCard = dict[str, Any]


class _TokenManager:
    """缓存 tenant_access_token，提前 5 分钟续期"""

    def __init__(self) -> None:
        self._token: str = ""
        self._expire_at: float = 0.0
        self._lock = asyncio.Lock()

    async def get(self) -> str:
        async with self._lock:
            now = time.time()
            if self._token and now < self._expire_at - 300:
                return self._token
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{_FEISHU_BASE}/auth/v3/tenant_access_token/internal",
                    json={"app_id": settings.feishu_app_id, "app_secret": settings.feishu_app_secret},
                )
                resp.raise_for_status()
                data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"Feishu token error: {data}")
            self._token = data["tenant_access_token"]
            self._expire_at = now + int(data.get("expire", 7200))
            return self._token


_token_manager = _TokenManager()


_SEVERITY_EMOJI = {
    "disaster": "🔴",
    "high": "🟠",
    "average": "🟡",
    "warning": "🔵",
    "info": "⚪",
}


def _action_button(text: str, btn_type: str, workflow_id: str, action: str, alert_id: str) -> FeishuCard:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": text},
        "type": btn_type,
        "value": {"workflow_id": workflow_id, "action": action, "alert_id": alert_id},
    }


def _reject_reason_input() -> FeishuCard:
    """拒绝原因输入框，提交拒绝时由飞书回调带回 form_value。"""
    return {
        "tag": "input",
        "name": "reason",
        "placeholder": {"tag": "plain_text", "content": "拒绝原因（必填，写入 Hermes 反馈库）"},
        "max_length": 200,
    }


def _reject_with_reason_button(workflow_id: str, alert_id: str, button_text: str = "提交拒绝") -> FeishuCard:
    """提交拒绝按钮；拒绝原因由同卡片 input 的 form_value 提供。"""
    return _action_button(button_text, "danger", workflow_id, "reject_with_reason", alert_id)


def _alert_header_elements(alert: Alert) -> list[FeishuCard]:
    """卡片顶部告警事实区"""
    return [
        {
            "tag": "div",
            "fields": [
                {"is_short": True, "text": {"tag": "lark_md", "content": f"**设备：**{alert.hostname}"}},
                {"is_short": True, "text": {"tag": "lark_md", "content": f"**IP：**{alert.host_ip}"}},
                {"is_short": True, "text": {"tag": "lark_md", "content": f"**级别：**{alert.severity}"}},
                {"is_short": True, "text": {"tag": "lark_md", "content": f"**状态：**{alert.status}"}},
            ],
        },
        {"tag": "div", "text": {"tag": "lark_md", "content": f"**告警：**{alert.event_name}"}},
        {"tag": "div", "text": {"tag": "lark_md", "content": f"**详情：**{alert.message}"}},
    ]


def build_feishu_card(alert: Alert, workflow_id: str) -> FeishuCard:
    """无 AI 分析时的简卡（agent 失败 / 选 none 时用）"""
    emoji = _SEVERITY_EMOJI.get(alert.severity, "⚪")
    elements = _alert_header_elements(alert)
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**时间：**{alert.timestamp}"}})
    elements.append({"tag": "hr"})
    elements.append(_reject_reason_input())
    elements.append(
        {
            "tag": "action",
            "actions": [
                _action_button("批准执行", "primary", workflow_id, "approve", alert.event_id),
                _reject_with_reason_button(workflow_id, alert.event_id),
            ],
        }
    )
    return {
        "header": {
            "title": {"tag": "plain_text", "content": f"{emoji} AIOps 告警通知"},
            "template": "red" if alert.severity in ("disaster", "high") else "orange",
        },
        "elements": elements,
    }


def _format_trace(trace: list[dict[str, Any]], max_entries: int = 8) -> str:
    """把 agent trace 渲染成简短的 markdown 列表"""
    if not trace:
        return "_（无诊断步骤）_"
    lines = []
    for step in trace[:max_entries]:
        tool = step.get("tool", "?")
        if tool == "propose_action":
            continue  # 终止工具不展示
        args = step.get("args") or {}
        # 摘要 args，避免太长
        args_str = ", ".join(f"{k}={v}" for k, v in args.items() if k != "host")
        if not args_str:
            args_str = "—"
        preview = (step.get("result_preview") or "").replace("\n", " ")[:80]
        lines.append(f"• `{tool}({args_str})` → {preview}")
    if not lines:
        return "_（直接给出结论，无诊断步骤）_"
    return "\n".join(lines)


def _format_policy_label(decision: str, mode: str, policy: dict[str, Any]) -> str:
    """渲染 policy 决策标签到飞书卡片"""
    rule = policy.get("matched_policy", "default")
    reason = policy.get("reason", "")

    if decision == "deny":
        return f"🚫 拒绝执行（规则 `{rule}`）：{reason}"
    if decision == "allow":
        if mode == "live":
            return f"🤖 自动执行（规则 `{rule}`，无需审批）"
        # shadow
        return f"🌓 Shadow 模式：本应自动执行（规则 `{rule}`）但仍走人工审批"
    # approval_required 或其它默认
    return f"👤 需要人工审批（规则 `{rule}`）：{reason or '默认走审批'}"


def build_feishu_card_with_agent(
    alert: Alert,
    workflow_id: str,
    plan: dict[str, Any],
    trace: list[dict[str, Any]],
    policy: dict[str, Any] | None = None,
) -> FeishuCard:
    """带 agent 诊断结果 + Policy 决策标签的卡片"""
    emoji = _SEVERITY_EMOJI.get(alert.severity, "⚪")

    confidence = float(plan.get("confidence") or 0)
    confidence_pct = f"{int(confidence * 100)}%"
    risk_level = plan.get("risk_level", "medium")
    risk_label = {"low": "🟢 低风险", "medium": "🟡 中风险", "high": "🔴 高风险"}.get(risk_level, "🟡 中风险")

    runbook_id = plan.get("runbook_id", "")
    params = plan.get("params") or {}
    reasoning = plan.get("reasoning", "")

    policy = policy or {}
    decision = policy.get("decision", "approval_required")
    policy_label = _format_policy_label(decision, settings.aiops_mode, policy)

    ai_section = {
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": (
                f"**🤖 AI 诊断：**\n"
                f"结论：{reasoning}\n"
                f"置信度：{confidence_pct} | 风险：{risk_label}\n"
                f"建议 Runbook：`{runbook_id}`\n"
                f"参数：`{params}`\n"
                f"**Policy：**{policy_label}"
            ),
        },
    }

    trace_section = {
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": f"**🔍 诊断步骤：**\n{_format_trace(trace)}",
        },
    }

    # 按钮策略：DENY 不展示按钮（执行被拒）；ALLOW + live 不展示（自动执行）；
    # 其它（APPROVAL_REQUIRED / ALLOW + shadow）展示批准/拒绝按钮
    actions: list[FeishuCard] = []
    if decision != "deny" and not (decision == "allow" and settings.aiops_mode == "live"):
        actions = [
            _action_button("按建议执行", "primary", workflow_id, "approve", alert.event_id),
            _reject_with_reason_button(workflow_id, alert.event_id),
        ]
        if risk_level == "high":
            actions.insert(
                1,
                _reject_with_reason_button(workflow_id, alert.event_id, "⚠️ 高风险 - 人工处理"),
            )

    elements: list[FeishuCard] = [
        *_alert_header_elements(alert),
        {"tag": "hr"},
        ai_section,
        {"tag": "hr"},
        trace_section,
    ]
    if actions:
        elements += [{"tag": "hr"}, _reject_reason_input(), {"tag": "action", "actions": actions}]

    return {
        "header": {
            "title": {"tag": "plain_text", "content": f"{emoji} AIOps 告警通知"},
            "template": "red" if alert.severity in ("disaster", "high") else "orange",
        },
        "elements": elements,
    }


async def _post_im_message(*, msg_type: str, content: dict[str, Any]) -> str:
    """通过 IM v1 接口发消息，返回 message_id"""
    if not settings.feishu_receive_id:
        raise RuntimeError("FEISHU_RECEIVE_ID is not configured")

    token = await _token_manager.get()
    payload = {
        "receive_id": settings.feishu_receive_id,
        "msg_type": msg_type,
        # IM v1 要求 content 是 JSON 字符串
        "content": json.dumps(content, ensure_ascii=False),
    }
    url = f"{_FEISHU_BASE}/im/v1/messages?receive_id_type={settings.feishu_receive_id_type}"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json=payload,
        )
    # 不直接 raise_for_status：飞书 4xx 时 body 里有 code/msg，比 HTTP 状态码更有诊断价值
    try:
        data = resp.json()
    except ValueError as exc:
        raise RuntimeError(f"Feishu non-JSON response (HTTP {resp.status_code}): {resp.text[:500]}") from exc
    if data.get("code") != 0:
        raise RuntimeError(
            f"Feishu send error: HTTP {resp.status_code} code={data.get('code')} "
            f"msg={data.get('msg')!r} payload_receive_id={settings.feishu_receive_id} "
            f"payload_receive_id_type={settings.feishu_receive_id_type}"
        )
    return cast(str, data["data"]["message_id"])


@activity.defn
async def send_feishu_alert(alert_json: str, workflow_id: str) -> str:
    """推送告警简卡（不含 AI 分析），返回 message_id"""
    alert = Alert.model_validate_json(alert_json)
    card = build_feishu_card(alert, workflow_id)
    return await _post_im_message(msg_type="interactive", content=card)


@activity.defn
async def send_feishu_alert_with_agent(
    alert_json: str,
    workflow_id: str,
    agent_output_json: str,
    policy_result_json: str = "{}",
) -> str:
    """推送带 ReAct agent 诊断结果 + Policy 决策标签的卡片"""
    alert = Alert.model_validate_json(alert_json)
    agent_output = json.loads(agent_output_json)
    if not isinstance(agent_output, dict):
        agent_output = {}
    raw_plan = agent_output.get("plan") or {}
    raw_trace = agent_output.get("trace") or []
    plan = raw_plan if isinstance(raw_plan, dict) else {}
    trace = raw_trace if isinstance(raw_trace, list) else []
    try:
        policy = json.loads(policy_result_json) if policy_result_json else {}
    except json.JSONDecodeError:
        policy = {}
    if not isinstance(policy, dict):
        policy = {}
    card = build_feishu_card_with_agent(alert, workflow_id, plan, trace, policy)
    return await _post_im_message(msg_type="interactive", content=card)


@activity.defn
async def send_feishu_result(message: str) -> None:
    """推送执行结果到飞书"""
    await _post_im_message(msg_type="text", content={"text": message})
