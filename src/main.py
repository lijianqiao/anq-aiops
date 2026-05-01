import asyncio
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
    from src.activities.feishu import send_feishu_alert, send_feishu_alert_with_ai, send_feishu_result
    from src.activities.llm import evaluate_risk, plan_action, rca_analyze
    from src.activities.runbook import execute_runbook
    from src.llm import create_llm_router
    from src.workflows.alert_workflow import AlertWorkflow

import src.activities.llm as llm_activities


@asynccontextmanager
async def lifespan(app: FastAPI):
    redis_client = aioredis.from_url(settings.redis_url)
    app.state.redis = redis_client

    temporal_client = await Client.connect(settings.temporal_address)
    app.state.temporal = temporal_client

    with suppress(ResponseError):
        await redis_client.xgroup_create("aiops:alerts", "aiops-workers", id="0", mkstream=True)

    from src.bus.consumer import start_consumer_loop

    consumer_task = asyncio.create_task(start_consumer_loop(app))

    # 初始化 LLM Router
    llm_router = create_llm_router()
    llm_activities.llm_router = llm_router

    worker = Worker(
        temporal_client,
        task_queue=settings.temporal_task_queue,
        workflows=[AlertWorkflow],
        activities=[
            send_feishu_alert, send_feishu_alert_with_ai, send_feishu_result,
            execute_runbook, write_audit,
            rca_analyze, plan_action, evaluate_risk,
        ],
    )
    worker_task = asyncio.create_task(worker.run())

    yield

    worker_task.cancel()
    consumer_task.cancel()
    await redis_client.aclose()
    await temporal_client.__aexit__(None, None, None)


app = FastAPI(title="AIOps Phase 2", lifespan=lifespan)
app.include_router(webhook_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
