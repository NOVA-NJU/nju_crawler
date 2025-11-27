"""
爬虫服务核心模块 (Crawler Services Core Module)

本模块实现了完整的网页爬虫功能，支持从配置的网站源抓取、解析和存储公告/文章数据。
主要功能包括：

核心功能：
- 异步网页抓取：使用curl_cffi库模拟浏览器行为，支持重试和超时机制
- 内容解析：支持HTML解析、PDF/Word文档解析、图片OCR识别
- 数据存储：SQLite数据库持久化存储，支持附件元数据
- 向量同步：可选同步到向量数据库以支持语义搜索

技术特性：
- 异步并发：使用asyncio实现高并发抓取，控制最大并发数避免被封
- 多格式支持：HTML文本、PDF文档、Word文档、图片OCR
- 智能解析：CSS选择器配置化，支持复杂页面结构
- 去重机制：基于内容SHA256哈希的去重，避免重复抓取
- 错误处理：完善的异常处理和日志记录

主要组件：
- fetch_html(): 异步网页抓取核心函数
- crawl_source(): 完整爬虫流程编排
- parse_detail_page(): 详情页内容解析
- 各种辅助函数：URL处理、时间解析、文件下载等

依赖库：
- curl_cffi: 异步HTTP客户端，浏览器伪装
- BeautifulSoup: HTML解析
- PyPDF2: PDF文本提取
- python-docx: Word文档解析
- pytesseract: OCR文字识别
- PIL: 图片处理

配置要求：
- 需要在config.py中配置目标网站信息
- OCR功能需要安装Tesseract
- 向量同步需要配置向量服务

使用示例：
    from .services import crawl_source
    items = await crawl_source("source_id")
"""

# 爬虫服务核心实现，包含抓取、解析、存储、同步等功能。
# 依赖众多第三方库，支持异步、OCR、PDF/Word解析、向量同步等。

import asyncio  # 异步任务调度
import base64   # Base64编码
import hashlib  # 用于生成唯一ID
import io       # 字节流处理
import json     # 附件序列化
import os       # 环境变量与路径
import re       # 正则表达式
from datetime import datetime, timezone  # 时间处理，支持UTC
from typing import List, Optional  # 类型注解
from urllib.parse import parse_qs, urljoin, urlparse  # URL处理


from curl_cffi import requests as curl_requests  # 高性能异步HTTP库，支持浏览器伪装
from PyPDF2 import PdfReader  # PDF解析
from bs4 import BeautifulSoup  # HTML解析
from docx import Document  # Word文档解析
from PIL import Image  # 图片处理
import pytesseract  # OCR文字识别


# 导入配置项和数据模型
from .config import (
    DETAIL_SELECTORS,      # 详情页选择器配置
    MAX_RETRIES,           # 最大重试次数
    REQUEST_TIMEOUT,       # 请求超时时间
    TARGET_SOURCES,        # 目标网站源配置
    TESSDATA_DIR,          # OCR数据目录
    TESSERACT_CMD,         # OCR命令路径
    VECTOR_SYNC_ENABLED,   # 是否同步到向量库
)
from .models import Attachments, CrawlItem  # 附件和爬取结果数据结构
from storage import database  # 统一数据库操作


# 初始化数据库，确保表结构存在
database.initialize()


# 配置OCR环境变量和命令路径
os.environ["TESSDATA_PREFIX"] = TESSDATA_DIR
pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD


# 列表页翻页URL正则匹配
PAGINATION_PATTERN = re.compile(r"(list)(\d+)(\.htm)$", re.IGNORECASE)
PARAGRAPH_CLOSE_PATTERN = re.compile(r"</p\s*>", re.IGNORECASE)



# 全局异步HTTP会话，模拟Chrome浏览器
ASYNC_HTTP = curl_requests.AsyncSession(impersonate="chrome120", verify=False)



