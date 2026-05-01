import json

import httpx
from temporalio import activity

from src.config import settings
from src.models import Alert, RCAResult, RiskEvaluation


def build_feishu_card(alert: Alert, workflow_id: str) -> dict:
    """构造飞书 Interactive Card"""
    severity_emoji = {
        "disaster": "🔴",
        "high": "🟠",
        "average": "🟡",
        "warning": "🔵",
        "info": "⚪",
    }
    emoji = severity_emoji.get(alert.severity, "⚪")
    return {
        "msg_type": "interactive",
        "card": {
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
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "批准执行"},
                            "type": "primary",
                            "value": json.dumps({"workflow_id": workflow_id, "action": "approve", "alert_id": alert.event_id}),
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "拒绝"},
                            "type": "danger",
                            "value": json.dumps({"workflow_id": workflow_id, "action": "reject", "alert_id": alert.event_id}),
                        },
                    ],
                },
            ],
        },
    }


@activity.defn
async def send_feishu_alert(alert_json: str, workflow_id: str) -> str:
    """推送告警卡片到飞书，返回 message_id"""
    alert = Alert.model_validate_json(alert_json)
    card = build_feishu_card(alert, workflow_id)
    async with httpx.AsyncClient() as client:
        resp = await client.post(settings.feishu_webhook_url, json=card, timeout=10)
        resp.raise_for_status()
        result = resp.json()
    if result.get("StatusCode", -1) != 0:
        raise RuntimeError(f"Feishu API error: {result}")
    return result.get("msg_id", "")


@activity.defn
async def send_feishu_result(message: str) -> None:
    """推送执行结果到飞书"""
    payload = {"msg_type": "text", "content": {"text": message}}
    async with httpx.AsyncClient() as client:
        resp = await client.post(settings.feishu_webhook_url, json=payload, timeout=10)
        resp.raise_for_status()


def build_feishu_card_with_ai(alert: Alert, workflow_id: str, rca: RCAResult, risk: RiskEvaluation) -> dict:
    """构造带 AI 分析区块的飞书卡片"""
    severity_emoji = {
        "disaster": "🔴",
        "high": "🟠",
        "average": "🟡",
        "warning": "🔵",
        "info": "⚪",
    }
    emoji = severity_emoji.get(alert.severity, "⚪")

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
        {
            "tag": "button",
            "text": {"tag": "plain_text", "content": "按建议执行"},
            "type": "primary",
            "value": json.dumps({"workflow_id": workflow_id, "action": "approve", "alert_id": alert.event_id}),
        },
        {
            "tag": "button",
            "text": {"tag": "plain_text", "content": "拒绝"},
            "type": "danger",
            "value": json.dumps({"workflow_id": workflow_id, "action": "reject", "alert_id": alert.event_id}),
        },
    ]

    if risk.risk_score >= 0.7:
        actions.insert(1, {
            "tag": "button",
            "text": {"tag": "plain_text", "content": "⚠️ 高风险 - 人工处理"},
            "type": "default",
            "value": json.dumps({"workflow_id": workflow_id, "action": "reject", "alert_id": alert.event_id}),
        })

    return {
        "msg_type": "interactive",
        "card": {
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
        },
    }


@activity.defn
async def send_feishu_alert_with_ai(alert_json: str, workflow_id: str, rca_json: str, risk_json: str) -> str:
    """推送带 AI 分析的告警卡片到飞书，返回 message_id"""
    alert = Alert.model_validate_json(alert_json)
    rca = RCAResult.model_validate_json(rca_json)
    risk = RiskEvaluation.model_validate_json(risk_json)
    card = build_feishu_card_with_ai(alert, workflow_id, rca, risk)
    async with httpx.AsyncClient() as client:
        resp = await client.post(settings.feishu_webhook_url, json=card, timeout=10)
        resp.raise_for_status()
        result = resp.json()
    if result.get("StatusCode", -1) != 0:
        raise RuntimeError(f"Feishu API error: {result}")
    return result.get("msg_id", "")
