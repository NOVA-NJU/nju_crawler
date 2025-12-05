from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import requests
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from bs4 import BeautifulSoup

from .config import WECHAT_SOURCES, WECHAT_SESSION, REQUEST_TIMEOUT, SESSION_FILE
from crawler.models import CrawlItem
from storage import database

# reuse a requests Session like the original project
Session = requests.Session()

_TIME_PATTERNS = (
	re.compile(r"var createTime\s*=\s*['\"](.*?)['\"]"),
	re.compile(r"var ct\s*=\s*['\"](.*?)['\"]"),
	re.compile(r"var publish_time\s*=\s*['\"](.*?)['\"]"),
)


def _parse_publish_timestamp(raw_value: str) -> Optional[datetime]:
	"""Normalize multiple publish_time formats into a UTC datetime."""
	if not raw_value:
		return None
	value = raw_value.strip()
	if not value:
		return None
	try:
		if value.isdigit():
			return datetime.fromtimestamp(int(value), tz=timezone.utc)
		return datetime.fromtimestamp(float(value), tz=timezone.utc)
	except (ValueError, OSError):
		pass
	for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
		try:
			dt = datetime.strptime(value, fmt)
			return dt.replace(tzinfo=timezone.utc)
		except ValueError:
			continue
	try:
		dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
		return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
	except ValueError:
		return None


def _extract_publish_datetime(html: str) -> tuple[Optional[datetime], Optional[str]]:
	"""Extract publish datetime (if any) and return both parsed datetime and raw value."""
	for pattern in _TIME_PATTERNS:
		match = pattern.search(html)
		if not match:
			continue
		raw_value = match.group(1).strip()
		parsed = _parse_publish_timestamp(raw_value)
		if parsed:
			return parsed, raw_value
		return None, raw_value
	return None, None


