"""
微信扫码登录工具（从 Wechat_official_clawler.auth 迁移并调整路径）。
保存会话到项目根的 `cfg/cookies.json`。
"""
import os, json, time, datetime
from typing import Optional, Tuple, Dict, Any, List
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

WX_LOGIN = "https://mp.weixin.qq.com/"
WX_HOME = "https://mp.weixin.qq.com/cgi-bin/home"
QR_SAVE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'wx_login_qrcode.png')

# OUTPUT_JSON => project_root/cfg/cookies.json
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_JSON = os.path.join(PROJECT_ROOT, "cfg", "cookies.json")


def wait_first_image_loaded(driver, timeout=20):
  WebDriverWait(driver, timeout).until(
    lambda d: d.execute_script(
      "const img=document.querySelector('img');return img && img.complete;")
  )


def find_qr_element(driver, timeout=20):
  selectors = [
    ".login__type__container__scan__qrcode",
  ]
  for css in selectors:
    try:
      el = WebDriverWait(driver, timeout).until(
        EC.visibility_of_element_located((By.CSS_SELECTOR, css))
      )
      return el
    except Exception:
      continue
  raise RuntimeError("二维码元素未找到，请检查页面结构或更新选择器")


def save_qr_image(driver, el, save_path=QR_SAVE_PATH):
  try:
    el.screenshot(save_path)
    if os.path.getsize(save_path) > 512:
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
    cropped = img.crop((left, top, right, bottom))
    cropped.save(save_path)
  os.remove(tmp_full)


def extract_token(driver) -> Optional[str]:
  url = driver.current_url
  import re
  m = re.search(r"[?&]token=([^&#]+)", url)
  if m:
    return m.group(1)
  else:
    return None


def cookies_and_expiry(driver) -> Tuple[List[Dict[str, Any]], Optional[int]]:
  cookies = driver.get_cookies()
  expiry_ts = None
  exp_list = []
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


def verify_logged_in(driver, timeout=20) -> bool:
  try:
    WebDriverWait(driver, timeout).until(EC.url_contains("/cgi-bin/home"))
    return True
  except Exception:
    return False


def get_cookies():
  options = webdriver.FirefoxOptions()
  options.add_argument("-headless")
  service = Service()
  driver = webdriver.Firefox(service=service, options=options)
  driver.set_window_size(1280, 900)
  try:
    print("开始获取二维码...")
    driver.get(WX_LOGIN)
    wait_first_image_loaded(driver, timeout=20)
    qr = find_qr_element(driver, timeout=20)
    os.makedirs(os.path.dirname(QR_SAVE_PATH), exist_ok=True)
    save_qr_image(driver, qr, QR_SAVE_PATH)
    if os.path.getsize(QR_SAVE_PATH) < 400:
      raise RuntimeError("二维码图片异常（过小），请重新运行或手动刷新页面后再试")

    print(f"[信息] 已保存二维码: {os.path.abspath(QR_SAVE_PATH)}，请扫描登录...")

    WebDriverWait(driver, 180).until(
      lambda d: ("token=" in d.current_url) or ("/cgi-bin/home" in d.current_url)
    )

    token = extract_token(driver)
    cookies, expiry_ts = cookies_and_expiry(driver)
    cookies_str = format_cookies_str(cookies)
    user_agent = driver.execute_script("return navigator.userAgent;")

    data = {
      "token": token,
      "cookies": cookies,
      "cookies_str": cookies_str,
      "user_agent": user_agent,
      "expiry": expiry_ts,
      "expiry_human": (
        datetime.datetime.utcfromtimestamp(expiry_ts).strftime("%Y-%m-%d %H:%M:%S UTC")
        if expiry_ts else None
      ),
      "saved_at": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
    }
    os.makedirs(os.path.dirname(OUTPUT_JSON), exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
      json.dump(data, f, ensure_ascii=False, indent=2)

    ok = verify_logged_in(driver, timeout=10)
    print(f"[结果] 登录成功: {ok}, token: {token}")
    print(f"[输出] 已保存会话到: {os.path.abspath(OUTPUT_JSON)}")
  finally:
    driver.quit()
  return data
