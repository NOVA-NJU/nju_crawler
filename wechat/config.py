"""
Wechat module configuration loader.

Loads `config/sources/wechat.json` and exposes `WECHAT_SOURCES` and runtime parameters.
"""
from __future__ import annotations

import os
import sys
import json
from typing import Dict, Any, List, Union

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

def _runtime_base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


BASE_DIR = _runtime_base_dir()
SOURCES_DIR = os.path.join(BASE_DIR, "config", "sources")
WECHAT_CONFIG_FILE = os.path.join(SOURCES_DIR, "wechat.json")
SESSION_DIR = os.getenv("WECHAT_SESSION_DIR", os.path.join(BASE_DIR, "cfg"))
SESSION_FILE = os.getenv("WECHAT_SESSION_FILE", os.path.join(SESSION_DIR, "session.json"))

WECHAT_SOURCES: List[dict] = []
WECHAT_SESSION: Dict[str, Any] = {}
_SESSION_NOTICE_SHOWN = False


def _read_json(path: str) -> Union[Dict[str, Any], List[Any]]:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read().strip()
        if not raw:
            return {}
        return json.loads(raw)


def load_configurations() -> None:
    """Load wechat sources from config file."""
    global WECHAT_SOURCES
    WECHAT_SOURCES.clear()
    if not os.path.exists(WECHAT_CONFIG_FILE):
        return
    try:
        data = _read_json(WECHAT_CONFIG_FILE)
        if isinstance(data, list):
            sources = data
        elif isinstance(data, dict):
            sources = data.get("sources", [])
        else:
            sources = []
        # 仅保留关键字段，避免旧版遗留的 wx_cfg/session 冗余
        for src in sources:
            if not isinstance(src, dict):
                continue
            WECHAT_SOURCES.append(
                {
                    "id": src.get("id"),
                    "name": src.get("name"),
                    "biz": src.get("biz"),
                    "count": src.get("count", 5),
                    "created_at": src.get("created_at", 0),
                    "article_urls": src.get("article_urls") or [],
                }
            )
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] Failed to load wechat config file: {WECHAT_CONFIG_FILE} {exc}")


def load_session() -> None:
    """Load session token/cookies from cfg/session.json if present."""
    global WECHAT_SESSION
    WECHAT_SESSION.clear()
    if not os.path.exists(SESSION_FILE):
        return
    try:
        data = _read_json(SESSION_FILE)
        if isinstance(data, dict):
            WECHAT_SESSION.update(data)
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] Failed to load wechat session file: {SESSION_FILE} {exc}")


def has_valid_session(session: Dict[str, Any] | None = None) -> bool:
    sess = session or WECHAT_SESSION
    return bool(sess.get("token") and sess.get("cookies_str"))


def _print_session_hint() -> None:
    global _SESSION_NOTICE_SHOWN
    if _SESSION_NOTICE_SHOWN:
        return
    _SESSION_NOTICE_SHOWN = True
    banner = "=" * 72
    print(
        f"\n{banner}\n"
        "⚠️  未检测到有效的微信后台登录态\n"
        "   请执行 `python scripts/\\wechat_setup.py""` 扫码登录，"
        "或将 token/cookies 写入 cfg/session.json 后重启服务。\n"
        f"{banner}\n"
    )


def ensure_session(interactive: bool = False, *, prompt_if_missing: bool = True) -> Dict[str, Any]:
    """Ensure a valid session is loaded. Optionally trigger login if missing."""
    if has_valid_session():
        return WECHAT_SESSION

    load_session()
    if has_valid_session():
        return WECHAT_SESSION

    if interactive:
        try:
            from . import auth  # lazy import to avoid selenium dependency on import

            auth.get_cookies()
            load_session()
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] 自动登录失败: {exc}")
    elif prompt_if_missing:
        _print_session_hint()
    return WECHAT_SESSION


# load on import
load_configurations()
load_session()
