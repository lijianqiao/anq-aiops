"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: jobs.py
@DateTime: 2026-05-08 23:50:00
@Docs: 启动 APScheduler 定时任务并调度 SOP 生成
"""

import asyncio
import logging
from datetime import UTC, datetime

from apscheduler import AsyncScheduler
from apscheduler.triggers.cron import CronTrigger

from src.activities.sop_generator import generate_sop_candidates
from src.config import settings
from src.sop.git_pr import open_sop_pr

logger = logging.getLogger(__name__)


async def daily_sop_job() -> None:
    """每天生成 SOP 候选并尝试创建 PR。"""
    logger.info("开始执行每日 SOP 生成任务")
    try:
        paths = await generate_sop_candidates()
        if not paths:
            logger.info("今日没有 SOP 候选")
            return
        suffix = datetime.now(UTC).strftime("%Y%m%d")
        await asyncio.to_thread(open_sop_pr, paths, suffix)
    except Exception as exc:
        logger.warning(f"每日 SOP 生成任务失败：{exc}")


async def start_scheduler() -> AsyncScheduler | None:
    """启动后台 scheduler；配置为 0 时禁用。"""
    if settings.sop_gen_schedule_hour < 0:
        logger.info("SOP 定时生成已禁用")
        return None

    scheduler = AsyncScheduler()
    await scheduler.__aenter__()
    await scheduler.add_schedule(
        daily_sop_job,
        CronTrigger(hour=settings.sop_gen_schedule_hour, minute=0),
        id="daily_sop_generation",
    )
    await scheduler.start_in_background()
    logger.info(f"SOP 定时生成已启动：每天 {settings.sop_gen_schedule_hour}:00")
    return scheduler


async def stop_scheduler(scheduler: AsyncScheduler | None) -> None:
    """停止 scheduler。"""
    if scheduler is not None:
        await scheduler.__aexit__(None, None, None)
