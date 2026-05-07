"""飞书卡片回调长连接监听器

为什么用线程而不是 asyncio.create_task：
  lark_oapi.ws.Client 在模块导入时就 grab 了一个 event loop，并用
  loop.run_until_complete(...) 启动；这两点都和 FastAPI 的 running loop 不兼容。
  最稳的隔离方式是单开一个守护线程，让 SDK 在自己的线程 + 自己的 loop 里跑。
  Handler 内部通过 asyncio.run_coroutine_threadsafe 把 Temporal signal 调度回主 loop。

注意：lark_oapi 的所有 import 都放在 _run() 内部，确保模块级代码在子线程首次执行，
SDK 的模块级 loop 才会绑到子线程，而不是主线程。
"""

import asyncio
import logging
import threading
from typing import TYPE_CHECKING

from src.config import settings

if TYPE_CHECKING:
    from temporalio.client import Client as TemporalClient

logger = logging.getLogger(__name__)


def start_in_thread(temporal_client: TemporalClient) -> threading.Thread:
    """启动飞书长连接监听，返回守护线程句柄"""
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        raise RuntimeError("FEISHU_APP_ID / FEISHU_APP_SECRET 未配置，无法启动飞书监听")

    main_loop = asyncio.get_running_loop()

    def _run() -> None:
        # 延迟 import：让 lark_oapi 的模块级代码在本线程执行，loop 绑到本线程
        import lark_oapi as lark
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            P2CardActionTrigger,
            P2CardActionTriggerResponse,
        )

        async def _signal_workflow(workflow_id: str, approved: bool) -> None:
            from src.workflows.alert_workflow import AlertWorkflow, ApprovalDecision

            handle = temporal_client.get_workflow_handle(workflow_id)
            await handle.signal(AlertWorkflow.approve, ApprovalDecision(approved=approved))

        def _on_card_action(data: P2CardActionTrigger) -> P2CardActionTriggerResponse:
            try:
                event = data.event
                value = (event.action.value if event and event.action else None) or {}
                workflow_id = value.get("workflow_id")
                decision = value.get("action")

                if decision not in {"approve", "reject"} or not workflow_id:
                    return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "回调参数不完整"}})

                # 跨线程调度到主 loop 执行 Temporal signal
                future = asyncio.run_coroutine_threadsafe(
                    _signal_workflow(workflow_id, decision == "approve"),
                    main_loop,
                )
                future.result(timeout=10)

                operator = event.operator if event else None
                operator_id = (operator.open_id or operator.user_id) if operator else None
                logger.info(
                    f"Approval signal sent: workflow={workflow_id}, "
                    f"approved={decision == 'approve'}, operator={operator_id}"
                )
                return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "已收到，处理中"}})
            except Exception as e:
                logger.exception(f"Feishu 卡片回调处理失败: {e}")
                return P2CardActionTriggerResponse({"toast": {"type": "error", "content": f"处理失败: {e}"}})

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