def upsert_session(payload: Dict[str, Any]) -> Dict[str, Any]:
	"""Persist session payload to cfg/session.json and refresh in-memory session."""
	if not isinstance(payload, dict):
		raise ValueError("session payload must be a JSON object")
	cleaned = {k: v for k, v in payload.items() if v is not None}
	if not cleaned:
		raise ValueError("session payload cannot be empty")
	now = datetime.now(timezone.utc)
	cleaned.setdefault("saved_at", now.strftime("%Y-%m-%d %H:%M:%S UTC"))
	expiry_value = cleaned.get("expiry")
	if expiry_value and not cleaned.get("expiry_human"):
		try:
			expiry_int = int(expiry_value)
			cleaned["expiry"] = expiry_int
			cleaned["expiry_human"] = datetime.fromtimestamp(expiry_int, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
		except (ValueError, OSError, OverflowError):
			pass
	os.makedirs(os.path.dirname(SESSION_FILE), exist_ok=True)
	with open(SESSION_FILE, "w", encoding="utf-8") as fp:
		json.dump(cleaned, fp, ensure_ascii=False, indent=2)
	WECHAT_SESSION.clear()
	WECHAT_SESSION.update(cleaned)
	return cleaned


def compute_sha256(*segments: Optional[str]) -> str:
	payload = "\n".join(segment or "" for segment in segments)
	return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def format_wechat_content(content_div) -> str:
	"""
	Format WeChat article content to preserve structure and images.
	Replaces <br> with newlines, handles images as markdown, and ensures paragraphs are separated.
	"""
	if not content_div:
		return ""
		
	# 1. Replace <br> with newline
	for br in content_div.find_all("br"):
		br.replace_with("\n")
		
	# 2. Handle Images
	for img in content_div.find_all("img"):
		src = img.get("data-src") or img.get("src")
		if src:
			# Insert newline before and after image to ensure it's on its own line
			img.replace_with(f"\n![图片]({src})\n")
			
	# 3. Handle Block Elements: Ensure they are separated by newlines
	# Append a newline to block elements to ensure separation when text is extracted
	for tag in content_div.find_all(["p", "section", "h1", "h2", "h3", "h4", "h5", "h6", "li", "div", "blockquote"]):
		tag.append("\n")
		
	# 4. Get text with no separator (relying on our inserted newlines)
	# We use strip=False to preserve the newlines we added, but we'll clean up later
	text = content_div.get_text(separator="", strip=False)
	
	# 5. Post-processing
	# Split by lines, strip each line to remove excessive spaces (but keep the line structure)
	lines = [line.strip() for line in text.split('\n')]
	
	# Remove empty lines
	lines = [line for line in lines if line]
	
	# Join with single newline as requested
	return "\n".join(lines)


def parse_wechat_article(html: str) -> Dict[str, Any]:
	"""Parse a WeChat article HTML and return aggregated text content."""
	soup = BeautifulSoup(html, "lxml")

	# Check for deleted content markers
	if any(marker in html for marker in ["此内容已被发布者删除", "此内容因违规无法查看", "该内容已被发布者删除"]):
		return {"Error": "Content deleted", "Content": ""}

	if "当前环境异常" in html:
		return {"Error": "WeChat environment exception (verification required)", "Content": ""}

	content_div = soup.find("div", class_="rich_media_content")
	if not content_div:
		content_div = soup.find("div", id="js_content")
	
	# Use the new formatting function
	content = format_wechat_content(content_div)

	# Fallback for empty content (e.g. image only articles)
	if not content:
		# Try to get description from meta tags
		desc_tag = soup.find("meta", property="og:description")
		if desc_tag and desc_tag.get("content"):
			import html as html_lib
			content = html_lib.unescape(desc_tag.get("content"))
		
		# If still empty, try to get the main image
		if not content:
			img_tag = soup.find("meta", property="og:image")
			if img_tag and img_tag.get("content"):
				content = f"![封面图]({img_tag.get('content')})"

	if not content:
		# Fallback to meta description for share pages or protected pages
		meta_desc = soup.find("meta", property="og:description")
		if not meta_desc:
			meta_desc = soup.find("meta", attrs={"name": "description"})
		
		content = meta_desc.get("content", "") if meta_desc else ""

		# If content is still empty, try to get the cover image (for image-only share pages)
		if not content:
			og_image = soup.find("meta", property="og:image")
			if not og_image:
				# Try attrs search as fallback
				og_image = soup.find("meta", attrs={"property": "og:image"})
			
			if og_image and og_image.get("content"):
				content = f"【图片内容】\n![Image]({og_image.get('content')})"

	title = soup.find("h1", class_="rich_media_title")
	title_text = title.get_text(strip=True) if title else ""
	
	# Fallback for title
	if not title_text:
		og_title = soup.find("meta", property="og:title")
		if og_title:
			title_text = og_title.get("content", "")

	if not title_text:
		meta_title = soup.find("meta", property="og:title")
		if meta_title:
			title_text = meta_title.get("content", "")

	author = soup.find("a", id="js_name")
	author_text = author.get_text(strip=True) if author else ""

	publish_dt, raw_time = _extract_publish_datetime(html)

	meta: Dict[str, Any] = {}
	if title_text:
		meta["Title"]=title_text
	if author_text:
		meta["Author"]=author_text
	if publish_dt:
		meta["Time"] = publish_dt
	elif raw_time:
		meta["Time"] = raw_time
	if content:
		meta["Content"]=content
	

	# full_text = "\n".join(meta) + "\n\n" + content
	return meta


def get_article_list(wx_cfg: dict, fakeid: str, count: int = 5) -> List[str]:
	"""根据 fakeid 使用 mp.weixin 的 appmsgpublish 接口获取文章 URL 列表。

	返回链接列表（字符串）。这是同步函数，调用方如果在异步上下文应使用 `asyncio.to_thread`。
	"""
	url = "https://mp.weixin.qq.com/cgi-bin/appmsgpublish"
	params = {
		"sub": "list",
		"sub_action": "list_ex",
		"begin": 0,
		"count": count,
		"fakeid": fakeid,
		"token": wx_cfg.get("token"),
		"lang": "zh_CN",
		"f": "json",
		"ajax": 1,
	}
	headers = {
		"Cookie": wx_cfg.get("cookies_str", "") if wx_cfg else "",
		"User-Agent": wx_cfg.get("user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
	}
	Session.headers.update(headers)
	resp = Session.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
	try:
		data = resp.json()
	except Exception:
		return []

	results: List[str] = []
	data_list = [data] if isinstance(data, dict) else data
	for entry in data_list:
		publish_page = entry.get("publish_page")
		if not publish_page:
			continue
		try:
			page_obj = json.loads(publish_page)
		except Exception:
			continue
		for pub in page_obj.get("publish_list", []):
			try:
				info_obj = json.loads(pub.get("publish_info", "{}"))
			except Exception:
				continue
			for appmsg in info_obj.get("appmsgex", []):
				link = appmsg.get("link")
				if link:
					link = link.replace("\\/", "/").replace("\\\\/", "/")
					results.append(link)
	# 去重并保持顺序
	seen = set()
	uniq: List[str] = []
	for u in results:
		if u not in seen:
			seen.add(u)
			uniq.append(u)
	return uniq


def fetch_article_details(url: str, timeout: int = REQUEST_TIMEOUT) -> dict:
	"""同步获取单篇微信文章详情，返回字典（title, author, content, create_time, biz）。"""
	headers = {
		"Referer": "https://mp.weixin.qq.com/",
		"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
	}
	url = url.strip()
	Session.headers.update(headers)
	resp = Session.get(url, timeout=timeout, headers=headers)
	if resp.status_code != 200:
		return {"status": 0}
	resp.encoding = resp.apparent_encoding
	if re.search(r"当前环境异常", resp.text):
		return {"status": 0}
	html = resp.text
	soup = BeautifulSoup(html, "lxml")
	content = ""
	try:
		content_div = soup.find("div", class_="rich_media_content")
		content = format_wechat_content(content_div)
	except Exception:
		content = ""
	title = ""
	try:
		title = soup.find("h1", {"class": "rich_media_title", "id": "activity-name"}).get_text(strip=True)
	except Exception:
		t = soup.find("h1", class_="rich_media_title")
		title = t.get_text(strip=True) if t else ""
	author = ""
	try:
		author = soup.find("a", {"id": "js_name"}).get_text(strip=True)
	except Exception:
		author = ""
	biz = ""
	m = re.search(r'var biz\s*=\s*"(.*?)";', html)
	if m:
		biz = m.group(1).replace('" || "', '').replace('"', '')
	create_time = ""
	publish_dt, raw_time = _extract_publish_datetime(html)
	if publish_dt:
		create_time = publish_dt.strftime("%Y-%m-%d")
	elif raw_time:
		create_time = raw_time

	return {
		"status": 1,
		"content": content,
		"title": title,
		"author": author,
		"create_time": create_time,
		"biz": biz,
	}


def get_fakeid_by_name(wx_cfg: dict, kw: str) -> Optional[str]:
	"""根据公众号名称关键词获取公众号的 fakeid（同步函数）。"""
	url = "https://mp.weixin.qq.com/cgi-bin/searchbiz"
	params = {
		"action": "search_biz",
		"begin": 0,
		"count": 5,
		"query": kw,
		"token": wx_cfg.get("token"),
		"lang": "zh_CN",
		"f": "json",
		"ajax": "1",
	}
	headers = {
		"Cookie": wx_cfg.get("cookies_str", ""),
		"User-Agent": wx_cfg.get("user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"),
	}
	Session.headers.update(headers)
	resp = Session.get(url, params=params, timeout=REQUEST_TIMEOUT)
	try:
		data = resp.json()
	except Exception:
		return None

	try:
		fakeid = data["list"][0]["fakeid"]
		return fakeid
	except Exception:
		return None


async def fetch_html(url: str, timeout: int = REQUEST_TIMEOUT) -> str:
	"""Fetch HTML using requests in a thread to avoid blocking the event loop."""
	def _get():
		headers = {
			"User-Agent": (
				"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
				"(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
			),
			"Referer": "https://mp.weixin.qq.com/",
		}
		resp = requests.get(url, headers=headers, timeout=timeout)
		resp.raise_for_status()
		resp.encoding = resp.apparent_encoding
		return resp.text

	return await asyncio.to_thread(_get)


async def crawl_single_article(url: str, source_id: Optional[str] = None, source_name: Optional[str] = None, override_id: Optional[str] = None, delete_if_invalid: bool = False) -> Optional[CrawlItem]:
	html = await fetch_html(url)
	meta = parse_wechat_article(html)

	if meta.get("Error"):
		print(f"[WARN] Article error ({meta.get('Error')}), skipping: {url}")
		if delete_if_invalid and override_id and meta.get("Error") == "Content deleted":
			print(f"[INFO] Deleting invalid record from DB: {override_id}")
			await asyncio.to_thread(database.delete_record, override_id)
		return None

	content = meta.get("Content", "")
	title = meta.get("Title", "")
	
	# 确保 create_time 是 datetime 对象，且仅包含日期部分
	raw_time = meta.get("Time")
	if isinstance(raw_time, str):
		try:
			# 尝试解析字符串时间，兼容带时分秒和不带时分秒的格式
			clean_time = raw_time.strip()
			if len(clean_time) <= 10:
				dt = datetime.strptime(clean_time, "%Y-%m-%d")
			else:
				dt = datetime.strptime(clean_time, "%Y-%m-%d %H:%M:%S")
			create_time = dt.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
		except ValueError:
			# 如果解析失败，使用当前时间并归零
			create_time = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
	elif isinstance(raw_time, datetime):
		create_time = raw_time.replace(hour=0, minute=0, second=0, microsecond=0)
	else:
		create_time = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

	item_id = override_id or compute_sha256(url)

	exists = await asyncio.to_thread(database.record_exists, item_id, url)
	if exists:
		# Still return existing record shape but don't duplicate storage
		return None

	# Determine storage source_id: prefer provided source_id, otherwise default to 'wechat_single'
	store_source_id = source_id or "wechat_single"
	store_source_name = source_name or ""
	metadata = {
		"url": url,
		"source_id": store_source_id,
		"source_name": store_source_name,
		"title": title,
		"publish_time": create_time.strftime("%Y-%m-%d"),
		"attachments": None,
	}

	try:
		await asyncio.to_thread(database.store_document, item_id, content, metadata)
	except Exception as exc:
		print(f"[WARN] Failed to store wechat single article: {exc}")

	return CrawlItem(
		id=item_id,
		title=metadata["title"],
		content=content,
		url=url,
		publish_time=create_time,
		source="wechat_single",
		attachments=None,
		extra_meta=None,
	)


async def crawl_wechat_source(source_id: str) -> List[CrawlItem]:
	"""Crawl a wechat-configured source.

		支持两种配置方式：
			- `article_urls`: 直接提供文章链接列表
			- 提供 `biz` + `wx_cfg`（token/cookies/user_agent），通过 `get_article_list` 拉取最近文章

	按要求，`source_id` 建议以 `wechat_` 前缀命名。
	"""
	targets = []
	if source_id == "all":
		targets = list(WECHAT_SOURCES)
	else:
		tgt = next((s for s in WECHAT_SOURCES if s.get("id") == source_id), None)
		if not tgt:
			raise ValueError(f"Unknown wechat source id: {source_id}")
		targets = [tgt]

	results: List[CrawlItem] = []
	
	# 限制并发数，防止被微信封禁
	semaphore = asyncio.Semaphore(3)

	for src in targets:
		urls: List[str] = []
		# 优先支持 biz 模式
		biz = src.get("biz")
		# 优先使用 source 中配置的 wx_cfg，否则回退到 wechat.json 顶层的 session（WECHAT_SESSION）
		wx_cfg = src.get("wx_cfg") or WECHAT_SESSION or {}
		count = int(src.get("count", 5)) if src.get("count") else 5
		if biz:
			try:
				# get_article_list 是同步函数，放到线程池执行
				urls = await asyncio.to_thread(get_article_list, wx_cfg, biz, count)
			except Exception as exc:
				print(f"[WARN] failed to get article list for {src.get('id')}: {exc}")
				urls = []
		else:
			urls = src.get("article_urls") or []

		if not urls:
			print(f"[INFO] wechat source {src.get('id')} has no article urls; skip")
			continue

		# 定义并发任务包装器
		async def process_url(url: str):
			async with semaphore:
				try:
					return await crawl_single_article(url, source_id=src.get("id"), source_name=src.get('name'))
				except Exception as exc:
					print(f"[WARN] failed to crawl article {url}: {exc}")
					return None

		# 创建并执行任务
		tasks = [process_url(url) for url in urls]
		batch_results = await asyncio.gather(*tasks, return_exceptions=True)

		# 收集结果
		new_items_count = 0
		for res in batch_results:
			if isinstance(res, CrawlItem):
				results.append(res)
				new_items_count += 1
			elif isinstance(res, Exception):
				print(f"[WARN] task failed: {res}")

		print(f"\n[SUCCESS] Source '公众号：{src.get('name')}' crawled successfully. {new_items_count} new items added.")
	return results