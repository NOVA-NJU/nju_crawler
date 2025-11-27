"""scripts/wechat_setup.py

合并脚本（交互式）：
- 提示用户输入公众号名称（或通过 `--names` 参数传入逗号分隔的名称列表）
- 确保存在会话（优先读取 `cfg/cookies.json`，否则尝试 Selenium 登录），并将会话内容写入 `config/sources/wechat.json` 的顶层 `session` 字段，和 `sources` 平级
- 查询每个公众号的 `biz`（FakeID），并将 `sources` 中记录为只保存 `biz`（每个 source 的 `id` 为 `wechat_<biz>`）
- 可选：通过 `--crawl` 标志在添加后立即抓取新增公众号的文章

用法（PowerShell）：
    python scripts\wechat_setup.py --names "公众号A,公众号B" --count 10 --crawl

"""
from __future__ import annotations

import os
import json
import argparse
import time
import asyncio
from typing import Optional, List, Dict, Any

import requests
import sys

# Ensure project root is on sys.path so local packages (wechat) are importable
PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    from Wechat_official_clawler import auth as wechat_auth
except Exception:
    wechat_auth = None

from wechat import config as wechat_config
from wechat.services import get_fakeid_by_name, crawl_wechat_source

CFG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cfg")
COOKIES_PATH = os.path.join(CFG_DIR, "cookies.json")
WECHAT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "sources", "wechat.json")


def load_local_session() -> Optional[Dict[str, Any]]:
    if not os.path.exists(COOKIES_PATH):
        return None
    try:
        with open(COOKIES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data
    except Exception:
        return None


def ensure_session_interactive() -> Dict[str, Any]:
    # 优先使用 wechat.json 顶层 session（由 wechat.config 加载），以避免依赖旧的 cfg/cookies.json
    try:
        session_from_conf = getattr(wechat_config, "WECHAT_SESSION", None) or {}
        if session_from_conf and (session_from_conf.get("token") or session_from_conf.get("cookies_str")):
            print(f"[INFO] 使用 config/sources/wechat.json 中的 session")
            return session_from_conf
    except Exception:
        pass

    sess = load_local_session()
    if sess:
        print(f"[INFO] loaded session from {COOKIES_PATH}")
        return sess

    if wechat_auth and hasattr(wechat_auth, "get_cookies"):
        print("会话文件未找到，尝试使用 Selenium 交互式登录获取会话（请扫码）...")
        os.makedirs(CFG_DIR, exist_ok=True)
        data = wechat_auth.get_cookies()
        if data:
            # persist to cfg/cookies.json for backward compat
            try:
                with open(COOKIES_PATH, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                print(f"[INFO] 已保存会话到 {COOKIES_PATH}")
            except Exception:
                pass
            return data
    raise RuntimeError("无法获取微信会话，请先运行扫码登录或准备 cfg/cookies.json 文件。")


def merge_wechat_config(new_sources: List[Dict[str, Any]], session: Dict[str, Any]) -> None:
    """将 new_sources 合并到 `config/sources/wechat.json` 中，并把 session 写入顶层 `session` 字段（与 sources 平级）。"""
    os.makedirs(os.path.dirname(WECHAT_CONFIG_PATH), exist_ok=True)
    if os.path.exists(WECHAT_CONFIG_PATH):
        try:
            with open(WECHAT_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {"sources": []}
    else:
        data = {"sources": []}

    existing = {s.get("id"): s for s in data.get("sources", [])}
    for s in new_sources:
        existing[s["id"]] = s

    merged = {
        "session": {
            "token": session.get("token"),
            "cookies_str": session.get("cookies_str"),
            "user_agent": session.get("user_agent"),
        },
        "sources": list(existing.values()),
    }

    with open(WECHAT_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    print(f"[INFO] 已写入 {WECHAT_CONFIG_PATH}，共 {len(merged['sources'])} 个源；session 已更新")


def build_source_entry(name: str, biz: str, wx_cfg: Dict[str, Any], count: int) -> Dict[str, Any]:
    sid = f"wechat_{biz}"
    return {
        "id": sid,
        "name": name,
        "biz": biz,
        "wx_cfg": {
            "token": wx_cfg.get("token"),
            "cookies_str": wx_cfg.get("cookies_str"),
            "user_agent": wx_cfg.get("user_agent"),
        },
        "count": count,
        "created_at": int(time.time()),
    }


async def maybe_crawl_sources(source_ids: List[str]):
    for sid in source_ids:
        print(f"开始抓取: {sid}")
        try:
            items = await crawl_wechat_source(sid)
            print(f"抓取到 {len(items)} 篇文章 for {sid}")
        except Exception as exc:
            print(f"[WARN] 抓取 {sid} 失败: {exc}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--names", help="公众号名称列表，逗号分隔，例如: '校团委,信息学院'", required=False)
    parser.add_argument("--count", help="每个公众号拉取文章数量，默认 10", type=int, default=10)
    parser.add_argument("--crawl", help="添加后是否立即抓取（y/n）", action="store_true")
    args = parser.parse_args()

    if args.names:
        names = [n.strip() for n in args.names.split(",") if n.strip()]
    else:
        s = input("请输入要添加的公众号名称（用逗号分隔）：\n")
        names = [n.strip() for n in s.split(",") if n.strip()]

    if not names:
        print("未提供公众号名称，退出")
        return

    wx_cfg = ensure_session_interactive()

    new_sources = []
    new_ids = []
    for name in names:
        print(f"\n处理: {name}")
        biz = get_fakeid_by_name(wx_cfg, name)
        if not biz:
            print(f"跳过: 未找到 biz for {name}")
            continue
        entry = build_source_entry(name, biz, wx_cfg, args.count)
        new_sources.append(entry)
        new_ids.append(entry["id"])

    if new_sources:
        merge_wechat_config(new_sources, wx_cfg)
        # 更新内存中的 wechat 配置，确保随后立即抓取能找到新加入的 source
        try:
            # 清理旧的 sources，重新加载文件中的配置
            if hasattr(wechat_config, "WECHAT_SOURCES"):
                wechat_config.WECHAT_SOURCES.clear()
            if hasattr(wechat_config, "load_configurations"):
                wechat_config.load_configurations()
        except Exception:
            pass
        if args.crawl:
            asyncio.run(maybe_crawl_sources(new_ids))
        else:
            yn = input("是否立即抓取新增公众号文章？(y/N): ").strip().lower()
            if yn == "y":
                asyncio.run(maybe_crawl_sources(new_ids))
    else:
        print("没有新增源，退出")


if __name__ == "__main__":
    main()
