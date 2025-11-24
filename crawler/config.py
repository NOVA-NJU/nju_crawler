"""
爬虫模块的统一配置文件。
本文件集中管理所有爬虫相关参数，包括目标网站、数据库路径、定时任务、OCR、环境变量等。
所有配置项均可通过环境变量覆盖，方便部署和调试。
"""
from __future__ import annotations  # 兼容未来类型注解语法

import os  # 用于读取环境变量，实现灵活配置


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

TARGET_SOURCES = [
    {
        "id": "bksy_ggtz",  # 源唯一标识
        "name": "本科生院-公告通知",  # 源名称
        "base_url": "https://jw.nju.edu.cn",  # 网站主域名
        "list_url": "https://jw.nju.edu.cn/ggtz/list1.htm",  # 列表页入口
        "max_pages": 5,  # 最大翻页数
        "headers": {  # 请求头，模拟浏览器
            "USER_AGENT": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36 Edg/142.0.0.0"
            ),
            "host": "jw.nju.edu.cn",
        },
        "selectors": {  # CSS选择器，定位列表页各字段
            "item_container": "#wp_news_w6 li.news",  # 每条新闻容器
            "date": ".news_meta",  # 发布时间
            "title": ".news_title a",  # 标题
            "url": ".news_title a",  # 详情页链接
            "type": ".wjj .lj",  # 类型标签
        },
        "detail_selector": "#d-container .wp_articlecontent p",  # 详情页正文选择器
    },
    {
        "id": "bksy_ggxxw",
        "name": "本科生院-公告信息网",
        "base_url": "https://jw.nju.edu.cn",
        "list_url": "https://jw.nju.edu.cn/24774/list1.htm",
        "max_pages": 1,
        "headers": {  # 请求头，模拟浏览器
            "USER_AGENT": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36 Edg/142.0.0.0"
            ),
            "host": "jw.nju.edu.cn",
        },
        "selectors": {  # CSS选择器，定位列表页各字段
            "item_container": "#wp_news_w6 li.news",  # 每条新闻容器
            "date": ".news_meta",  # 发布时间
            "title": ".news_title a",  # 标题
            "url": ".news_title a",  # 详情页链接
        },
    }
]

DETAIL_SELECTORS = [
    {
        "base_url": "https://jw.nju.edu.cn",
        "meta_selector": {
            "item_container": "#d-container",
            "publisher": ".arti_publisher",
            "views": ".arti_views",
        },
        "text_selector": {
            "item_container": "#d-container",
            "content": ".wp_articlecontent",
        },
        "img_selector": {
            "item_container": "#d-container",
            "images": ".wp_articlecontent img[src]",
        },
        "pdf_selector": {
            "item_container": "#d-container",
            "files": ".wp_articlecontent a[href$=\".pdf\"]",
            "name": ".wp_articlecontent a[href$=\".pdf\"] span",
        },
        "doc_selector": {
            "item_container": "#d-container",
            "files": ".wp_articlecontent a[href$=\".doc\"], .wp_articlecontent a[href$=\".docx\"]",
            "name": ".wp_articlecontent a[href$=\".doc\"], .wp_articlecontent a[href$=\".docx\"]",
        },
        "embedded_pdf_selector": {
            "item_container": "#d-container",
            "viewer": ".wp_articlecontent iframe.wp_pdf_player",
            "download_link": ".wp_articlecontent img[src$=\"icon_pdf.gif\"] + a",
        },
    },
    # 如有其他 base_url，可继续添加
    # {
    #     "base_url": "https://example.com",
    #     "meta_selector": {...},
    #     ...
    # },
]
