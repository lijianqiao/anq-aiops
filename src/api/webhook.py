import json
import logging

from fastapi import APIRouter, Request

from src.bus.producer import produce_alert
from src.models import Alert

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/webhook/zabbix")
async def zabbix_webhook(alert: Alert, request: Request):
    redis = request.app.state.redis
    msg_id = await produce_alert(redis, alert)

    if msg_id is None:
        logger.info(f"Duplicate alert: {alert.event_id}")
        return {"status": "duplicate", "event_id": alert.event_id}

    logger.info(f"Alert received: {alert.event_id} -> stream {msg_id}")
    return {"status": "accepted", "event_id": alert.event_id, "stream_id": msg_id}


@router.post("/webhook/feishu")
async def feishu_webhook(request: Request):
    body = await request.json()

    action_value = body.get("action", {}).get("value", "{}")
    callback = json.loads(action_value)

    workflow_id = callback.get("workflow_id")
    action = callback.get("action")
    approved = action == "approve"

    if not workflow_id:
        return {"status": "error", "message": "missing workflow_id"}

    temporal = request.app.state.temporal
    handle = temporal.get_workflow_handle(workflow_id)

    from src.workflows.alert_workflow import AlertWorkflow, ApprovalDecision

    await handle.signal(AlertWorkflow.approve, ApprovalDecision(approved=approved))

    logger.info(f"Approval signal sent: workflow={workflow_id}, approved={approved}")
    return {"status": "ok"}