async def fetch_html(
    url: str,
    headers: dict,
    timeout: int = REQUEST_TIMEOUT,
    retries: int = MAX_RETRIES,
) -> str:
    """
    异步获取网页HTML内容，带重试和退避机制。
    参数：url 网页地址，headers 请求头，timeout 超时，retries 最大重试。
    失败时抛出RuntimeError。
    """
    for attempt in range(retries):
        try:
            response = await ASYNC_HTTP.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            return response.text
        except Exception as exc:
            if attempt == retries - 1:
                raise RuntimeError(f"Failed to fetch {url} after {retries} attempts.") from exc
            wait_seconds = 1 + attempt
            print(f"[WARN] attempt {attempt + 1} for {url} failed: {exc}; retry in {wait_seconds}s.")
            await asyncio.sleep(wait_seconds)
    raise RuntimeError(f"Failed to fetch {url}")



async def download_binary(
    url: str,
    headers: dict,
    timeout: int = REQUEST_TIMEOUT,
    retries: int = MAX_RETRIES,
) -> Optional[bytes]:
    """
    异步下载二进制文件（图片、PDF、Word等），带重试。
    参数同fetch_html。失败时返回None。
    """
    for attempt in range(retries):
        try:
            response = await ASYNC_HTTP.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            return response.content
        except Exception as exc:
            if attempt == retries - 1:
                print(f"[WARN] failed to download binary {url}: {exc}")
                return None
            wait_seconds = 1 + attempt
            print(f"[WARN] download attempt {attempt + 1} for {url} failed: {exc}; retry in {wait_seconds}s.")
            await asyncio.sleep(wait_seconds)
    return None



def normalize_url(base_url: str, url_el) -> Optional[str]:
    """
    将相对、协议相对或绝对URL属性转为绝对URL。
    参数：base_url 基准域名，url_el 可能为标签或字符串。
    """
    href = None
    if isinstance(url_el, str):
        href = url_el.strip()
    elif url_el is not None:
        href = (url_el.get("href") or url_el.get("src") or "").strip()

    if not href:
        return None

    parsed = urlparse(href)
    if parsed.scheme:
        return href
    if href.startswith("//"):
        base_scheme = urlparse(base_url).scheme or "https"
        return f"{base_scheme}:{href}"
    return urljoin(base_url, href)



def parse_list(html: str, selectors: dict, base_url: str) -> List[dict]:
    """
    用CSS选择器解析列表页，提取每条公告/文章的基本信息。
    返回：包含title、date、url、type的字典列表。
    """
    html_with_newlines = PARAGRAPH_CLOSE_PATTERN.sub("</p>\n", html)
    soup = BeautifulSoup(html_with_newlines, "lxml")
    items = soup.select(selectors["item_container"])
    results = []
    for item in items:
        date_el = item.select_one(selectors["date"])
        title_el = item.select_one(selectors["title"])
        
        # 处理 URL 选择器为空的情况（链接在容器本身）
        if not selectors.get("url"):
            url_el = item
        else:
            url_el = item.select_one(selectors["url"])

        type_selector = selectors.get("type")
        type_el = item.select_one(type_selector) if type_selector else None

        full_url = normalize_url(base_url, url_el)

        results.append(
            {
                "title": title_el.get_text(strip=True) if title_el else None,
                "date": date_el.get_text(strip=True) if date_el else None,
                "url": full_url,
                "type": type_el.get_text(strip=True) if type_el else None,
            }
        )
    return results



def build_paginated_urls(list_url: str, max_pages: int) -> List[str]:
    """
    生成所有翻页的列表URL（如list1.htm、list2.htm...），支持最大页数。
    """
    if max_pages <= 1:
        return [list_url]

    urls = [list_url]
    match = PAGINATION_PATTERN.search(list_url)
    for page in range(2, max_pages + 1):
        if match:
            prefix = list_url[: match.start()]
            suffix = match.group(3)
            urls.append(f"{prefix}list{page}{suffix}")
        else:
            separator = "&" if "?" in list_url else "?"
            urls.append(f"{list_url}{separator}page={page}")
    return urls



