import asyncio
import json
import time

import httpx
from temporalio import activity

from src.config import settings
from src.models import Alert

_FEISHU_BASE = "https://open.feishu.cn/open-apis"


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


def _action_button(text: str, btn_type: str, workflow_id: str, action: str, alert_id: str) -> dict:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": text},
        "type": btn_type,
        "value": {"workflow_id": workflow_id, "action": action, "alert_id": alert_id},
    }


def _alert_header_elements(alert: Alert) -> list[dict]:
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


def build_feishu_card(alert: Alert, workflow_id: str) -> dict:
    """无 AI 分析时的简卡（agent 失败 / 选 none 时用）"""
    emoji = _SEVERITY_EMOJI.get(alert.severity, "⚪")
    elements = _alert_header_elements(alert)
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**时间：**{alert.timestamp}"}})
    elements.append({"tag": "hr"})
    elements.append({
        "tag": "action",
        "actions": [
            _action_button("批准执行", "primary", workflow_id, "approve", alert.event_id),
            _action_button("拒绝", "danger", workflow_id, "reject", alert.event_id),
        ],
    })
    return {
        "header": {
            "title": {"tag": "plain_text", "content": f"{emoji} AIOps 告警通知"},
            "template": "red" if alert.severity in ("disaster", "high") else "orange",
        },
        "elements": elements,
    }


def _format_trace(trace: list[dict], max_entries: int = 8) -> str:
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


def build_feishu_card_with_agent(alert: Alert, workflow_id: str, plan: dict, trace: list[dict]) -> dict:
    """带 agent 诊断结果的卡片：展示推理 + 工具轨迹 + 计划"""
    emoji = _SEVERITY_EMOJI.get(alert.severity, "⚪")

    confidence = float(plan.get("confidence") or 0)
    confidence_pct = f"{int(confidence * 100)}%"
    risk_level = plan.get("risk_level", "medium")
    risk_label = {"low": "🟢 低风险", "medium": "🟡 中风险", "high": "🔴 高风险"}.get(risk_level, "🟡 中风险")

    runbook_id = plan.get("runbook_id", "")
    params = plan.get("params") or {}
    reasoning = plan.get("reasoning", "")

    ai_section = {
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": (
                f"**🤖 AI 诊断：**\n"
                f"结论：{reasoning}\n"
                f"置信度：{confidence_pct} | 风险：{risk_label}\n"
                f"建议 Runbook：`{runbook_id}`\n"
                f"参数：`{params}`"
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

    actions = [
        _action_button("按建议执行", "primary", workflow_id, "approve", alert.event_id),
        _action_button("拒绝", "danger", workflow_id, "reject", alert.event_id),
    ]
    if risk_level == "high":
        actions.insert(1, _action_button("⚠️ 高风险 - 人工处理", "default", workflow_id, "reject", alert.event_id))

    return {
        "header": {
            "title": {"tag": "plain_text", "content": f"{emoji} AIOps 告警通知"},
            "template": "red" if alert.severity in ("disaster", "high") else "orange",
        },
        "elements": [
            *_alert_header_elements(alert),
            {"tag": "hr"},
            ai_section,
            {"tag": "hr"},
            trace_section,
            {"tag": "hr"},
            {"tag": "action", "actions": actions},
        ],
    }


async def _post_im_message(*, msg_type: str, content: dict) -> str:
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
        raise RuntimeError(
            f"Feishu non-JSON response (HTTP {resp.status_code}): {resp.text[:500]}"
        ) from exc
    if data.get("code") != 0:
        raise RuntimeError(
            f"Feishu send error: HTTP {resp.status_code} code={data.get('code')} "
            f"msg={data.get('msg')!r} payload_receive_id={settings.feishu_receive_id} "
            f"payload_receive_id_type={settings.feishu_receive_id_type}"
        )
    return data["data"]["message_id"]


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
    """推送带 ReAct agent 诊断结果的卡片

    policy_result_json: 占位参数，Task 9 会真正用它在卡片上加 policy 决策标签
    """
    _ = policy_result_json  # Task 9 实装；现在保留参数兼容 workflow 调用
    alert = Alert.model_validate_json(alert_json)
    agent_output = json.loads(agent_output_json)
    plan = agent_output.get("plan") or {}
    trace = agent_output.get("trace") or []
    card = build_feishu_card_with_agent(alert, workflow_id, plan, trace)
    return await _post_im_message(msg_type="interactive", content=card)


@activity.defn
async def send_feishu_result(message: str) -> None:
    """推送执行结果到飞书"""
    await _post_im_message(msg_type="text", content={"text": message})
