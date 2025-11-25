import asyncio
import json
import os
import sys
from curl_cffi import requests as curl_requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

# 默认测试配置
DEFAULT_CONFIG_FILE = "config/sources/sxy.json"
DEFAULT_SOURCE_ID = "sxy_zxdt"

async def fetch_html(url: str, headers: dict = None):
    print(f"正在抓取: {url} ...")
    async with curl_requests.AsyncSession(impersonate="chrome120", headers=headers) as session:
        response = await session.get(url)
        print(f"状态码: {response.status_code}")
        return response.text

def test_list_page(html: str, selectors: dict, base_url: str):
    print("\n--- 测试列表页解析 ---")
    soup = BeautifulSoup(html, "lxml")
    items = soup.select(selectors["item_container"])
    print(f"找到 {len(items)} 个条目")

    for i, item in enumerate(items[:5]): # 只打印前5个
        print(f"\n[条目 {i+1}]")
        
        # 提取标题
        title_el = item.select_one(selectors["title"])
        title = title_el.get_text(strip=True) if title_el else "未找到"
        print(f"标题: {title}")

        # 提取日期
        date_el = item.select_one(selectors["date"])
        date = date_el.get_text(strip=True) if date_el else "未找到"
        print(f"日期: {date}")

        # 提取链接
        url_el = item.select_one(selectors["url"])
        link = url_el.get('href') if url_el else "未找到"
        if link != "未找到":
             link = urljoin(base_url, link)
        print(f"链接: {link}")
        
        # 返回第一个链接用于详情页测试
        if i == 0 and link != "未找到":
            return link
    return None

def test_detail_page(html: str, detail_selectors: list, base_url: str):
    print("\n--- 测试详情页解析 ---")
    soup = BeautifulSoup(html, "lxml")
    
    # 查找匹配的详情页配置
    selector_cfg = None
    for cfg in detail_selectors:
        if cfg.get("base_url") in base_url:
            selector_cfg = cfg
            break
    
    if not selector_cfg:
        print(f"未找到匹配 base_url '{base_url}' 的详情页配置")
        return

    # 提取正文
    if "text_selector" in selector_cfg:
        content_sel = selector_cfg["text_selector"].get("content")
        container_sel = selector_cfg["text_selector"].get("item_container")
        
        container = soup.select_one(container_sel) if container_sel else soup
        if container:
            content_el = container.select_one(content_sel)
            if content_el:
                text = content_el.get_text(strip=True)[:100] + "..." 
                print(f"正文预览: {text}")
            else:
                print("未找到正文内容元素")
        else:
            print("未找到正文容器")

    # 提取发布者
    if "meta_selector" in selector_cfg:
        pub_sel = selector_cfg["meta_selector"].get("publisher")
        if pub_sel:
            pub_el = soup.select_one(pub_sel)
            if pub_el:
                print(f"发布信息: {pub_el.get_text(strip=True)}")
            else:
                print("未找到发布信息")

    # 提取图片
    if "img_selector" in selector_cfg:
        img_sel = selector_cfg["img_selector"].get("images")
        if img_sel:
            images = soup.select(img_sel)
            print(f"找到 {len(images)} 张图片")
            for i, img in enumerate(images[:5]):
                src = img.get('src')
                print(f"图片 {i+1}: {src}")

async def main():
    # 获取命令行参数或使用默认值
    config_file = DEFAULT_CONFIG_FILE
    source_id = DEFAULT_SOURCE_ID
    
    if len(sys.argv) > 1:
        config_file = sys.argv[1]
    if len(sys.argv) > 2:
        source_id = sys.argv[2]

    print(f"加载配置文件: {config_file}")
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            config_data = json.load(f)
    except FileNotFoundError:
        print(f"错误: 找不到文件 {config_file}")
        return
    except json.JSONDecodeError:
        print(f"错误: 文件 {config_file} 不是有效的 JSON")
        return

    # 查找指定的 source
    target_source = None
    for source in config_data.get("sources", []):
        if source["id"] == source_id:
            target_source = source
            break
    
    if not target_source:
        print(f"错误: 在配置文件中未找到 ID 为 '{source_id}' 的源")
        print("可用源 ID:")
        for source in config_data.get("sources", []):
            print(f" - {source['id']}")
        return

    print(f"开始测试源: {target_source['name']} ({target_source['id']})")
    
    # 测试列表页
    list_url = target_source["list_url"]
    headers = target_source.get("headers", {})
    
    html = await fetch_html(list_url, headers)
    first_link = test_list_page(html, target_source["selectors"], target_source["base_url"])

    # 测试详情页 (如果列表页解析成功且有链接)
    if first_link:
        detail_html = await fetch_html(first_link, headers)
        test_detail_page(detail_html, config_data.get("detail_selectors", []), target_source["base_url"])

if __name__ == "__main__":
    asyncio.run(main())
