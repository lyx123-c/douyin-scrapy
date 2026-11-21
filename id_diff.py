import json
import re
import sys
import os
from urllib.parse import parse_qs, urlparse
import csv
from lxml import html as lxml_html
from scrapling.fetchers import StealthySession

# ==================== 基础配置 ====================
USER_DATA_DIR = r"/爬虫/douyin_user_data"
OUTPUT_FILE = r"/爬虫/scrapling_c/douyin_videos.csv"
SCROLL_COUNT = 6
SCROLL_DISTANCE = 900
video_data = {}  # 全局存储 {id: title}
captured = {}
title_scores = {}  # 防止 DOM 或空标题覆盖完整 feed 标题

# UTF-8 输出，避免控制台乱码
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

# ==================== 正则与规则 ====================
AWEME_ID_RE = re.compile(r"(?<!\d)(7\d{18})(?!\d)")
VIDEO_PATH_RE = re.compile(r"/video/(7\d{18})(?!\d)")
FIELD_ID_RE = re.compile(r'"(?:aweme_id|awemeId|group_id|group_id_str|item_id|itemId)"\s*:\s*"?(7\d{18})(?!\d)')
ID_KEYS = {"awemeid", "awemeidstr", "groupid", "groupidstr", "itemid", "itemidstr"}
TITLE_PLACEHOLDERS = {"", "无标题", "null", "none", "undefined"}
VIDEO_ITEM_MARKERS = ("desc", "preview_title", "item_title", "video", "images", "image_list", "aweme_type")
NETWORK_RECORD_BODY_LIMIT = 1024 * 1024


# ==================== 提取函数 ====================
def normalize_title(value):
    """清理标题并过滤无意义的占位值。"""
    if not isinstance(value, str):
        return ""
    title = " ".join(value.replace("\u200b", "").split()).strip()
    if title.lower() in TITLE_PLACEHOLDERS:
        return ""
    return title


def store_title(video_id, title, priority):
    """按来源优先级保存标题，低质量结果不覆盖完整标题。"""
    title = normalize_title(title)
    if not video_id or not title:
        return

    current = video_data.get(video_id, "")
    current_priority = title_scores.get(video_id, -1)
    if current and (
        priority < current_priority
        or (priority == current_priority and len(title) <= len(current))
    ):
        return

    video_data[video_id] = title
    title_scores[video_id] = priority


def extract_title_from_json_item(value):
    """从 feed item 的多个标题字段中选出最可靠的一项。"""
    candidates = [
        (normalize_title(value.get("desc")), 100),
        (normalize_title(value.get("preview_title")), 95),
        (normalize_title(value.get("item_title")), 85),
        (normalize_title(value.get("title")), 80),
    ]

    share_info = value.get("share_info")
    if isinstance(share_info, dict):
        candidates.extend([
            (normalize_title(share_info.get("share_title")), 70),
            (normalize_title(share_info.get("share_desc")), 60),
        ])

    candidates.append((normalize_title(value.get("caption")), 40))
    candidates = [(title, priority) for title, priority in candidates if title]
    if not candidates:
        return "", 0
    return max(candidates, key=lambda item: (item[1], len(item[0])))


def parse_json_payload(text):
    """解析完整 JSON，并兼容常见的 XSSI/JSONP 前缀。"""
    if not text:
        return None

    payload = text.lstrip("\ufeff \t\r\n")
    for prefix in (")]}'", "while(1);"):
        if payload.startswith(prefix):
            payload = payload[len(prefix):].lstrip()
            break

    if payload[:1] not in ("{", "["):
        starts = [index for index in (payload.find("{"), payload.find("[")) if index >= 0]
        if not starts or min(starts) > 8:
            return None
        payload = payload[min(starts):]

    try:
        return json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        try:
            data, _ = json.JSONDecoder().raw_decode(payload)
            return data
        except (json.JSONDecodeError, TypeError):
            return None


def extract_ids_from_json(value, ids):
    """递归提取视频 ID 和文章标题。"""
    if isinstance(value, dict):
        video_ids = set()
        for key, child in value.items():
            if re.sub(r"[^a-z0-9]", "", str(key).lower()) in ID_KEYS:
                if isinstance(child, (str, int)):
                    child_text = str(child).strip()
                    if AWEME_ID_RE.fullmatch(child_text):
                        video_ids.add(child_text)

        is_video_item = any(marker in value for marker in VIDEO_ITEM_MARKERS)
        if video_ids and is_video_item:
            ids.update(video_ids)
            title, priority = extract_title_from_json_item(value)
            if title:
                for video_id in video_ids:
                    store_title(video_id, title, priority)

        for child in value.values():
            extract_ids_from_json(child, ids)
    elif isinstance(value, list):
        for child in value:
            extract_ids_from_json(child, ids)


