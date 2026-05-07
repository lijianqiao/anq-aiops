import hmac
import logging

from fastapi import APIRouter, HTTPException, Request

from src.bus.producer import produce_alert
from src.config import settings
from src.models import Alert

logger = logging.getLogger(__name__)
router = APIRouter()


def _require_zabbix_auth(request: Request) -> None:
    if not settings.zabbix_webhook_token:
        raise HTTPException(status_code=503, detail="zabbix webhook token is not configured")

    auth = request.headers.get("authorization", "")
    bearer = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
    token = request.headers.get("x-zabbix-token") or bearer
    if not hmac.compare_digest(token, settings.zabbix_webhook_token):
        raise HTTPException(status_code=401, detail="invalid zabbix webhook token")


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


# 飞书卡片回调走 src/feishu_listener.py 的长连接（无需公网 webhook）
