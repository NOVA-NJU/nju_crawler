"""
微信扫码登录工具。
保存会话到 cfg/session.json。
"""

from __future__ import annotations

import datetime
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.edge.webdriver import WebDriver as EdgeWebDriver
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.firefox.webdriver import WebDriver as FirefoxWebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

WX_LOGIN = "https://mp.weixin.qq.com/"
WX_HOME = "https://mp.weixin.qq.com/cgi-bin/home"


def _runtime_project_root() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


PROJECT_ROOT = _runtime_project_root()
QR_SAVE_PATH = os.path.join(PROJECT_ROOT, "wx_login_qrcode.png")
SESSION_DIR = os.getenv("WECHAT_SESSION_DIR", os.path.join(PROJECT_ROOT, "cfg"))
OUTPUT_JSON = os.getenv("WECHAT_SESSION_FILE", os.path.join(SESSION_DIR, "session.json"))


def _parse_browser_order() -> List[str]:
    raw = os.getenv("WECHAT_LOGIN_BROWSERS", "edge,firefox")
    order = [item.strip().lower() for item in raw.split(",") if item.strip()]
    valid = [b for b in order if b in {"edge", "firefox"}]
    return valid or ["edge", "firefox"]


def _create_edge_driver() -> EdgeWebDriver:
    options = EdgeOptions()
    options.use_chromium = True
    service = EdgeService()
    return EdgeWebDriver(service=service, options=options)


def _create_firefox_driver() -> FirefoxWebDriver:
    options = FirefoxOptions()
    # options.add_argument("-headless")  # 保持有界面，方便扫码登录
    service = FirefoxService()
    return FirefoxWebDriver(service=service, options=options)


def create_web_driver() -> tuple[Any, str]:
    errors: List[str] = []
    for browser in _parse_browser_order():
        try:
            driver = _create_edge_driver() if browser == "edge" else _create_firefox_driver()
            driver.set_window_position(80, 60)
            driver.set_window_size(1280, 900)
            print(f"[信息] 已启动浏览器: {browser}")
            return driver, browser
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{browser}: {exc}")
    raise RuntimeError("无法启动浏览器，请安装/更新 Edge 或 Firefox。详细错误: " + " | ".join(errors))


def wait_first_image_loaded(driver: Any, timeout: int = 20) -> None:
    WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script("const img=document.querySelector('img');return img && img.complete;")
    )


def find_qr_element(driver: Any, timeout: int = 20):
    selectors = [
        ".login__type__container__scan__qrcode img",
        ".login__type__container__scan__qrcode canvas",
        ".login__type__container__scan__qrcode",
        ".login__qrcode img",
        ".login__qrcode canvas",
        ".qrcode img",
        ".qrcode canvas",
    ]
    for css in selectors:
        try:
            el = WebDriverWait(driver, timeout).until(EC.visibility_of_element_located((By.CSS_SELECTOR, css)))
            size = el.size or {}
            if int(size.get("width", 0)) < 120 or int(size.get("height", 0)) < 120:
                continue
            return el
        except Exception:
            continue
    raise RuntimeError("二维码元素未找到，请检查页面结构或更新选择器")


def _image_has_content(path: str) -> bool:
    try:
        from PIL import Image

        with Image.open(path) as img:
            gray = img.convert("L")
            extrema = gray.getextrema()
            if not extrema:
                return False
            low, high = extrema
            # 几乎纯色时范围会很小，通常是灰色占位图
            return (high - low) >= 12
    except Exception:
        return False


def save_qr_image(driver: Any, el, save_path: str = QR_SAVE_PATH) -> None:
    try:
        el.screenshot(save_path)
        if os.path.getsize(save_path) > 512 and _image_has_content(save_path):
            return
    except Exception:
        pass

    tmp_full = save_path + "_full.png"
    driver.save_screenshot(tmp_full)
    loc = el.location
    size = el.size

    from PIL import Image

    with Image.open(tmp_full) as img:
        left, top = int(loc["x"]), int(loc["y"])
        right, bottom = int(loc["x"] + size["width"]), int(loc["y"] + size["height"])
        img.crop((left, top, right, bottom)).save(save_path)

    os.remove(tmp_full)
    if not _image_has_content(save_path):
        raise RuntimeError("二维码截图为空白，请重试或切换浏览器（WECHAT_LOGIN_BROWSERS=edge,firefox）")


def extract_token(driver: Any) -> Optional[str]:
    m = re.search(r"[?&]token=([^&#]+)", driver.current_url)
    return m.group(1) if m else None


def cookies_and_expiry(driver: Any) -> Tuple[List[Dict[str, Any]], Optional[int]]:
    cookies = driver.get_cookies()
    expiry_ts = None
    exp_list: List[int] = []
    for c in cookies:
        if "expiry" in c:
            try:
                exp_list.append(int(c["expiry"]))
            except Exception:
                pass
    if exp_list:
        expiry_ts = min(exp_list)
    return cookies, expiry_ts


def format_cookies_str(cookies: List[Dict[str, Any]]) -> str:
    return "; ".join([f"{c['name']}={c['value']}" for c in cookies])


def verify_logged_in(driver: Any, timeout: int = 20) -> bool:
    try:
        WebDriverWait(driver, timeout).until(EC.url_contains("/cgi-bin/home"))
        return True
    except Exception:
        return False


def get_cookies() -> Dict[str, Any]:
    driver, browser = create_web_driver()
    try:
        print("已打开微信公众平台登录页，请在浏览器中直接扫码登录...")
        driver.get(WX_LOGIN)
        WebDriverWait(driver, 300).until(lambda d: ("token=" in d.current_url) or ("/cgi-bin/home" in d.current_url))

        token = extract_token(driver)
        cookies, expiry_ts = cookies_and_expiry(driver)
        cookies_str = format_cookies_str(cookies)
        user_agent = driver.execute_script("return navigator.userAgent;")

        data: Dict[str, Any] = {
            "token": token,
            "cookies": cookies,
            "cookies_str": cookies_str,
            "user_agent": user_agent,
            "browser": browser,
            "expiry": expiry_ts,
            "expiry_human": datetime.datetime.utcfromtimestamp(expiry_ts).strftime("%Y-%m-%d %H:%M:%S UTC") if expiry_ts else None,
            "saved_at": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        }

        os.makedirs(os.path.dirname(OUTPUT_JSON), exist_ok=True)
        with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        ok = verify_logged_in(driver, timeout=10)
        print(f"[结果] 登录成功: {ok}, token: {token}")
        print(f"[输出] session 已保存到: {os.path.abspath(OUTPUT_JSON)}")
        print("[信息] 已获取 session，正在关闭浏览器...")
        return data
    except WebDriverException as exc:
        message = str(exc)
        if "Failed to decode response from marionette" in message:
            raise RuntimeError("Firefox 驱动通信失败。建议优先使用 Edge（默认已启用），或升级 Firefox 后重试。") from exc
        raise
    finally:
        driver.quit()


if __name__ == "__main__":
    get_cookies()