def extract_ids_from_text(text, ids):
    """从文本中提取 ID 和标题。"""
    if not text:
        return
    data = parse_json_payload(text)
    if data is not None:
        extract_ids_from_json(data, ids)
        return

    for match in VIDEO_PATH_RE.finditer(text):
        ids.add(match.group(1))
    for match in FIELD_ID_RE.finditer(text):
        ids.add(match.group(1))


def extract_router_data(html_text, ids):
    """核心大招：抠出隐藏的 _ROUTER_DATA。"""
    if not html_text:
        return
    match = re.search(r'window\._ROUTER_DATA\s*=\s*(\{.*?\})</script>', html_text, re.DOTALL)
    if match:
        print("🎯 命中 SSR 数据块 (_ROUTER_DATA)！")
        data = parse_json_payload(match.group(1))
        if data is not None:
            extract_ids_from_json(data, ids)


def extract_card_title(anchor, mode):
    """按可信度从卡片节点中读取标题，避免写入占位值。"""
    candidates = []
    if mode == "user":
        candidates.extend([anchor.get("title", ""), anchor.get("aria-label", "")])
        candidates.extend(node.text_content() for node in anchor.xpath(".//p"))
        candidates.extend(node.text_content() for node in anchor.xpath("ancestor::li[1]//p"))
        candidates.append(anchor.text_content())
        candidates.extend(anchor.xpath("./img/@alt | .//img/@alt"))
    else:
        selectors = [
            './/*[@data-e2e="video-desc"]',
            './/*[@data-feed-ad-click-refer="title"]',
            './/p',
            'ancestor::*[self::li or self::div][1]//*[@data-e2e="video-desc"]',
            'ancestor::*[self::li or self::div][1]//*[@data-feed-ad-click-refer="title"]',
        ]
        for selector in selectors:
            candidates.extend(
                node.text_content() if hasattr(node, "text_content") else str(node)
                for node in anchor.xpath(selector)
            )
        candidates.extend(anchor.xpath("./img/@alt | .//img/@alt"))
        candidates.extend([anchor.get("title", ""), anchor.get("aria-label", "")])
        anchor_text = anchor.text_content()
        if anchor_text and len(anchor_text.strip()) <= 500:
            candidates.append(anchor_text)

    for candidate in candidates:
        title = normalize_title(candidate)
        if title:
            return title
    return ""


def extract_cards_from_html(html_text, ids, mode):
    """根据模式提取 DOM 卡片数据。"""
    if not html_text:
        return
    try:
        tree = lxml_html.fromstring(html_text)
    except Exception:
        return

    # 1. 博主主页和推荐页：从视频链接卡片提取。
    if mode in ("user", "recommend"):
        for anchor in tree.xpath("//a[contains(@href, '/video/')]"):
            match = VIDEO_PATH_RE.search(anchor.get("href", ""))
            if not match:
                continue
            vid = match.group(1)
            ids.add(vid)
            title = extract_card_title(anchor, mode)
            if title:
                store_title(vid, title, 30)

    # 2. 精选页瀑布流提取。
    elif mode == "jingxuan":
        cards = tree.xpath('//div[contains(@class, "discover-video-card-item") and @data-aweme-id]')
        for card in cards:
            vid = card.get("data-aweme-id")
            if not vid:
                continue
            ids.add(vid)
            title_nodes = card.xpath('.//span[@data-feed-ad-click-refer="title"]')
            if title_nodes:
                store_title(vid, title_nodes[0].text_content(), 30)
            else:
                alt_nodes = card.xpath(".//img/@alt")
                if alt_nodes:
                    store_title(vid, alt_nodes[0], 20)


def response_body_bytes(response):
    """兼容 Playwright 的 body() 和 Scrapling 录制对象的 body 属性。"""
    body = getattr(response, "body", b"")
    if callable(body):
        body = body()
    return body or b""


def decode_response_body(response):
    """按响应头字符集解码浏览器返回的完整响应体。"""
    body = response_body_bytes(response)
    if isinstance(body, str):
        return body
    if not body:
        return ""

    headers = getattr(response, "headers", {}) or {}
    content_type = headers.get("content-type", "") if isinstance(headers, dict) else ""
    match = re.search(r"charset=([^;\s]+)", content_type, re.IGNORECASE)
    encodings = [match.group(1)] if match else []
    encodings.extend(["utf-8-sig", "utf-8", getattr(response, "encoding", None)])

    for encoding in dict.fromkeys(encodings):
        if not encoding:
            continue
        try:
            return body.decode(encoding, errors="replace")
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


def is_target_feed_url(url, mode):
    url = (url or "").lower()
    if mode == "recommend":
        return "feed" in url or "/recommend/" in url
    if mode == "user":
        return "/aweme/post/" in url
    return False


