import asyncio
import json
import os
import sys
import base64
from curl_cffi import requests as curl_requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

# 默认测试配置
DEFAULT_CONFIG_FILE = "config/sources/hjxy.json"

def base64_encode(s):
    return base64.b64encode(str(s).encode('utf-8')).decode('utf-8')

async def fetch_api(url: str, payload: dict, headers: dict):
    print(f"正在请求 API: {url} ...")
    # Base64 编码参数
    encoded_data = {k: base64_encode(v) for k, v in payload.items()}
    
    # 确保 Content-Type
    if "Content-Type" not in headers:
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"

    async with curl_requests.AsyncSession(impersonate="chrome120", headers=headers) as session:
        response = await session.post(url, data=encoded_data)
        print(f"状态码: {response.status_code}")
        return response.json()

async def fetch_html(url: str, headers: dict = None):
    # 支持本地文件路径 (以 file:// 开头或绝对路径)
    if url.startswith("file://") or os.path.exists(url):
        file_path = url.replace("file://", "")
        print(f"正在读取本地文件: {file_path} ...")
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            print(f"读取文件失败: {e}")
            return ""

    print(f"正在抓取: {url} ...")
    async with curl_requests.AsyncSession(impersonate="chrome120", headers=headers) as session:
        response = await session.get(url)
        print(f"状态码: {response.status_code}")
        # 调试：如果抓取到的内容过短，可能是反爬或动态加载
        if len(response.text) < 1000:
            print(f"警告: 响应内容过短 ({len(response.text)} 字符)")
            print(response.text)
        return response.text

def test_list_page(html: str, selectors: dict, base_url: str):
    print("\n--- 测试列表页解析 ---")
    soup = BeautifulSoup(html, "lxml")
    print(soup)
    items = soup.select(selectors["item_container"])
    print(f"找到 {len(items)} 个条目")

    links = []
    for i, item in enumerate(items): # 只打印前5个
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
        if not selectors.get("url"):
            url_el = item
        else:
            url_el = item.select_one(selectors["url"])
    
        link = url_el.get('href') if url_el else "未找到"
        if link != "未找到":
            link = urljoin(base_url, link)
            links.append(link)
        print(f"链接: {link}")
    return links

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

    if "embedded_pdf_selector" in selector_cfg:
        pdf_cfg = selector_cfg["embedded_pdf_selector"]
        container_sel = pdf_cfg.get("item_container")
        viewer_sel = pdf_cfg.get("viewer")
        download_sel = pdf_cfg.get("download_link")

        container = soup.select_one(container_sel) if container_sel else soup
        if container:
            viewer_el = container.select_one(viewer_sel) if viewer_sel else None
            download_el = container.select_one(download_sel) if download_sel else None

            if viewer_el:
                pdf_src = viewer_el.get('pdfsrc') or viewer_el.get('src')
                print(f"嵌入式 PDF 查看器链接: {pdf_src}")
            else:
                print("未找到嵌入式 PDF 查看器元素")

            if download_el:
                download_link = download_el.get('href')
                print(f"PDF 下载链接: {download_link}")
            else:
                print("未找到 PDF 下载链接元素")
        else:
            print("未找到嵌入式 PDF 容器")

def test_api_list_page(json_data: dict, selectors: dict, base_url: str):
    print("\n--- 测试 API 列表页解析 ---")
    list_key = selectors.get("item_container", "infolist")
    items = json_data.get(list_key, [])
    print(f"找到 {len(items)} 个条目")
    
    links = []
    for i, item in enumerate(items):
        print(f"\n[条目 {i+1}]")
        title_key = selectors.get("title", "title")
        title = item.get(title_key, "未找到")
        print(f"标题: {title}")

        date_key = selectors.get("date", "releasetime")
        date = item.get(date_key, "未找到")
        print(f"日期: {date}")

        url_key = selectors.get("url", "url")
        raw_url = item.get(url_key)
        link = "未找到"
        if raw_url:
            if raw_url.startswith("http"):
                link = raw_url
            else:
                link = urljoin(base_url, raw_url)
            links.append(link)
            print(f"链接: {link}")
    return links

async def main():
    # 获取命令行参数或使用默认值
    config_file = DEFAULT_CONFIG_FILE
    target_source_id = None
    
    if len(sys.argv) > 1:
        config_file = sys.argv[1]
    if len(sys.argv) > 2:
        target_source_id = sys.argv[2]

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

    sources_to_test = []
    if target_source_id:
        # 查找指定的 source
        for source in config_data.get("sources", []):
            if source["id"] == target_source_id:
                sources_to_test.append(source)
                break
        if not sources_to_test:
            print(f"错误: 在配置文件中未找到 ID 为 '{target_source_id}' 的源")
            return
    else:
        # 测试所有 source
        sources_to_test = config_data.get("sources", [])

    print(f"将测试 {len(sources_to_test)} 个源")

    for source in sources_to_test:
        print(f"\n{'='*50}")
        print(f"开始测试源: {source['name']} ({source['id']})")
        print(f"{'='*50}")
        
        headers = source.get("headers", {})
        links = []

        try:
            if source.get("type") == "api":
                # API 模式测试
                api_url = source.get("api_url")
                payload = source.get("payload", {})
                # 构造第一页的 payload
                current_payload = payload.copy()
                current_payload["pageno"] = "1"
                current_payload["hasPage"] = "true"

                json_data = await fetch_api(api_url, current_payload, headers)
                links = test_api_list_page(json_data, source["selectors"], source["base_url"])
            else:
                # HTML 模式测试
                list_url = source["list_url"]
                html = await fetch_html(list_url, headers)
                links = test_list_page(html, source["selectors"], source["base_url"])

            # 测试详情页 (如果列表页解析成功且有链接)
            if links:
                for idx, link in enumerate(links):
                    print(f"\n正在抓取详情页 {idx+1}: {link}")
                    detail_html = await fetch_html(link, headers)
                    test_detail_page(detail_html, config_data.get("detail_selectors", []), source["base_url"])
            else:
                print("\n未获取到有效链接，跳过详情页测试")

        except Exception as e:
            print(f"测试源 {source['id']} 时发生错误: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
