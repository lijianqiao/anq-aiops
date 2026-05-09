"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: feishu_listener.py
@DateTime: 2026-05-09 11:26:00
@Docs: 飞书卡片回调长连接监听器
"""

import asyncio
import concurrent.futures
import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from src.config import settings

if TYPE_CHECKING:
    from temporalio.client import Client as TemporalClient

logger = logging.getLogger(__name__)
_SIGNAL_TIMEOUT_SECONDS = 10


async def _signal_workflow(
    temporal_client: TemporalClient,
    workflow_id: str,
    approved: bool,
    reason: str = "",
) -> None:
    """向 Temporal Workflow 发送人工审批信号。"""
    from src.workflows.alert_workflow import AlertWorkflow, ApprovalDecision

    handle = temporal_client.get_workflow_handle(workflow_id)
    await handle.signal(AlertWorkflow.approve, ApprovalDecision(approved=approved, reason=reason))


def _build_card_action_handler(
    temporal_client: TemporalClient,
    main_loop: asyncio.AbstractEventLoop,
    response_cls: Callable[[dict[str, Any]], Any],
) -> Callable[[Any], Any]:
    """构建飞书卡片回调处理器。"""

    def _on_card_action(data: Any) -> Any:
        try:
            event = data.event
            value = (event.action.value if event and event.action else None) or {}
            workflow_id = value.get("workflow_id")
            decision = value.get("action")

            if decision not in {"approve", "reject", "reject_with_reason"} or not workflow_id:
                return response_cls({"toast": {"type": "error", "content": "回调参数不完整"}})

            reason = ""
            if decision == "reject_with_reason":
                form_value = getattr(event.action, "form_value", {}) if event and event.action else {}
                reason = str((form_value or {}).get("reason", "")).strip()
                if not reason:
                    return response_cls({"toast": {"type": "error", "content": "请填写拒绝原因"}})

            # 跨线程调度到主 loop 执行 Temporal signal，并等待结果后再向用户确认。
            future = asyncio.run_coroutine_threadsafe(
                _signal_workflow(temporal_client, workflow_id, decision == "approve", reason),
                main_loop,
            )
            try:
                future.result(timeout=_SIGNAL_TIMEOUT_SECONDS)
            except concurrent.futures.TimeoutError:
                future.cancel()
                logger.exception(f"Feishu 卡片回调等待 Temporal signal 超时: workflow={workflow_id}")
                return response_cls({"toast": {"type": "error", "content": "处理超时，请稍后确认审批结果"}})

            operator = event.operator if event else None
            operator_id = (operator.open_id or operator.user_id) if operator else None
            logger.info(
                f"Approval signal sent: workflow={workflow_id}, "
                f"approved={decision == 'approve'}, operator={operator_id}"
            )
            toast = "拒绝原因已记录，处理中" if decision == "reject_with_reason" else "已收到，处理中"
            return response_cls({"toast": {"type": "success", "content": toast}})
        except Exception as e:
            logger.exception(f"Feishu 卡片回调处理失败: {e}")
            return response_cls({"toast": {"type": "error", "content": f"处理失败: {e}"}})

    return _on_card_action


def start_in_thread(temporal_client: TemporalClient) -> threading.Thread:
    """启动飞书长连接监听，返回守护线程句柄"""
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        raise RuntimeError("FEISHU_APP_ID / FEISHU_APP_SECRET 未配置，无法启动飞书监听")

    main_loop = asyncio.get_running_loop()

    def _run() -> None:
        # 延迟 import：让 lark_oapi 的模块级代码在本线程执行，loop 绑到本线程。
        import lark_oapi as lark
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            P2CardActionTriggerResponse,
        )

        _on_card_action = _build_card_action_handler(temporal_client, main_loop, P2CardActionTriggerResponse)
        handler = lark.EventDispatcherHandler.builder("", "").register_p2_card_action_trigger(_on_card_action).build()
        cli = lark.ws.Client(
            settings.feishu_app_id,
            settings.feishu_app_secret,
            event_handler=handler,
            log_level=lark.LogLevel.INFO,
        )

        try:
            # cli.start() 阻塞：内部跑 connect → ping_loop → 永久阻塞
            # SDK 自带 auto_reconnect，断线自动重连
            cli.start()
        except Exception as e:
            logger.exception(f"Feishu WS客户端退出: {e}")

    thread = threading.Thread(target=_run, daemon=True, name="lark-ws")
    thread.start()
    logger.info("Feishu WS监听线程启动")
    return thread
