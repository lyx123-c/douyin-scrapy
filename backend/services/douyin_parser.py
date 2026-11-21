import json
import re

from lxml import html as lxml_html

from config import (
    AWEME_ID_RE,
    ID_KEYS,
    SEARCH_ITEM_RE,
    VIDEO_PATH_RE,
)


def _normalize_title(value) -> str:
    if value is None:
        return ""
    title = " ".join(str(value).split()).strip()
    return "" if title == "无标题" else title


def _add_video_id(ids, video_data, raw_id, title=""):
    video_id = str(raw_id or "").strip()
    if not AWEME_ID_RE.fullmatch(video_id):
        return False

    ids.add(video_id)
    normalized_title = _normalize_title(title)
    if normalized_title:
        existing = video_data.get(video_id, "")
        if not existing or len(normalized_title) > len(existing):
            video_data[video_id] = normalized_title
    return True


def walk_json(data, ids, video_data):
    if isinstance(data, dict):
        discovered_ids = []
        for key, value in data.items():
            if re.sub(r"[^a-z0-9]", "", str(key).lower()) in ID_KEYS:
                if isinstance(value, (str, int)) and _add_video_id(
                    ids,
                    video_data,
                    value,
                    data.get("desc") or data.get("title"),
                ):
                    discovered_ids.append(str(value).strip())

        if discovered_ids:
            title = data.get("desc") or data.get("title")
            if title:
                for video_id in discovered_ids:
                    _add_video_id(ids, video_data, video_id, title)

        for value in data.values():
            walk_json(value, ids, video_data)
    elif isinstance(data, list):
        for item in data:
            walk_json(item, ids, video_data)


def extract_ids_from_text(text, ids, video_data):
    if not text:
        return
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    if not isinstance(text, str):
        return

    searchable_text = text
    try:
        data = json.loads(text)
        walk_json(data, ids, video_data)
        searchable_text = json.dumps(data, ensure_ascii=False)
    except Exception:
        pass

    for match in VIDEO_PATH_RE.finditer(searchable_text):
        _add_video_id(ids, video_data, match.group(1))
    for match in SEARCH_ITEM_RE.finditer(searchable_text):
        _add_video_id(ids, video_data, match.group(1))


def extract_router_data(html_text, ids, video_data):
    if not html_text:
        return

    match = re.search(
        r"window\._ROUTER_DATA\s*=\s*(\{.*?\})</script>",
        html_text,
        re.DOTALL,
    )
    if not match:
        return

    extract_ids_from_text(match.group(1), ids, video_data)


def _scope_by_xpath(tree, expressions):
    for expression in expressions:
        nodes = tree.xpath(expression)
        if nodes:
            return nodes[0]
    return tree


def _anchor_title(anchor) -> str:
    title = (
        anchor.get("title")
        or anchor.get("aria-label")
        or anchor.get("data-title")
        or anchor.text_content()
    )
    normalized = _normalize_title(title)
    if normalized:
        return normalized

    alt_nodes = anchor.xpath(".//img/@alt")
    if alt_nodes:
        return _normalize_title(alt_nodes[0])
    return ""


def _card_title(card) -> str:
    title_nodes = card.xpath(
        './/*[@data-feed-ad-click-refer="title" or @data-e2e="video-title"]'
    )
    for node in title_nodes:
        title = _normalize_title(node.text_content())
        if title:
            return title

    for attribute in ("title", "aria-label", "data-title"):
        title = _normalize_title(card.get(attribute))
        if title:
            return title

    alt_nodes = card.xpath(".//img/@alt")
    if alt_nodes:
        title = _normalize_title(alt_nodes[0])
        if title:
            return title

    return _normalize_title(card.text_content())


def _extract_links(scope, ids, video_data):
    for anchor in scope.xpath(".//a[contains(@href, '/video/')]"):
        match = VIDEO_PATH_RE.search(anchor.get("href", ""))
        if not match:
            continue
        _add_video_id(ids, video_data, match.group(1), _anchor_title(anchor))


def _extract_aweme_cards(scope, ids, video_data):
    cards = scope.xpath(
        './/*[@data-aweme-id or contains(@class, "discover-video-card-item")]'
    )
    if hasattr(scope, "get") and (
        scope.get("data-aweme-id")
        or "discover-video-card-item" in (scope.get("class") or "")
    ):
        cards.insert(0, scope)

    for card in cards:
        title = _card_title(card)
        raw_id = card.get("data-aweme-id")
        if raw_id:
            _add_video_id(ids, video_data, raw_id, title)
        _extract_links(card, ids, video_data)
    return len(cards)


def extract_cards_from_html(html_text, ids, mode, video_data):
    if not html_text:
        return
    try:
        tree = lxml_html.fromstring(html_text)
    except Exception:
        return

    if mode in ("recommend", "user"):
        scope = _scope_by_xpath(
            tree,
            [
                '//*[@data-e2e="scroll-list"]',
                '//*[@data-e2e="user-post-list"]',
                "//main",
            ],
        )
        _extract_links(scope, ids, video_data)

    elif mode == "jingxuan":
        card_count = _extract_aweme_cards(tree, ids, video_data)
        if not card_count:
            _extract_links(tree, ids, video_data)

    elif mode == "search":
        scope = _scope_by_xpath(
            tree,
            [
                '//*[@id="search-result-container"]',
                '//*[@data-e2e="search-result-container"]',
                "//main",
            ],
        )
        _extract_aweme_cards(scope, ids, video_data)
        _extract_links(scope, ids, video_data)
        for node in scope.xpath('.//*[starts-with(@id, "waterfall_item_")]'):
            match = SEARCH_ITEM_RE.search(node.get("id", ""))
            if match:
                _add_video_id(ids, video_data, match.group(1), _card_title(node))
