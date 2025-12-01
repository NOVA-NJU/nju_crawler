"""
主应用入口：独立 FastAPI 爬虫服务

本文件将 crawler 作为主应用运行，支持直接启动。
"""
from fastapi import FastAPI
import os
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import logging
from crawler.router import router as crawler_router
from crawler.lifecycle import crawler_lifespan
from wechat.router import router as wechat_router
from wechat.lifecycle import wechat_lifespan
from wechat.config import ensure_session, has_valid_session
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger("nju_crawler.main")

# 启动前检查一次微信登录状态，避免运行时才发现需要扫码
ensure_session(interactive=False)
if not has_valid_session():
    separator = "=" * 60
    warning_msg = (
        "\n%s\n"
        "⚠️  WeChat 会话缺失，定时抓取已暂停。\n"
        "   运行 `python scripts/\\wechat_setup.py""` 扫码登录，"
        "或补充 cfg/session.json 后重新启动。\n"
        "%s"
    )
    logger.warning(warning_msg, separator, separator)

@asynccontextmanager
async def _combined_lifespan(app: FastAPI):
    # compose crawler and wechat lifespans so both background tasks run
    async with crawler_lifespan(app):
        async with wechat_lifespan(app):
            yield


app = FastAPI(lifespan=_combined_lifespan)
# Both crawler and wechat lifespans are now composed; routers mounted below
app.include_router(crawler_router)
app.include_router(wechat_router)

@app.get("/")
async def read_root():
    return FileResponse("index.html")


origins = [
    "*" 
    # 将来部署时, 您应该只允许您的前端网址
    # "http://your-frontend-domain.com", 
]

# 3. 添加中间件 (Middleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,       # 允许访问的来源
    allow_credentials=True,    # 允许 cookie
    allow_methods=["*"],       # 允许所有 HTTP 方法 (GET, POST, OPTIONS 等)
    allow_headers=["*"],       # 允许所有请求头
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
