import asyncio
import json
import time

import httpx
from temporalio import activity

from src.config import settings
from src.models import Alert, RCAResult, RiskEvaluation

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


def build_feishu_card(alert: Alert, workflow_id: str) -> dict:
    """构造飞书 Interactive Card（IM v1 用，返回 card 本体，不含外层 msg_type 包装）"""
    emoji = _SEVERITY_EMOJI.get(alert.severity, "⚪")
    return {
        "header": {
            "title": {"tag": "plain_text", "content": f"{emoji} AIOps 告警通知"},
            "template": "red" if alert.severity in ("disaster", "high") else "orange",
        },
        "elements": [
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
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**时间：**{alert.timestamp}"}},
            {"tag": "hr"},
            {
                "tag": "action",
                "actions": [
                    _action_button("批准执行", "primary", workflow_id, "approve", alert.event_id),
                    _action_button("拒绝", "danger", workflow_id, "reject", alert.event_id),
                ],
            },
        ],
    }


def build_feishu_card_with_ai(alert: Alert, workflow_id: str, rca: RCAResult, risk: RiskEvaluation) -> dict:
    """构造带 AI 分析区块的飞书卡片（同样返回 card 本体）"""
    emoji = _SEVERITY_EMOJI.get(alert.severity, "⚪")
    confidence_pct = f"{int(rca.confidence * 100)}%"
    risk_label = "🟢 低风险" if risk.risk_score < 0.4 else "🟡 中风险" if risk.risk_score < 0.7 else "🔴 高风险"

    ai_section = {
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": (
                f"**🤖 AI 分析：**\n"
                f"根因：{rca.root_cause}\n"
                f"置信度：{confidence_pct}\n"
                f"建议 Runbook：`{rca.recommended_runbook}`\n"
                f"参数：`{rca.params}`\n"
                f"风险：{risk_label}"
            ),
        },
    }

    actions = [
        _action_button("按建议执行", "primary", workflow_id, "approve", alert.event_id),
        _action_button("拒绝", "danger", workflow_id, "reject", alert.event_id),
    ]
    if risk.risk_score >= 0.7:
        actions.insert(1, _action_button("⚠️ 高风险 - 人工处理", "default", workflow_id, "reject", alert.event_id))

    return {
        "header": {
            "title": {"tag": "plain_text", "content": f"{emoji} AIOps 告警通知"},
            "template": "red" if alert.severity in ("disaster", "high") else "orange",
        },
        "elements": [
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
            {"tag": "hr"},
            ai_section,
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
    """推送告警卡片到飞书，返回 message_id"""
    alert = Alert.model_validate_json(alert_json)
    card = build_feishu_card(alert, workflow_id)
    return await _post_im_message(msg_type="interactive", content=card)


@activity.defn
async def send_feishu_alert_with_ai(alert_json: str, workflow_id: str, rca_json: str, risk_json: str) -> str:
    """推送带 AI 分析的告警卡片到飞书，返回 message_id"""
    alert = Alert.model_validate_json(alert_json)
    rca = RCAResult.model_validate_json(rca_json)
    risk = RiskEvaluation.model_validate_json(risk_json)
    card = build_feishu_card_with_ai(alert, workflow_id, rca, risk)
    return await _post_im_message(msg_type="interactive", content=card)


@activity.defn
async def send_feishu_result(message: str) -> None:
    """推送执行结果到飞书"""
    await _post_im_message(msg_type="text", content={"text": message})
