import json
import re
import sys
import os
import csv
from urllib.parse import urlparse
from lxml import html as lxml_html
from scrapling.fetchers import StealthySession

# ==================== 基础配置 ====================
USER_DATA_DIR = r"D:\下载\PythonProject4\爬虫\douyin_user_data"
OUTPUT_FILE = r"D:\下载\PythonProject4\爬虫\scrapling_c\视频\douyin_videos.csv"
SCROLL_COUNT = 6
SCROLL_DISTANCE = 900

# 全局变量，用于在 page_action 和主函数之间传递数据
video_data = {}  # 存储 {id: title}
captured = {}  # 存储 {html: ...}

# 终端防乱码
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

# 正则与规则
AWEME_ID_RE = re.compile(r"(?<!\d)(7\d{18})(?!\d)")
VIDEO_PATH_RE = re.compile(r"/video/(7\d{18})(?!\d)")
# 针对搜索页的特殊 ID 规则：waterfall_item_7xxxxx
SEARCH_ITEM_RE = re.compile(r"waterfall_item_(7\d{18})")
ID_KEYS = {"awemeid", "awemeidstr", "groupid", "groupidstr", "itemid", "itemidstr"}


# ==================== 提取函数 ====================

def walk_json(data, ids):
    """递归遍历 JSON 数据，提取 ID 和标题"""
    if isinstance(data, dict):
        vid = None
        for key, value in data.items():
            if re.sub(r"[^a-z0-9]", "", str(key).lower()) in ID_KEYS:
                if isinstance(value, (str, int)) and AWEME_ID_RE.fullmatch(str(value).strip()):
                    vid = str(value).strip()
                    ids.add(vid)

        if vid:
            title = data.get("desc") or data.get("title")
            if isinstance(title, str) and title.strip():
                video_data[vid] = " ".join(title.split())

        for v in data.values():
            walk_json(v, ids)
    elif isinstance(data, list):
        for item in data:
            walk_json(item, ids)


def extract_ids_from_text(text, ids):
    """从文本或源码中提取 ID（JSON解析 + 正则兜底）"""
    if not text:
        return

    # 1. 尝试作为纯 JSON 解析
    try:
        data = json.loads(text)
        walk_json(data, ids)
        return
    except (json.JSONDecodeError, TypeError):
        pass

    # 2. 正则兜底提取 /video/ 链接
    for match in VIDEO_PATH_RE.finditer(text):
        ids.add(match.group(1))

    # 3. 正则兜底提取搜索页的特殊 ID
    for match in SEARCH_ITEM_RE.finditer(text):
        ids.add(match.group(1))


def extract_router_data(html_text, ids):
    """提取隐藏在 HTML 源码里的 _ROUTER_DATA 数据块"""
    if not html_text:
        return
    match = re.search(r'window\._ROUTER_DATA\s*=\s*(\{.*?\})</script>', html_text, re.DOTALL)
    if match:
        print("🎯 命中 SSR 数据块 (_ROUTER_DATA)！")
        try:
            data = json.loads(match.group(1))
            walk_json(data, ids)
        except Exception:
            pass


def extract_cards_from_html(html_text, ids, mode):
    """解析页面 DOM 结构提取标题"""
    if not html_text:
        return
    try:
        tree = lxml_html.fromstring(html_text)
    except Exception:
        return

    # 模式1/2：推荐页 & 博主主页（找 a 标签兜底）
    if mode in ("recommend", "user"):
        for anchor in tree.xpath("//a[contains(@href, '/video/')]"):
            match = VIDEO_PATH_RE.search(anchor.get("href", ""))
            if match:
                vid = match.group(1)
                ids.add(vid)
                title = anchor.get("title", "") or anchor.get("aria-label", "") or anchor.text_content()
                if not title:
                    alt = anchor.xpath(".//img/@alt")
                    title = " ".join(alt[0].split()) if alt else "无标题"
                if " ".join(title.split()) not in ("", "无标题"):
                    video_data[vid] = " ".join(title.split())

    # 模式3：精选页（找特有卡片 Div）
    elif mode == "jingxuan":
        cards = tree.xpath('//div[contains(@class, "discover-video-card-item") and @data-aweme-id]')
        for card in cards:
            vid = card.get("data-aweme-id")
            if not vid: continue
            ids.add(vid)
            title_nodes = card.xpath('.//span[@data-feed-ad-click-refer="title"]')
            if title_nodes:
                video_data[vid] = " ".join(title_nodes[0].text_content().split())
            else:
                alt = card.xpath('.//img/@alt')
                if alt: video_data[vid] = " ".join(alt[0].split())

    # 模式4：搜索页（针对截图里的 FPJdImQO 和 waterfall_item）
    elif mode == "search":
        cards = tree.xpath('//div[contains(@class, "FPJdImQO")]')
        for card in cards:
            raw_id = card.get('id', '')
            id_match = SEARCH_ITEM_RE.search(raw_id)
            if id_match:
                vid = id_match.group(1)
                ids.add(vid)
                # 获取标题（找 class 为 k67TqHqQ 的 div 提取文本）
                title_nodes = card.xpath('.//div[contains(@class, "k67TqHqQ")]')
                if title_nodes:
                    title = " ".join(title_nodes[0].text_content().split())
                else:
                    title = " ".join(card.text_content().split())

                if len(title) > 5:
                    video_data[vid] = title


