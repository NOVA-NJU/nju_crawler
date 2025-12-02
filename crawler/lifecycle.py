"""
爬虫模块的后台任务与生命周期钩子（lifespan新版写法）。
本文件实现定时自动抓取功能，并在FastAPI应用启动/关闭时自动管理后台任务。
采用FastAPI推荐的 lifespan 事件管理方式，兼容新版本。
"""
from __future__ import annotations  # 兼容未来类型注解语法

import asyncio  # 异步任务调度
import contextlib  # 异常抑制工具
import logging  # 日志记录
from typing import Optional  # 类型注解

from fastapi import FastAPI  # 导入FastAPI主类
from contextlib import asynccontextmanager  # lifespan上下文管理器

from .config import AUTO_CRAWL_ENABLED, CRAWL_INTERVAL, TARGET_SOURCES  # 配置项：自动抓取开关、间隔、目标源
from .services import crawl_source  # 业务函数：执行实际爬取

logger = logging.getLogger(__name__)  # 获取当前模块日志对象

# 用于保存后台定时任务对象，便于启动/关闭管理
_periodic_task: Optional[asyncio.Task] = None


async def _crawl_all_sources_once() -> None:
    """
    依次遍历所有配置的目标源，顺序执行爬取任务。
    每次爬取完成后记录日志，异常时警告。
    """
    for source in TARGET_SOURCES:
        source_id = source["id"]
        try:
            await crawl_source(source_id)  # 调用服务层异步爬取函数
            logger.info("Periodic crawl finished for source %s", source_id)  # 正常完成日志
        except Exception as exc:  # noqa: BLE001
            logger.warning("Periodic crawl failed for source %s: %s", source_id, exc)  # 异常警告日志


async def _periodic_crawl_loop() -> None:
    """
    后台循环任务，根据配置的间隔不断执行爬取。
    """
    logger.info("Starting periodic crawler loop with interval: %s seconds", CRAWL_INTERVAL)
    while True:
        start_time = asyncio.get_running_loop().time()
        await _crawl_all_sources_once()  # 执行一次全源爬取
        elapsed = asyncio.get_running_loop().time() - start_time
        logger.info("Crawler cycle finished in %.2f seconds. Sleeping for %s seconds...", elapsed, max(1, CRAWL_INTERVAL))
        await asyncio.sleep(max(1, CRAWL_INTERVAL))  # 间隔等待后继续下一轮



# lifespan事件管理器，替代原有startup/shutdown钩子
@asynccontextmanager
async def crawler_lifespan(app: FastAPI):
    """
    FastAPI推荐的生命周期管理方式。
    应用启动时自动开启定时任务，关闭时安全停止。
    """
    global _periodic_task
    if AUTO_CRAWL_ENABLED:
        _periodic_task = asyncio.create_task(_periodic_crawl_loop())  # 启动后台定时任务
        logger.info("Started periodic crawler task with interval %s seconds", CRAWL_INTERVAL)  # 启动日志
    yield  # 应用运行期间
    if _periodic_task:
        _periodic_task.cancel()  # 取消后台任务
        with contextlib.suppress(asyncio.CancelledError):
            await _periodic_task  # 等待任务安全退出
        logger.info("Stopped periodic crawler task")  # 停止日志
        _periodic_task = None  # 清空任务对象