def parse_publish_time(date_str: Optional[str]) -> datetime:
    """
    尽力解析日期字符串，支持多种格式，失败则返回当前UTC时间（带时区）。
    """
    if not date_str:
        return datetime.now(timezone.utc)

    # 确保转换为字符串，处理 API 返回整数的情况
    date_str = str(date_str).strip()

    # 处理纯数字时间戳 (如 1618379815000)
    if date_str.isdigit():
        try:
            # 13位为毫秒级时间戳
            if len(date_str) == 13:
                ts = float(date_str) / 1000.0
                return datetime.fromtimestamp(ts, tz=timezone.utc)
            # 10位为秒级时间戳 (排除 20250101 这种8位日期)
            elif len(date_str) == 10:
                ts = float(date_str)
                return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            pass

    # 特殊格式处理：DayYear-Month (e.g., "252025-11" -> "2025-11-25")
    # 这种格式出现在信息管理学院等网站
    special_match = re.match(r"^(\d{1,2})(\d{4}-\d{2})$", date_str)
    if special_match:
        day, year_month = special_match.groups()
        date_str = f"{year_month}-{day.zfill(2)}"

    # 特殊格式处理：Day/Year/Month (e.g., "07/2023/04" -> "2023-04-07")
    dym_match = re.match(r"^(\d{1,2})/(\d{4})/(\d{1,2})$", date_str)
    if dym_match:
        p1, year, p2 = dym_match.groups()
        # 假设格式为 Day/Year/Month
        date_str = f"{year}-{p2.zfill(2)}-{p1.zfill(2)}"

    # 特殊格式处理：Month-Day/ Year (e.g., "11-13/ 2025" -> "2025-11-13")
    # 这种格式出现在马克思主义学院等网站
    mdy_ws_year = re.match(r"^(\d{1,2})[-/.](\d{1,2})\s*/\s*(\d{4})$", date_str)
    if mdy_ws_year:
        month = int(mdy_ws_year.group(1))
        day = int(mdy_ws_year.group(2))
        year = int(mdy_ws_year.group(3))
        try:
            return datetime(year, month, day).replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    # 新增：仅有月日的情况（如 "11-25" 或 "11/25" 或 "11.25"）
    md_match = re.match(r"^(\d{1,2})[-/.](\d{1,2})$", date_str)
    if md_match:
        month = int(md_match.group(1))
        day = int(md_match.group(2))
        now = datetime.now(timezone.utc)
        # 判断月日与当前日期关系
        if (month < now.month) or (month == now.month and day <= now.day):
            year = now.year
        else:
            year = now.year - 1
        date_str_full = f"{year}-{month:02d}-{day:02d}"
        try:
            return datetime.strptime(date_str_full, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"):
        try:
            dt = datetime.strptime(date_str, fmt)
            # 补充时区信息
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    print(f"[WARN] Failed to parse date string: {date_str}")
    return datetime.now(timezone.utc)



def extract_text_content(soup: BeautifulSoup, selector_cfg: Optional[dict]) -> str:
    """
    按配置提取详情页正文内容。
    支持多节点聚合，返回纯文本。
    """
    if not selector_cfg:
        return ""
    container = soup.select_one(selector_cfg.get("item_container", ""))
    if not container:
        return ""
    content_selector = selector_cfg.get("content")
    if content_selector:
        # 优先处理 <p> 标签分段
        p_nodes = container.select("p")
        if p_nodes:
            text_chunks = [p.get_text(" ", strip=True) for p in p_nodes if p]
        else:
            nodes = container.select(content_selector)
            text_chunks = [node.get_text(" ", strip=True) for node in nodes if node]
    else:
        text_chunks = [container.get_text(" ", strip=True)]
    return "\n".join(filter(None, text_chunks))



async def perform_ocr_from_url(image_url: str, headers: dict) -> str:
    """
    下载图片并用pytesseract进行OCR识别。
    仅当OCR命令配置有效时才执行。
    """
    if not TESSERACT_CMD:
        return ""

    image_bytes = await download_binary(image_url, headers)
    if not image_bytes:
        return ""

    def _ocr() -> str:
        try:
            pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
            config_parts = []
            if TESSDATA_DIR:
                config_parts.append(f'--tessdata-dir "{TESSDATA_DIR}"')
            config = " ".join(config_parts) or None
            with Image.open(io.BytesIO(image_bytes)) as img:
                text = pytesseract.image_to_string(img, lang="chi_sim+eng", config=config)
            return text.strip()
        except (pytesseract.TesseractError, OSError) as exc:
            print(f"[WARN] OCR failed for {image_url}: {exc}")
            return ""

    return await asyncio.to_thread(_ocr)


async def extract_image_texts(
    soup: BeautifulSoup, selector_cfg: Optional[dict], base_url: str, headers: dict
) -> List[str]:
    """Collect OCR text for every image that matches the configured selector."""
    if not selector_cfg:
        return []
    container = soup.select_one(selector_cfg.get("item_container", ""))
    if not container:
        return []
    image_selector = selector_cfg.get("images")
    if not image_selector:
        return []
    texts: List[str] = []
    for img in container.select(image_selector):
        src = normalize_url(base_url, img.get("src"))
        if not src:
            continue
        ocr_text = await perform_ocr_from_url(src, headers)
        if ocr_text:
            texts.append(ocr_text)
    return texts


def parse_pdf_bytes(file_bytes: bytes) -> str:
    """Return concatenated text for all PDF pages (skipping empty extractions)."""
    reader = PdfReader(io.BytesIO(file_bytes))
    texts = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(filter(None, texts))


def parse_docx_bytes(file_bytes: bytes) -> str:
    """Join all paragraph texts from a DOCX binary payload."""
    document = Document(io.BytesIO(file_bytes))
    return "\n".join(p.text for p in document.paragraphs if p.text)


async def extract_file_texts(
    soup: BeautifulSoup,
    selector_cfg: Optional[dict],
    base_url: str,
    headers: dict,
    allowed_ext: tuple,
) -> List[Attachments]:
    """Download and parse attachment texts for the allowed extensions."""
    if not selector_cfg:
        return []
    container = soup.select_one(selector_cfg.get("item_container", ""))
    if not container:
        return []
    file_selector = selector_cfg.get("files")
    if not file_selector:
        return []

    attachments: List[Attachments] = []
    for link in container.select(file_selector):
        file_url = normalize_url(base_url, link)
        if not file_url:
            continue
        if not file_url.lower().endswith(allowed_ext):
            continue
        filename = link.get_text(strip=True) or "attachment"
        binary = await download_binary(file_url, headers)
        if not binary:
            continue

        if file_url.lower().endswith(".pdf"):
            text = await asyncio.to_thread(parse_pdf_bytes, binary)
            mime = "application/pdf"
        elif file_url.lower().endswith(".docx"):
            text = await asyncio.to_thread(parse_docx_bytes, binary)
            mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        else:
            continue

        attachments.append(Attachments(url=file_url, filename=filename, mime_type=mime, text=text))
    return attachments


async def extract_embedded_pdf_attachment(
    soup: BeautifulSoup, selector_cfg: Optional[dict], base_url: str, headers: dict
) -> List[Attachments]:
    """Handle sites that embed PDFs via viewer iframes instead of direct links."""
    if not selector_cfg:
        return []
    viewer_selector = selector_cfg.get("viewer")
    if not viewer_selector:
        return []
    iframe = soup.select_one(viewer_selector)
    if not iframe:
        return []
    src = iframe.get("src")
    if not src:
        return []
    full_src = normalize_url(base_url, src)
    if not full_src:
        return []
    parsed = urlparse(full_src)
    file_param = parse_qs(parsed.query).get("file")
    if not file_param:
        return []
    pdf_url = normalize_url(base_url, file_param[0])
    if not pdf_url:
        return []
    binary = await download_binary(pdf_url, headers)
    if not binary:
        return []
    text = await asyncio.to_thread(parse_pdf_bytes, binary)
    return [
        Attachments(
            url=pdf_url,
            filename=pdf_url.split("/")[-1],
            mime_type="application/pdf",
            text=text,
        )
    ]


async def extract_script_embedded_pdf_attachments(
    soup: BeautifulSoup, selector_cfg: Optional[dict], base_url: str, headers: dict
) -> List[Attachments]:
    """Handle sites that embed PDFs via script"""
    if not selector_cfg:
        return []
    script_selector = selector_cfg.get("download_link")
    if not script_selector:
        return []
    scripts = soup.select(script_selector)
    if not scripts:
        return []
    urls: List[str] = []
    for s in scripts:
        content = s.string or s.get_text() or ""
        m = re.search(r"showVsbpdfIframe\([\"']([^\"']+?\.pdf)[\"']", content)
        if m:
            url = normalize_url(base_url, m.group(1))
            if url:
                urls.append(url)
    if not urls:
        return []
    attachments: List[Attachments] = []
    for url in urls:
        binary = await download_binary(url, headers)
        if not binary:
            attachments.append(
                Attachments(url=url, filename=url.split("/")[-1], mime_type="application/pdf", text="")
            )
            continue
        text = await asyncio.to_thread(parse_pdf_bytes, binary)
        attachments.append(
            Attachments(url=url, filename=url.split("/")[-1], mime_type="application/pdf", text=text)
        )
    return attachments


def aggregate_content(text: str, image_texts: List[str], attachment_texts: List[str]) -> str:
    """Merge base content, OCR outputs, and attachment snippets into one blob."""
    chunks = [chunk for chunk in [text] if chunk]
    if image_texts:
        chunks.append("\n".join(image_texts))
    if attachment_texts:
        chunks.append("\n".join(attachment_texts))
    return "\n\n".join(chunks)


def build_attachment_text_snippet(attachment: Attachments) -> str:
    """Render human-friendly markers before attachment texts."""
    title = attachment.filename or attachment.url
    return f"【附件：{title}】\n{attachment.text or ''}"


def compute_sha256(*segments: Optional[str]) -> str:
    """Generate a deterministic identifier from the provided text segments."""
    payload = "\n".join(segment or "" for segment in segments)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_wechat_article(html: str) -> tuple[str, List[Attachments]]:
    """Parse WeChat official account article."""
    soup = BeautifulSoup(html, "lxml")
    
    if "当前环境异常" in html:
        return "Error: WeChat environment exception (verification required)", []

    content_div = soup.find("div", class_="rich_media_content")
    content = content_div.get_text("\n", strip=True) if content_div else ""
    
    title = soup.find("h1", class_="rich_media_title")
    title_text = title.get_text(strip=True) if title else ""
    
    author = soup.find("a", id="js_name")
    author_text = author.get_text(strip=True) if author else ""
    
    create_time = ""
    match = re.search(r"var createTime = '(.*?)';", html)
    if match:
        try:
            ts = float(match.group(1))
            create_time = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            create_time = match.group(1)

    # Biz (FakeID)
    biz = ""
    biz_match = re.search(r'var biz\s*=\s*"(.*?)";', html)
    if biz_match:
        biz = biz_match.group(1).replace('" || "', '').replace('"', '')

    meta = []
    if title_text: meta.append(f"Title: {title_text}")
    if author_text: meta.append(f"Author: {author_text}")
    if create_time: meta.append(f"Time: {create_time}")
    if biz: meta.append(f"Biz: {biz}")
    
    full_text = "\n".join(meta) + "\n\n" + content
    return full_text, []


def resolve_detail_selector(detail_url: str) -> Optional[dict]:
    """Find the detail selector config that matches the given URL hostname/path."""
    if not DETAIL_SELECTORS:
        return None

    parsed_detail = urlparse(detail_url)
    detail_host = parsed_detail.netloc
    detail_path = parsed_detail.path or "/"

    for cfg in DETAIL_SELECTORS:
        cfg_url = cfg.get("base_url") or ""
        parsed_cfg = urlparse(cfg_url)
        cfg_host = parsed_cfg.netloc or cfg_url

        if detail_host and cfg_host and detail_host != cfg_host:
            continue

        cfg_path = parsed_cfg.path or ""
        if cfg_path and not detail_path.startswith(cfg_path):
            continue

        return cfg

    return DETAIL_SELECTORS[0]


async def parse_detail_page(html: str, detail_url: str, headers: dict) -> tuple[str, List[Attachments]]:
    """Parse a detail page and return aggregated text plus attachment metadata."""
    if "mp.weixin.qq.com" in detail_url:
        return parse_wechat_article(html)

    soup = BeautifulSoup(html, "lxml")
    selector_cfg = resolve_detail_selector(detail_url) or {}

    text_content = extract_text_content(soup, selector_cfg.get("text_selector"))
    image_texts = await extract_image_texts(soup, selector_cfg.get("img_selector"), detail_url, headers)
    pdf_attachments = await extract_file_texts(
        soup, selector_cfg.get("pdf_selector"), detail_url, headers, allowed_ext=(".pdf",)
    )
    doc_attachments = await extract_file_texts(
        soup, selector_cfg.get("doc_selector"), detail_url, headers, allowed_ext=(".docx",)
    )
    embedded_pdf = await extract_embedded_pdf_attachment(
        soup, selector_cfg.get("embedded_pdf_selector"), detail_url, headers
    )
    embedded_pdf_script = await extract_script_embedded_pdf_attachments(
        soup, selector_cfg.get("embedded_pdf_selector"), detail_url, headers
    )

    attachments = pdf_attachments + doc_attachments + embedded_pdf + embedded_pdf_script
    attachment_texts = [build_attachment_text_snippet(att) for att in attachments if att.text]
    content = aggregate_content(text_content, image_texts, attachment_texts)
    return content, attachments


MAX_CONCURRENT_DETAIL_REQUESTS = 5


def get_max_page(html: str) -> int:
    """
    从HTML中解析最大页码。
    优先查找 .p_no 元素，提取其中的数字。
    """
    soup = BeautifulSoup(html, "lxml")
    # 查找所有 .p_no 元素
    page_nodes = soup.select(".p_no")
    if not page_nodes:
        # 尝试查找常见的翻页容器
        page_nodes = soup.select(".pagination a, .pages a, .pb_sys_common a")
    
    max_page = 1
    for node in page_nodes:
        text = node.get_text(strip=True)
        # 提取数字
        match = re.search(r"(\d+)", text)
        if match:
            try:
                page_num = int(match.group(1))
                if page_num > max_page:
                    max_page = page_num
            except ValueError:
                continue
    return max_page


async def crawl_source(source_id: str) -> List[CrawlItem]:
    """Crawl a configured list page and return normalized CrawlItem records."""
    source_cfg = next((src for src in TARGET_SOURCES if src["id"] == source_id), None)
    if not source_cfg:
        raise ValueError(f"Unknown source id: {source_id}")

    max_pages = int(source_cfg.get("max_pages", 1))
    pagination_mode = source_cfg.get("pagination_mode", "forward")
    entries: List[dict] = []

    # 分支处理：API 模式 vs 静态 HTML 模式
    if pagination_mode == "api" or source_cfg.get("type") == "api":
        api_url = source_cfg.get("api_url")
        base_payload = source_cfg.get("payload", {})
        
        for page in range(1, max_pages + 1):
            # 构造当前页的 payload
            current_payload = base_payload.copy()
            current_payload["pageno"] = str(page)
            current_payload["hasPage"] = "true" # 确保包含此参数
            
            try:
                json_data = await fetch_api(api_url, current_payload, source_cfg["headers"])
                page_entries = parse_api_response(json_data, source_cfg["selectors"], source_cfg["base_url"])
                if not page_entries:
                    print(f"[INFO] API page {page} returned no entries. Stopping pagination.")
                    break
                entries.extend(page_entries)
            except RuntimeError as exc:
                print(f"[WARN] skip API page {page}: {exc}")
                continue
    elif pagination_mode == "reverse":
        # Reverse 模式：先获取第一页（通常是最新页），解析最大页码，然后倒序生成 URL
        list_url = source_cfg["list_url"]
        try:
            # 获取第一页 HTML
            first_page_html = await fetch_html(list_url, source_cfg["headers"])
            # 解析第一页条目
            first_page_entries = parse_list(first_page_html, source_cfg["selectors"], source_cfg["base_url"])
            entries.extend(first_page_entries)
            
            # 获取最大页码
            max_p = get_max_page(first_page_html)
            print(f"[INFO] Detected max page for {source_id}: {max_p}")
            
            # 生成后续页码 URL (从 max_p - 1 倒序抓取)
            # 假设 URL 模式: base/name.ext -> base/name/{page}.ext
            # 例如: xwdt.htm -> xwdt/5.htm, xwdt/4.htm ...
            if max_pages > 1 and max_p > 1:
                base_name, ext = list_url.rsplit('.', 1)
                
                # 计算需要抓取的页数，受配置的 max_pages 限制
                pages_to_crawl = min(max_pages - 1, max_p - 1)
                
                for i in range(pages_to_crawl):
                    page_num = max_p - 1 - i
                    if page_num < 1:
                        break
                    
                    # 构造 URL: .../xwdt/{page_num}.htm
                    next_url = f"{base_name}/{page_num}.{ext}"
                    try:
                        html = await fetch_html(next_url, source_cfg["headers"])
                        page_entries = parse_list(html, source_cfg["selectors"], source_cfg["base_url"])
                        if page_entries:
                            entries.extend(page_entries)
                        else:
                            print(f"[INFO] Page {page_num} returned no entries.")
                    except RuntimeError as exc:
                        print(f"[WARN] skip page {next_url}: {exc}")
                        continue

        except RuntimeError as exc:
            print(f"[WARN] Failed to fetch initial list page {list_url}: {exc}")

    else:
        # Forward 模式 (默认)
        list_urls = build_paginated_urls(source_cfg["list_url"], max_pages)
        for page_number, list_url in enumerate(list_urls, start=1):
            try:
                list_html = await fetch_html(list_url, source_cfg["headers"])
            except RuntimeError as exc:
                print(f"[WARN] skip list page {list_url}: {exc}")
                continue
            page_entries = parse_list(list_html, source_cfg["selectors"], source_cfg["base_url"])
            if not page_entries:
                print(f"[INFO] list page {page_number} returned no entries. Stopping pagination.")
                break
            entries.extend(page_entries)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_DETAIL_REQUESTS)

    async def process_entry(entry: dict) -> Optional[CrawlItem]:
        detail_url = entry.get("url")
        if not detail_url:
            return None
        item_id = compute_sha256((entry.get("title") or "") + (detail_url or ""), detail_url)
        exists = await asyncio.to_thread(database.record_exists, item_id)
        if exists:
            return None
        try:
            async with semaphore:
                # 动态调整 Headers (如移除不匹配的 Host)
                req_headers = source_cfg["headers"].copy()
                target_host = urlparse(detail_url).netloc
                cfg_host = req_headers.get("host") or req_headers.get("Host")
                if cfg_host and cfg_host != target_host:
                    req_headers.pop("host", None)
                    req_headers.pop("Host", None)

                detail_html = await fetch_html(detail_url, req_headers)
            
            # 使用 detail_url 作为 base_url 以正确解析相对路径
            content, attachments = await parse_detail_page(detail_html, detail_url, req_headers)
            content = content or ""
        except RuntimeError as exc:
            print(f"[WARN] skip detail {detail_url}: {exc}")
            # 详情页不可访问时，直接存储列表页字段
            content = "详情页不可访问"
            attachments = None

        publish_time = parse_publish_time(entry.get("date"))

        attachments_payload = None
        if attachments:
            attachment_dicts = []
            for attachment in attachments:
                data = attachment.dict()
                data["url"] = str(data.get("url") or "")
                attachment_dicts.append(data)
            attachments_payload = json.dumps(attachment_dicts, ensure_ascii=False)

        metadata = {
            "url": detail_url,
            "source_id": source_cfg["id"],
            "source_name": source_cfg["name"],
            "title": entry.get("title"),
            "publish_time": publish_time.strftime("%Y-%m-%d"),
            "attachments": attachments_payload,
        }
        # 本地存储文档内容及元数据
        try:
            await asyncio.to_thread(database.store_document, item_id, content, metadata)
        except Exception as exc:
            print(f"[WARN] Failed to store document {item_id} in local SQLite: {exc}")

        return CrawlItem(
            id=item_id,
            title=entry.get("title") or "",
            content=content,
            url=detail_url,
            publish_time=publish_time,
            source=source_cfg["name"],
            attachments=attachments or None,
            extra_meta={"category": entry.get("type")},
        )

    tasks = [process_entry(entry) for entry in entries]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    crawl_items: List[CrawlItem] = []
    for result in results:
        if isinstance(result, Exception):
            print(f"[WARN] detail task failed: {result}")
            continue
        if result:
            crawl_items.append(result)

    # 终端显示提醒
    if crawl_items:
        print(f"\n[SUCCESS] Source '{source_cfg['name']}' crawled successfully. {len(crawl_items)} new items added.")
    else:
        print(f"\n[INFO] Source '{source_cfg['name']}' crawled. No new items found.")

    return crawl_items