# ==================== 页面滚动操作 ====================

def capture_scrolled_page(page):
    """自动滚动并等待加载，最后把 HTML 存进全局变量"""
    page.wait_for_timeout(4000)
    for i in range(SCROLL_COUNT):
        page.mouse.wheel(0, SCROLL_DISTANCE)
        page.wait_for_timeout(1500)
        print(f" 已滚动 {i + 1}/{SCROLL_COUNT} 次")
    page.wait_for_timeout(3000)
    captured["html"] = page.content()


# ==================== 核心抓取程序 ====================

def scrape_by_mode(target_url, mode):
    print(f"\n🚀 目标模式: [{mode}] | 地址: {target_url}")
    found_ids = set()
    video_data.clear()
    captured.clear()

    with StealthySession(
            record_requests=True,
            max_recorded_requests=3000,
            headless=False,
            user_data_dir=USER_DATA_DIR,
            network_idle=False,
    ) as session:

        # 1. 访问页面并自动滚动
        session.fetch(target_url, page_action=capture_scrolled_page, wait=1500)

        html = captured.get("html", "")
        if not html:
            print("❌ 未能获取到页面 HTML 源码！请检查登录状态或服务异常。")
            return

        print(f"✅ 成功获取 HTML，长度: {len(html)}")

        # 2. 解析页面源码中的隐藏数据
        print("🔍 正在解析页面隐藏数据和 DOM...")
        extract_router_data(html, found_ids)
        extract_ids_from_text(html, found_ids)
        extract_cards_from_html(html, found_ids, mode)

        # 3. 从网络请求中兜底补充
        print("🔍 正在从网络接口补充数据...")
        for response in session.network.search(limit=None, include_static=False):
            url = response.url.lower()
            # 精准匹配四种模式对应的接口特征
            if mode == "recommend" and 'feed' not in url: continue
            if mode == "user" and 'aweme/post' not in url: continue
            if mode == "search" and 'search' not in url: continue

            body = getattr(response, "body", b"")
            if body and len(body) < 1024 * 1024:
                text = body.decode('utf-8', errors='replace')
                extract_ids_from_text(text, found_ids)

    # ==================== 结果处理 ====================
    ordered_ids = sorted(found_ids, key=int)
    print(f"\n🎉 提取完成！共捕获 {len(ordered_ids)} 个视频。")

    if not ordered_ids:
        print("❌ 没有提取到 ID。可能触发了风控或页面未加载。")
        return

    # 过滤无标题视频
    valid_ids = []
    for vid in ordered_ids:
        title = video_data.get(vid)
        if title and title.strip() != "" and title.strip() != "无标题":
            valid_ids.append(vid)

    filtered_count = len(ordered_ids) - len(valid_ids)
    print(f"🧹 过滤完成：剔除了 {filtered_count} 个无标题视频。")
    print(f"🏷️ 最终有效视频：{len(valid_ids)} 个（100% 带标题）")

    if not valid_ids:
        print("⚠️ 所有视频都没有抓到标题，请确认页面是否正常加载。")
        return

    # 保存文件
    with open(OUTPUT_FILE, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        if not os.path.exists(OUTPUT_FILE) or os.path.getsize(OUTPUT_FILE) == 0:
            writer.writerow(["视频ID", "标题"])

        for vid in valid_ids:
            writer.writerow([vid, video_data.get(vid).strip()])

    print(f"💾 数据已追加保存至：{OUTPUT_FILE}")


# ==================== 主控菜单 ====================

if __name__ == "__main__":
    print("🎯 抖音数据抓取系统")
    print("1. 推荐页")
    print("2. 博主主页")
    print("3. 精选/抖音首页")
    print("4. 搜索页 (关键词搜索)")

    choice = input("请选择抓取模式 (1/2/3/4): ").strip()

    if choice == '1':
        scrape_by_mode("https://www.douyin.com/?recommend=1", mode="recommend")
    elif choice == '2':
        url = input("请粘贴博主主页地址: ").strip()
        scrape_by_mode(url, mode="user")
    elif choice == '3':
        scrape_by_mode("https://www.douyin.com/jingxuan", mode="jingxuan")
    elif choice == '4':
        keyword = input("请输入你要搜索的关键词: ").strip()
        # 构造搜索页的 URL，type=video 表示只搜视频
        search_url = f"https://www.douyin.com/search/{keyword}?type=video"
        scrape_by_mode(search_url, mode="search")
    else:
        print("无效输入。")