def capture_full_feed_response(response, ids, mode):
    """直接读取完整 feed 响应，绕过 Scrapling 网络录制的 1 MB 单响应上限。"""
    if not is_target_feed_url(getattr(response, "url", ""), mode):
        return 0

    status = getattr(response, "status", 200)
    if status and not 200 <= status < 300:
        return 0

    text = decode_response_body(response)
    if not text:
        return 0

    before = sum(1 for video_id in ids if video_data.get(video_id))
    extract_ids_from_text(text, ids)
    after = sum(1 for video_id in ids if video_data.get(video_id))
    return max(0, after - before)


def build_feed_response_handler(ids, mode):
    def handle_response(response):
        try:
            added = capture_full_feed_response(response, ids, mode)
            if added:
                print(f"  feed 响应补充 {added} 个标题：{response.url[:100]}")
        except Exception as error:
            print(f"  feed 响应解析失败：{error}")

    return handle_response


# ==================== 页面自动滚动 ====================
def capture_scrolled_page(page):
    page.wait_for_timeout(4000)
    for i in range(SCROLL_COUNT):
        page.mouse.wheel(0, SCROLL_DISTANCE)
        page.wait_for_timeout(1500)
        print(f" 已滚动 {i + 1}/{SCROLL_COUNT} 次")
    page.wait_for_timeout(3000)
    captured["html"] = page.content()


# ==================== 核心分流抓取程序 ====================
def scrape_by_mode(target_url, mode):
    print(f"\n🚀 目标模式: [{mode}] | 地址: {target_url}")
    found_ids = set()
    video_data.clear()
    title_scores.clear()
    captured.clear()
    feed_response_handler = build_feed_response_handler(found_ids, mode)

    with StealthySession(
            record_requests=True,
            max_recorded_requests=3000,
            headless=False,
            user_data_dir=USER_DATA_DIR,
            network_idle=False,
    ) as session:

        # 1. 访问页面并自动滚动。page_setup 在导航前注册监听，首个 feed 响应也不会漏掉。
        session.fetch(
            target_url,
            page_setup=lambda page: page.on("response", feed_response_handler),
            page_action=capture_scrolled_page,
            wait=1500,
        )

        html = captured.get("html", "")
        if not html:
            print("❌ 未能获取到页面 HTML 源码！请检查登录状态。")
            return

        print(f"✅ 成功获取 HTML，长度: {len(html)}")

        # 2. 从页面源码提取
        print("🔍 正在解析页面 DOM 和隐藏数据...")
        extract_ids_from_text(html, found_ids)
        extract_router_data(html, found_ids)
        extract_cards_from_html(html, found_ids, mode)

        # 3. 从网络请求中精准兜底补充
        print("🔍 正在从网络接口补充数据...")
        records = session.network.search(limit=None, include_static=False)

        for response in records:
            if not is_target_feed_url(getattr(response, "url", ""), mode):
                continue

            # 录制体可能被截断，这里只作为 ID 和短响应的兜底。
            body = response_body_bytes(response)
            if 0 < len(body) < NETWORK_RECORD_BODY_LIMIT:
                extract_ids_from_text(decode_response_body(response), found_ids)

    # ==================== 结果处理 ====================
    ordered_ids = sorted(found_ids, key=int)
    print(f"\n🎉 提取完成！共捕获 {len(ordered_ids)} 个视频。")

    if not ordered_ids:
        print("❌ 没有提取到 ID。可能触发了风控或服务异常。")
        return

    missing_titles = sum(1 for vid in ordered_ids if not video_data.get(vid))
    print(f"🏷️ 标题覆盖：{len(ordered_ids) - missing_titles}/{len(ordered_ids)}")

    # 保存文件
    with open(OUTPUT_FILE, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        if not os.path.exists(OUTPUT_FILE) or os.path.getsize(OUTPUT_FILE) == 0:
            writer.writerow(["视频ID", "标题"])
        for vid in ordered_ids:
            writer.writerow([vid, video_data.get(vid, "无标题")])

    print(f"💾 数据已追加保存至：{OUTPUT_FILE}")


# ==================== 主控菜单 ====================
if __name__ == "__main__":
    print("🎯 抖音数据抓取系统")
    print("1. 推荐页 (靠接口监听)")
    print("2. 博主主页 (靠 A 标签与 ROUTER 解析)")
    print("3. 精选/抖音首页 (靠卡片 Div 解析)")

    choice = input("请选择抓取模式 (1/2/3): ").strip()

    if choice == '1':
        scrape_by_mode("https://www.douyin.com/?recommend=1", mode="recommend")
    elif choice == '2':
        url = input("请粘贴博主主页地址: ").strip()
        scrape_by_mode(url, mode="user")
    elif choice == '3':
        scrape_by_mode("https://www.douyin.com/jingxuan", mode="jingxuan")
    else:
        print("无效输入。")
