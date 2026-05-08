"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: main.py
@DateTime: 2026-05-08 14:33:00
@Docs: 初始化 FastAPI 应用、Redis、Temporal Worker 和后台消费循环
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import redis.asyncio as aioredis
from fastapi import FastAPI
from redis.exceptions import ResponseError
from temporalio import workflow
from temporalio.client import Client
from temporalio.worker import Worker

from src.api.webhook import router as webhook_router
from src.config import settings

with workflow.unsafe.imports_passed_through():
    from src.activities.audit import write_audit
    from src.activities.coordination import decr_pending_gauge, release_mutex, try_acquire_mutex
    from src.activities.feishu import send_feishu_alert, send_feishu_alert_with_agent, send_feishu_result
    from src.activities.llm import agent_diagnose
    from src.activities.policy import evaluate_policy_activity
    from src.activities.runbook import execute_runbook
    from src.llm import create_llm_router
    from src.workflows.alert_workflow import AlertWorkflow

import src.activities.coordination as coordination_activities
import src.activities.llm as llm_activities


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    redis_client = aioredis.from_url(settings.redis_url)
    app.state.redis = redis_client

    temporal_client = await Client.connect(settings.temporal_address)
    app.state.temporal = temporal_client

    with suppress(ResponseError):
        await redis_client.xgroup_create("aiops:alerts", "aiops-workers", id="0", mkstream=True)

    # 顺序：先初始化 LLM Router → 起 worker → 最后才起 consumer，
    # 否则 consumer 拉到积压消息触发 workflow 时 llm_router 还是 None
    llm_router = create_llm_router()
    llm_activities.llm_router = llm_router
    coordination_activities.redis_client = redis_client

    worker = Worker(
        temporal_client,
        task_queue=settings.temporal_task_queue,
        workflows=[AlertWorkflow],
        activities=[
            send_feishu_alert, send_feishu_alert_with_agent, send_feishu_result,
            execute_runbook, write_audit,
            agent_diagnose,
            evaluate_policy_activity,
            decr_pending_gauge, try_acquire_mutex, release_mutex,
        ],
    )
    worker_task = asyncio.create_task(worker.run())

    from src.bus.consumer import start_consumer_loop

    consumer_task = asyncio.create_task(start_consumer_loop(app))

    # 飞书长连接监听（卡片审批回调）。daemon thread，进程退出自动结束。
    # 没配 App ID/Secret 时直接跳过，方便本地起服务跑非飞书功能。
    if settings.feishu_app_id and settings.feishu_app_secret:
        from src.feishu_listener import start_in_thread as start_feishu_listener

        start_feishu_listener(temporal_client)

    yield

    worker_task.cancel()
    consumer_task.cancel()
    await asyncio.gather(worker_task, consumer_task, return_exceptions=True)
    await redis_client.aclose()


app = FastAPI(title="AIOps Phase 3", lifespan=lifespan)
app.include_router(webhook_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