def fetch_detail(parsed_lists, headers):
    raise NotImplementedError("fetch_detail is superseded by crawl_source.")

def base64_encode(s):
    """
    辅助函数: 将字符串转换为 Base64 编码。
    """
    return base64.b64encode(str(s).encode('utf-8')).decode('utf-8')


async def fetch_api(
    url: str,
    payload: dict,
    headers: dict,
    timeout: int = REQUEST_TIMEOUT,
    retries: int = MAX_RETRIES,
) -> dict:
    """
    异步获取API JSON数据，带重试机制。
    """
    # 对 payload 中的所有值进行 Base64 编码
    encoded_data = {k: base64_encode(v) for k, v in payload.items()}
    
    # 确保 headers 中包含 Content-Type
    if "Content-Type" not in headers:
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"

    for attempt in range(retries):
        try:
            response = await ASYNC_HTTP.post(url, data=encoded_data, headers=headers, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            if attempt == retries - 1:
                raise RuntimeError(f"Failed to fetch API {url} after {retries} attempts.") from exc
            wait_seconds = 1 + attempt
            print(f"[WARN] API attempt {attempt + 1} for {url} failed: {exc}; retry in {wait_seconds}s.")
            await asyncio.sleep(wait_seconds)
    raise RuntimeError(f"Failed to fetch API {url}")


def parse_api_response(json_data: dict, selectors: dict, base_url: str) -> List[dict]:
    """
    解析API返回的JSON数据。
    selectors 映射关系:
    - item_container: 列表数据在JSON中的键名 (如 "infolist")
    - title: 标题键名
    - date: 日期键名
    - url: URL键名
    """
    list_key = selectors.get("item_container", "infolist")
    items = json_data.get(list_key, [])
    if not items:
        return []

    results = []
    for item in items:
        title_key = selectors.get("title", "title")
        date_key = selectors.get("date", "releasetime")
        url_key = selectors.get("url", "url")
        
        raw_url = item.get(url_key)
        full_url = normalize_url(base_url, raw_url)

        results.append({
            "title": item.get(title_key),
            "date": item.get(date_key),
            "url": full_url,
            "type": None # API通常不直接返回类型，或者需要额外配置
        })
    return results
