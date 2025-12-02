import asyncio
import json
import os
import sys
import base64
from curl_cffi import requests as curl_requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs

# 默认测试配置
DEFAULT_CONFIG_FILE = "config/sources/sxxy.json"

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
        if not selectors.get("url"):
            url_el = item
        else:
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
                print(f"图片 {i+1}: {urljoin(base_url,src)}")

    # 提取 PDF 链接
    if "embedded_pdf_selector" in selector_cfg:
        files_sel = selector_cfg["embedded_pdf_selector"].get("download_link")
        if files_sel:
            files = soup.select(files_sel)
            print(f"找到 {len(files)} 份PDF")
            for i, el in enumerate(files[:5]):
                pdf_url = None
                # iframe: 从src的file参数解析
                if el.name == "iframe":
                    src = el.get("src")
                    if src:
                        parsed = urlparse(src)
                        q = parse_qs(parsed.query)
                        file_param = q.get("file")
                        if file_param:
                            pdf_url = urljoin(base_url, file_param[0])
                        else:
                            pdf_url = urljoin(base_url, src)
                # script: 从文本匹配 showVsbpdfIframe("/path.pdf", ...)
                elif el.name == "script":
                    content = el.string or el.get_text() or ""
                    import re
                    m = re.search(r"showVsbpdfIframe\([\"']([^\"']+?\.pdf)[\"']", content)
                    if m:
                        pdf_url = urljoin(base_url, m.group(1))
                elif el.name == "a":
                    href = el.get("href") or el.get("src")
                    if href and href.endswith(".pdf"):
                        pdf_url = urljoin(base_url, href)
                print(f"PDF {i+1}: {pdf_url}")

    # 提取 DOC/DOCX 链接
    if "doc_selector" in selector_cfg:
        files_sel = selector_cfg["doc_selector"].get("files")
        if files_sel:
            files = soup.select(files_sel)
            print(f"找到 {len(files)} 份DOC/DOCX")
            for i, a in enumerate(files[:5]):
                href = a.get("href") or a.get("src")
                name = a.get_text(strip=True)
                print(f"DOC/DOCX {i+1}: {urljoin(base_url,href)}")

def test_wechat_page(html: str):
    print("\n--- 测试微信公众号文章解析 ---")
    soup = BeautifulSoup(html, "lxml")
    
    if "当前环境异常" in html:
        print("警告: 检测到微信环境异常")
        return

    title = soup.find("h1", class_="rich_media_title")
    print(f"标题: {title.get_text(strip=True) if title else '未找到'}")
    
    author = soup.find("a", id="js_name")
    print(f"作者: {author.get_text(strip=True) if author else '未找到'}")
    
    content_div = soup.find("div", class_="rich_media_content")
    if content_div:
        text = content_div.get_text("\n", strip=True)[:100] + "..."
        print(f"正文预览: {text}")
    else:
        print("未找到正文")
        
    # Regex checks
    import re
    from datetime import datetime
    
    biz_match = re.search(r'var biz\s*=\s*"(.*?)";', html)
    if biz_match:
        print(f"Biz: {biz_match.group(1)}")
        
    time_match = None
    for pattern in (
        r"var createTime = ['\"](.*?)['\"]",
        r"var ct = ['\"](.*?)['\"]",
        r"var publish_time = ['\"](.*?)['\"]",
    ):
        time_match = re.search(pattern, html)
        if time_match:
            break
    if time_match:
        try:
            ts = float(time_match.group(1))
            dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
            print(f"CreateTime: {dt}")
        except:
            print(f"CreateTime (raw): {time_match.group(1)}")

def test_api_list_page(json_data: dict, selectors: dict, base_url: str):
    print("\n--- 测试 API 列表页解析 ---")
    list_key = selectors.get("item_container", "infolist")
    items = json_data.get(list_key, [])
    print(f"找到 {len(items)} 个条目")

    for i, item in enumerate(items[:5]):
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
        print(f"链接: {link}")

        if i == 0 and link != "未找到":
            return link
    return None

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
        first_link = None

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
                first_link = test_api_list_page(json_data, source["selectors"], source["base_url"])
            else:
                # HTML 模式测试
                list_url = source["list_url"]
                html = await fetch_html(list_url, headers)
                first_link = test_list_page(html, source["selectors"], source["base_url"])

            # 测试详情页 (如果列表页解析成功且有链接)
            if first_link:
                print(f"\n正在抓取详情页: {first_link}")
                
                # Handle headers
                req_headers = headers.copy()
                target_host = urlparse(first_link).netloc
                cfg_host = req_headers.get("host") or req_headers.get("Host")
                if cfg_host and cfg_host != target_host:
                    req_headers.pop("host", None)
                    req_headers.pop("Host", None)

                detail_html = await fetch_html(first_link, req_headers)
                
                if "mp.weixin.qq.com" in first_link:
                    test_wechat_page(detail_html)
                else:
                    test_detail_page(detail_html, config_data.get("detail_selectors", []), source["base_url"])
            else:
                print("\n未获取到有效链接，跳过详情页测试")

        except Exception as e:
            print(f"测试源 {source['id']} 时发生错误: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
