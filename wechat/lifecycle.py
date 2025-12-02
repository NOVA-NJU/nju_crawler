from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Optional

from fastapi import FastAPI
from contextlib import asynccontextmanager

from .config import (
    AUTO_CRAWL_ENABLED,
    CRAWL_INTERVAL,
    WECHAT_SOURCES,
    ensure_session,
    has_valid_session,
)
from .services import crawl_wechat_source

logger = logging.getLogger(__name__)

_periodic_task: Optional[asyncio.Task] = None


async def _crawl_all_wechat_sources_once() -> None:
    ensure_session(interactive=False)
    if not has_valid_session():
        logger.warning(
            "Skipping WeChat crawl because cfg/session.json is missing or invalid. "
            "Run scripts/wechat_setup.py to log in before retrying."
        )
        return
    for source in WECHAT_SOURCES:
        source_id = source.get("id")
        try:
            await crawl_wechat_source(source_id)
            logger.info("Periodic wechat crawl finished for source %s", source_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Periodic wechat crawl failed for source %s: %s", source_id, exc)


async def _periodic_crawl_loop() -> None:
    logger.info("Starting periodic wechat crawl loop with interval: %s seconds", CRAWL_INTERVAL)
    while True:
        start_time = asyncio.get_running_loop().time()
        await _crawl_all_wechat_sources_once()
        elapsed = asyncio.get_running_loop().time() - start_time
        logger.info("WeChat crawl cycle finished in %.2f seconds. Sleeping for %s seconds...", elapsed, max(1, CRAWL_INTERVAL))
        await asyncio.sleep(max(1, CRAWL_INTERVAL))


@asynccontextmanager
async def wechat_lifespan(app: FastAPI):
    """Wechat 模块的 lifespan 管理器：启动/停止定时抓取任务。"""
    global _periodic_task
    ensure_session(interactive=False)
    if AUTO_CRAWL_ENABLED:
        if has_valid_session():
            _periodic_task = asyncio.create_task(_periodic_crawl_loop())
            logger.info("Started periodic wechat crawler task with interval %s seconds", CRAWL_INTERVAL)
        else:
            banner = "=" * 68
            logger.warning(
                "\n%s\n"
                "⚠️  AUTO_CRAWL_ENABLED = true，但未找到有效的微信登录态。\n"
                "   定时抓取任务已跳过，请执行 `python scripts/\\wechat_setup.py""` 扫码登录，"
                "并确认 cfg/session.json 有效后重启。\n"
                "%s",
                banner,
                banner,
            )
    yield
    if _periodic_task:
        _periodic_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _periodic_task
        logger.info("Stopped periodic wechat crawler task")
        _periodic_task = None
