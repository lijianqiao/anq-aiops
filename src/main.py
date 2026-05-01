import asyncio
from contextlib import asynccontextmanager, suppress

import redis.asyncio as aioredis
from fastapi import FastAPI
from temporalio.client import Client

from src.api.webhook import router as webhook_router
from src.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    redis_client = aioredis.from_url(settings.redis_url)
    app.state.redis = redis_client

    temporal_client = await Client.connect(settings.temporal_address)
    app.state.temporal = temporal_client

    with suppress(aioredis.exceptions.ResponseError):
        await redis_client.xgroup_create("aiops:alerts", "aiops-workers", id="0", mkstream=True)

    from src.bus.consumer import start_consumer_loop

    consumer_task = asyncio.create_task(start_consumer_loop(app))

    yield

    consumer_task.cancel()
    await redis_client.aclose()
    await temporal_client.__aexit__(None, None, None)


app = FastAPI(title="AIOps Phase 1", lifespan=lifespan)
app.include_router(webhook_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
