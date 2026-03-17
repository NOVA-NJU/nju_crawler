from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List
from urllib.parse import urlsplit, urlunsplit

import requests
from requests import Response
from requests.exceptions import RequestException, SSLError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from wechat import auth as wechat_auth
from wechat import config as wechat_config


DEFAULT_ENDPOINTS = [
    "https://rag.njunova.com/nen/api/session",
    "https://rag.njunova.com/uaic/api/session",
]
DEFAULT_TASK_NAME = "NjuCrawler-WechatSessionRefresh"
DEFAULT_TASK_TIME = "09:00"
DEFAULT_UPLOAD_MODE = "json"
DEFAULT_FILE_FIELD = "file"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="刷新微信公众号 session 并自动同步到远端 API")
    parser.add_argument(
        "--register-task",
        action="store_true",
        help="注册一个 Windows 计划任务，每 3 天运行一次当前脚本",
    )
    parser.add_argument(
        "--task-name",
        default=os.getenv("WECHAT_SESSION_TASK_NAME", DEFAULT_TASK_NAME),
        help=f"计划任务名称，默认 {DEFAULT_TASK_NAME}",
    )
    parser.add_argument(
        "--task-time",
        default=os.getenv("WECHAT_SESSION_TASK_TIME", DEFAULT_TASK_TIME),
        help=f"计划任务开始时间，24 小时制 HH:MM，默认 {DEFAULT_TASK_TIME}",
    )
    parser.add_argument(
        "--mode",
        choices=("json", "file"),
        default=os.getenv("WECHAT_SESSION_UPLOAD_MODE", DEFAULT_UPLOAD_MODE),
        help="上传模式：json=直接发送 session 内容；file=上传 cfg/session.json 文件",
    )
    parser.add_argument(
        "--file-field",
        default=os.getenv("WECHAT_SESSION_FILE_FIELD", DEFAULT_FILE_FIELD),
        help=f"file 上传模式的表单字段名，默认 {DEFAULT_FILE_FIELD}",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.getenv("WECHAT_SESSION_SYNC_TIMEOUT", "60")),
        help="上传超时秒数，默认 60",
    )
    parser.add_argument(
        "--url",
        action="append",
        dest="urls",
        help="可重复传入多个上传地址；未传时使用内置的两个 rag 接口",
    )
    return parser.parse_args()


def get_session_path() -> Path:
    return Path(getattr(wechat_config, "SESSION_FILE", PROJECT_ROOT / "cfg" / "session.json"))


def parse_urls(cli_urls: List[str] | None) -> List[str]:
    if cli_urls:
        return cli_urls
    raw = os.getenv("WECHAT_SESSION_SYNC_URLS", "")
    if raw.strip():
        return [item.strip() for item in raw.split(",") if item.strip()]
    return list(DEFAULT_ENDPOINTS)


def load_extra_headers() -> Dict[str, str]:
    headers: Dict[str, str] = {}
    token = os.getenv("WECHAT_SESSION_SYNC_AUTH_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    raw_headers = os.getenv("WECHAT_SESSION_SYNC_HEADERS", "").strip()
    if not raw_headers:
        return headers

    try:
        parsed = json.loads(raw_headers)
    except json.JSONDecodeError as exc:
        raise ValueError("环境变量 WECHAT_SESSION_SYNC_HEADERS 不是合法 JSON") from exc

    if not isinstance(parsed, dict):
        raise ValueError("环境变量 WECHAT_SESSION_SYNC_HEADERS 必须是 JSON 对象")

    for key, value in parsed.items():
        if value is None:
            continue
        headers[str(key)] = str(value)
    return headers


def load_saved_session(session_path: Path) -> Dict[str, Any]:
    if not session_path.exists():
        raise FileNotFoundError(f"未找到 session 文件: {session_path}")
    with session_path.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)
    if not isinstance(payload, dict) or not payload:
        raise ValueError(f"session 文件内容无效: {session_path}")
    if not payload.get("token") or not payload.get("cookies_str"):
        raise ValueError(f"session 缺少 token 或 cookies_str: {session_path}")
    return payload


def refresh_session(session_path: Path) -> Dict[str, Any]:
    print("[INFO] 即将打开微信公众平台登录窗口，请用手机微信扫码。")
    session_data = wechat_auth.get_cookies()
    if not isinstance(session_data, dict) or not session_data:
        raise RuntimeError("扫码登录结束后未返回有效 session 数据")
    if session_path.exists():
        return load_saved_session(session_path)
    return session_data


