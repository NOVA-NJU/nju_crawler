"""
Wechat module configuration loader.

Loads `config/sources/wechat.json` and exposes `WECHAT_SOURCES` and runtime parameters.
"""
from __future__ import annotations

import os
import json

def _get_bool_env(name: str, default: bool) -> bool:
    """
    读取布尔型环境变量，支持多种写法（1/true/yes/on），无则返回默认值。
    用于控制定时任务、同步开关等布尔配置。
    """
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

CRAWL_INTERVAL = int(os.getenv("CRAWL_INTERVAL", "3600"))  # 定时抓取间隔（秒），默认1小时
REQUEST_TIMEOUT = 30  # 单次请求超时时间（秒）
MAX_RETRIES = 3       # 网络请求最大重试次数
AUTO_CRAWL_ENABLED = _get_bool_env("AUTO_CRAWL_ENABLED", True)  # 是否启用定时自动抓取

VECTOR_SYNC_ENABLED = _get_bool_env("VECTOR_SYNC_ENABLED", True)  # 是否自动同步爬取内容到向量库

TESSERACT_CMD = ""  # OCR工具tesseract命令路径，可用环境变量覆盖
TESSDATA_DIR = ""   # OCR数据目录路径，可用环境变量覆盖

DATABASE_PATH = os.getenv("CRAWLER_DB_PATH", "./data/crawler.db")  # SQLite数据库文件路径

WECHAT_SOURCES = []
WECHAT_SESSION = {}


def load_configurations():

    global WECHAT_SOURCES

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_dir = os.path.join(base_dir, "config", "sources")

    if not os.path.exists(config_dir):
        print(f"[WARN] Config directory not found: {config_dir}")
        return

    wechat_file = os.path.join(config_dir, "wechat.json")

    if not os.path.exists(wechat_file):
        return

    try:
        with open(wechat_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            if "sources" in data:
                WECHAT_SOURCES.extend(data["sources"])
            if "session" in data:
                # session may contain token/cookies_str/user_agent
                WECHAT_SESSION.update(data["session"])
    except Exception as e:
    	print(f"[WARN] Failed to load wechat config file: {wechat_file} {e}")


# load on import
load_configurations()
