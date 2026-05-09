"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: main.py
@DateTime: 2026-05-08 14:33:00
@Docs: 初始化 FastAPI 应用、Redis、Temporal Worker 和后台消费循环
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

import redis.asyncio as aioredis
from fastapi import FastAPI
from redis.exceptions import ResponseError
from temporalio import workflow
from temporalio.client import Client
from temporalio.worker import Worker

from src.api.webhook import router as webhook_router
from src.config import settings

with workflow.unsafe.imports_passed_through():
    from src.activities.audit import label_feedback, write_audit
    from src.activities.coordination import decr_pending_gauge, release_mutex, try_acquire_mutex
    from src.activities.feishu import send_feishu_alert, send_feishu_alert_with_agent, send_feishu_result
    from src.activities.incident_summary import post_incident_summary
    from src.activities.llm import agent_diagnose
    from src.activities.policy import evaluate_policy_activity
    from src.activities.runbook import execute_runbook
    from src.activities.sop_generator import generate_sop_candidates
    from src.llm import create_llm_router
    from src.workflows.alert_workflow import AlertWorkflow

import src.activities.audit as audit_activities
import src.activities.coordination as coordination_activities
import src.activities.llm as llm_activities
import src.activities.sop_generator as sop_generator_activities

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    redis_client = aioredis.from_url(settings.redis_url, max_connections=20)
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
    hermes_db = None
    if settings.hermes_db_url:
        from src.hermes.db import HermesDB
        from src.hermes.feedback import FeedbackRepository
        from src.hermes.repository import AuditRepository

        hermes_db = HermesDB(dsn=settings.hermes_db_url)
        try:
            await hermes_db.connect()
            await hermes_db.init_schema()
            assert hermes_db.pool is not None
            hermes_repo = AuditRepository(hermes_db.pool)
            feedback_repo = FeedbackRepository(hermes_db.pool)
            audit_activities.set_repo(hermes_repo)
            audit_activities.set_feedback_repo(feedback_repo)
            llm_activities.hermes_repo = hermes_repo
            llm_activities.hermes_feedback = feedback_repo
            sop_generator_activities.set_repo(hermes_repo)
            logger.info("Hermes 知识层已就绪")
        except Exception as exc:
            await hermes_db.close()
            hermes_db = None
            audit_activities.set_repo(None)
            audit_activities.set_feedback_repo(None)
            llm_activities.hermes_repo = None
            llm_activities.hermes_feedback = None
            sop_generator_activities.set_repo(None)
            logger.warning(f"Hermes 初始化失败，将以无知识层模式运行：{exc}")

    worker = Worker(
        temporal_client,
        task_queue=settings.temporal_task_queue,
        workflows=[AlertWorkflow],
        activities=[
            send_feishu_alert,
            send_feishu_alert_with_agent,
            send_feishu_result,
            execute_runbook,
            write_audit,
            label_feedback,
            agent_diagnose,
            evaluate_policy_activity,
            decr_pending_gauge,
            try_acquire_mutex,
            release_mutex,
            post_incident_summary,
            generate_sop_candidates,
        ],
    )
    worker_task = asyncio.create_task(worker.run())

    from src.scheduler.jobs import start_scheduler, stop_scheduler

    scheduler = await start_scheduler()

    from src.bus.consumer import start_consumer_loop

    consumer_task = asyncio.create_task(start_consumer_loop(app))
    app.state.consumer_task = consumer_task

    # 飞书长连接监听（卡片审批回调）。daemon thread，进程退出自动结束。
    # 没配 App ID/Secret 时直接跳过，方便本地起服务跑非飞书功能。
    if settings.feishu_app_id and settings.feishu_app_secret:
        from src.feishu_listener import start_in_thread as start_feishu_listener

        start_feishu_listener(temporal_client)

    yield

    worker_task.cancel()
    consumer_task.cancel()
    await asyncio.gather(worker_task, consumer_task, return_exceptions=True)
    await stop_scheduler(scheduler)
    if hermes_db is not None:
        await hermes_db.close()
    await redis_client.aclose()


app = FastAPI(title="AIOps Phase 3", lifespan=lifespan)
app.include_router(webhook_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/deep")
async def health_deep() -> dict[str, Any]:
    """深度健康检查：consumer 存活、Redis stream 积压、各组件状态"""
    checks: dict[str, Any] = {}
    healthy = True

    # consumer loop 存活
    consumer_task: asyncio.Task | None = getattr(app.state, "consumer_task", None)
    if consumer_task is not None:
        checks["consumer_alive"] = not consumer_task.done()
        if consumer_task.done():
            healthy = False
    else:
        checks["consumer_alive"] = False
        healthy = False

    # Redis stream 积压
    redis_client: aioredis.Redis | None = getattr(app.state, "redis", None)
    if redis_client is not None:
        try:
            pending = await redis_client.xpending("aiops:alerts", "aiops-workers")
            checks["stream_pending"] = pending.get("pending", 0) if isinstance(pending, dict) else 0
        except Exception:
            checks["stream_pending"] = "error"
            healthy = False
    else:
        checks["redis"] = "not_connected"
        healthy = False

    checks["status"] = "ok" if healthy else "degraded"
    return checks