def post_session_json(url: str, session_data: Dict[str, Any], headers: Dict[str, str], timeout: int) -> requests.Response:
    return requests.post(url, json=session_data, headers=headers, timeout=timeout)


def post_session_file(
    url: str,
    session_path: Path,
    headers: Dict[str, str],
    timeout: int,
    file_field: str,
) -> requests.Response:
    safe_headers = {k: v for k, v in headers.items() if k.lower() != "content-type"}
    with session_path.open("rb") as fp:
        files = {
            file_field: (session_path.name, fp, "application/json"),
        }
        return requests.post(url, files=files, headers=safe_headers, timeout=timeout)


def build_http_fallback_url(url: str) -> str | None:
    parts = urlsplit(url)
    if parts.scheme.lower() != "https":
        return None
    return urlunsplit(("http", parts.netloc, parts.path, parts.query, parts.fragment))


def send_session_request(
    url: str,
    session_data: Dict[str, Any],
    session_path: Path,
    mode: str,
    file_field: str,
    headers: Dict[str, str],
    timeout: int,
) -> Response:
    try:
        if mode == "file":
            return post_session_file(url, session_path, headers, timeout, file_field)
        return post_session_json(url, session_data, headers, timeout)
    except SSLError as exc:
        fallback_url = build_http_fallback_url(url)
        if not fallback_url:
            raise
        print(f"[WARN] HTTPS 握手失败，自动回退到 HTTP: {url} -> {fallback_url}")
        if mode == "file":
            return post_session_file(fallback_url, session_path, headers, timeout, file_field)
        return post_session_json(fallback_url, session_data, headers, timeout)
    except RequestException:
        raise


def upload_session(
    urls: Iterable[str],
    session_data: Dict[str, Any],
    session_path: Path,
    mode: str,
    file_field: str,
    timeout: int,
) -> None:
    headers = load_extra_headers()
    for url in urls:
        print(f"[INFO] 正在同步 session -> {url}")
        response = send_session_request(
            url=url,
            session_data=session_data,
            session_path=session_path,
            mode=mode,
            file_field=file_field,
            headers=headers,
            timeout=timeout,
        )

        if response.ok:
            print(f"[OK] 上传成功: {response.url}")
            continue

        message = response.text.strip()
        raise RuntimeError(f"上传失败: {response.url} -> HTTP {response.status_code} {message}")


def validate_task_time(task_time: str) -> str:
    parts = task_time.split(":")
    if len(parts) != 2:
        raise ValueError("计划任务时间格式必须是 HH:MM")
    hour, minute = parts
    if not (hour.isdigit() and minute.isdigit()):
        raise ValueError("计划任务时间格式必须是 HH:MM")
    hour_int = int(hour)
    minute_int = int(minute)
    if not (0 <= hour_int <= 23 and 0 <= minute_int <= 59):
        raise ValueError("计划任务时间超出范围")
    return f"{hour_int:02d}:{minute_int:02d}"


def register_windows_task(task_name: str, task_time: str) -> None:
    normalized_time = validate_task_time(task_time)
    python_executable = Path(sys.executable).resolve()
    script_path = Path(__file__).resolve()
    action = f'"{python_executable}" "{script_path}"'
    command = [
        "schtasks",
        "/Create",
        "/F",
        "/SC",
        "DAILY",
        "/MO",
        "3",
        "/TN",
        task_name,
        "/TR",
        action,
        "/ST",
        normalized_time,
        "/RL",
        "LIMITED",
        "/IT",
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"创建计划任务失败: {stderr}")
    print(f"[OK] 已创建计划任务: {task_name}，每 3 天执行一次，开始时间 {normalized_time}")


def main() -> int:
    args = parse_args()
    urls = parse_urls(args.urls)

    if args.register_task:
        register_windows_task(args.task_name, args.task_time)
        return 0

    if not urls:
        raise ValueError("至少需要一个上传地址")

    session_path = get_session_path()
    session_data = refresh_session(session_path)
    upload_session(
        urls=urls,
        session_data=session_data,
        session_path=session_path,
        mode=args.mode,
        file_field=args.file_field,
        timeout=args.timeout,
    )
    print("[DONE] session 已完成刷新并同步到全部目标接口。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[WARN] 用户取消了操作。")
        raise SystemExit(130)
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] {exc}")
        raise SystemExit(1)