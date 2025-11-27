from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Optional

from fastapi import FastAPI
from contextlib import asynccontextmanager

from .config import AUTO_CRAWL_ENABLED, CRAWL_INTERVAL, WECHAT_SOURCES
from .services import crawl_wechat_source

logger = logging.getLogger(__name__)

_periodic_task: Optional[asyncio.Task] = None


async def _crawl_all_wechat_sources_once() -> None:
    for source in WECHAT_SOURCES:
        source_id = source.get("id")
        try:
            await crawl_wechat_source(source_id)
            logger.info("Periodic wechat crawl finished for source %s", source_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Periodic wechat crawl failed for source %s: %s", source_id, exc)


async def _periodic_crawl_loop() -> None:
    while True:
        await _crawl_all_wechat_sources_once()
        await asyncio.sleep(max(1, CRAWL_INTERVAL))


@asynccontextmanager
async def wechat_lifespan(app: FastAPI):
    """Wechat 模块的 lifespan 管理器：启动/停止定时抓取任务。"""
    global _periodic_task
    if AUTO_CRAWL_ENABLED:
        _periodic_task = asyncio.create_task(_periodic_crawl_loop())
        logger.info("Started periodic wechat crawler task with interval %s seconds", CRAWL_INTERVAL)
    yield
    if _periodic_task:
        _periodic_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _periodic_task
        logger.info("Stopped periodic wechat crawler task")
        _periodic_task = None
