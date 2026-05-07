import hashlib
import hmac
import json
import logging
import time

from fastapi import APIRouter, HTTPException, Request

from src.bus.producer import produce_alert
from src.config import settings
from src.models import Alert

logger = logging.getLogger(__name__)
router = APIRouter()
FEISHU_SIGNATURE_MAX_AGE_SEC = 300


def _require_zabbix_auth(request: Request) -> None:
    if not settings.zabbix_webhook_token:
        raise HTTPException(status_code=503, detail="zabbix webhook token is not configured")

    auth = request.headers.get("authorization", "")
    bearer = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
    token = request.headers.get("x-zabbix-token") or bearer
    if not hmac.compare_digest(token, settings.zabbix_webhook_token):
        raise HTTPException(status_code=401, detail="invalid zabbix webhook token")


def _require_feishu_signature(request: Request, body: bytes) -> None:
    if not settings.feishu_webhook_secret:
        raise HTTPException(status_code=503, detail="feishu webhook secret is not configured")

    timestamp = request.headers.get("x-lark-request-timestamp")
    nonce = request.headers.get("x-lark-request-nonce")
    signature = request.headers.get("x-lark-signature")
    if not timestamp or not nonce or not signature:
        raise HTTPException(status_code=401, detail="missing feishu signature headers")

    try:
        request_ts = int(timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="invalid feishu timestamp") from exc
    if abs(time.time() - request_ts) > FEISHU_SIGNATURE_MAX_AGE_SEC:
        raise HTTPException(status_code=401, detail="expired feishu timestamp")

    expected = hashlib.sha256(
        f"{timestamp}{nonce}{settings.feishu_webhook_secret}".encode() + body,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="invalid feishu signature")


@router.post("/webhook/zabbix")
async def zabbix_webhook(alert: Alert, request: Request):
    _require_zabbix_auth(request)
    redis = request.app.state.redis
    msg_id = await produce_alert(redis, alert)

    if msg_id is None:
        logger.info(f"Duplicate alert: {alert.event_id}")
        return {"status": "duplicate", "event_id": alert.event_id}

    logger.info(f"Alert received: {alert.event_id} -> stream {msg_id}")
    return {"status": "accepted", "event_id": alert.event_id, "stream_id": msg_id}


@router.post("/webhook/feishu")
async def feishu_webhook(request: Request):
    raw_body = await request.body()
    _require_feishu_signature(request, raw_body)
    body = json.loads(raw_body)

    action_value = body.get("action", {}).get("value", "{}")
    callback = json.loads(action_value)

    workflow_id = callback.get("workflow_id")
    action = callback.get("action")
    if action not in {"approve", "reject"}:
        return {"status": "error", "message": "invalid action"}
    approved = action == "approve"

    if not workflow_id:
        return {"status": "error", "message": "missing workflow_id"}

    temporal = request.app.state.temporal
    handle = temporal.get_workflow_handle(workflow_id)

    from src.workflows.alert_workflow import AlertWorkflow, ApprovalDecision

    await handle.signal(AlertWorkflow.approve, ApprovalDecision(approved=approved))

    logger.info(f"Approval signal sent: workflow={workflow_id}, approved={approved}")
    return {"status": "ok"}
