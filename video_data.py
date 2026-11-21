import os
import re
import json
import time
import csv
import subprocess
import requests
import pandas as pd
from tqdm import tqdm
from scrapling.fetchers import StealthySession

# ================= 全局配置 =================
USER_DATA_DIR = r"D:\下载\PythonProject4\爬虫\douyin_user_data"
FFMPEG_PATH = r"E:\360Downloads\ffmpeg.exe"
CSV_FILE = r"D:\下载\PythonProject4\爬虫\scrapling_c\视频\video_analytics.csv"
VIDEO_TITLE_CSV = r"D:\下载\PythonProject4\爬虫\scrapling_c\视频\douyin_videos.csv"

REQUEST_HEADERS = {
    "Referer": "https://www.douyin.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"
}


# ================= 工具函数 =================
def get_title_from_csv(video_id):
    if not os.path.exists(VIDEO_TITLE_CSV):
        return None
    try:
        df = pd.read_csv(VIDEO_TITLE_CSV)
        match = df[df[df.columns[0]].astype(str) == str(video_id)]
        if not match.empty:
            title = match.iloc[0][df.columns[1]]
            if pd.notna(title) and str(title).strip() not in ("", "无标题"):
                return str(title).strip()
    except Exception:
        pass
    return None


def is_already_downloaded(video_id):
    """👈 新增：直接用 pandas 查 analytics 里是否有这个 ID"""
    if not os.path.exists(CSV_FILE):
        return False
    try:
        # 使用 dtype=str 防止长数字 ID 被转成科学计数法或丢失精度
        df = pd.read_csv(CSV_FILE, dtype=str)
        if df.empty or "视频ID" not in df.columns:
            return False
        # 判断是否有匹配的行
        return not df[df["视频ID"].astype(str).str.strip() == str(video_id)].empty
    except Exception:
        return False


def download_media(url, output_file, desc="下载"):
    """带进度条的流式下载"""
    with requests.get(url, headers=REQUEST_HEADERS, stream=True, timeout=300) as response:
        response.raise_for_status()
        total_size = int(response.headers.get('content-length', 0))
        with tqdm(total=total_size, unit='B', unit_scale=True, desc=desc, leave=False, ncols=80) as pbar:
            with open(output_file, "wb") as file:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        file.write(chunk)
                        pbar.update(len(chunk))


def merge_video_and_audio(video_file, audio_file, output_file):
    command = [
        FFMPEG_PATH, "-hide_banner", "-y",
        "-i", video_file, "-i", audio_file,
        "-c:v", "copy", "-c:a", "aac", "-shortest", output_file,
    ]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


# ================= 核心主流程 =================
def _fetch_detail_once(video_id):
    target_url = f"https://www.douyin.com/video/{video_id}"
    detail_json = None

    with StealthySession(record_requests=True, headless=False, user_data_dir=USER_DATA_DIR,
                         network_idle=False) as session:
        session.fetch(target_url)

        for _ in range(30):  # 最多等 15 秒
            time.sleep(0.5)
            found = False
            for req in session.network.search(limit=None):
                if '/aweme/detail/' in req.url and f'aweme_id={video_id}' in req.url and req.status == 200:
                    try:
                        detail_json = json.loads(req.body.decode('utf-8'))
                        found = True
                        break
                    except Exception:
                        pass
            if found:
                break
    return detail_json


def step2_crawl_and_download(video_id, max_retries=3):
    # 👇 【核心新增】：下载前先查重
    if is_already_downloaded(video_id):
        print(f"⏭️ 视频 {video_id} 已在 analytics 中存在，跳过下载。")
        return

    video_title = get_title_from_csv(video_id) or "无标题"
    detail_json = None

    # 1. 抓取 JSON 过程
    for attempt in range(1, max_retries + 1):
        print(f"\n🚀 拦截详情 JSON (ID: {video_id}) | 尝试 {attempt}/{max_retries} "
              f"[{'█' * attempt}{'░' * (max_retries - attempt)}]")

        # 清理可能残留的临时文件
        save_dir = r"D:\下载\PythonProject4\爬虫\scrapling_c\视频\mmp"
        video_temp = os.path.join(save_dir, "temp_v.mp4")
        audio_temp = os.path.join(save_dir, "temp_a.m4a")
        for f in (video_temp, audio_temp):
            if os.path.exists(f): os.remove(f)

        detail_json = _fetch_detail_once(video_id)

        if detail_json and 'aweme_detail' in detail_json:
            print("✅ 成功截获 JSON！")
            break
        else:
            print(f"⚠️ 第 {attempt} 次尝试未截获数据。")
            if attempt < max_retries:
                time.sleep(2)

    if not detail_json or 'aweme_detail' not in detail_json:
        print(f"❌ 连续 {max_retries} 次失败，放弃该视频 (ID: {video_id})。")
        return

    # ---------------- 解析 JSON ----------------
    detail = detail_json.get('aweme_detail') or {}
    if video_title == "无标题":
        video_title = detail.get('desc', '无标题').replace('\n', ' ').strip()

    author_name = detail.get('author', {}).get('nickname', '未知作者')
    stats = detail.get('statistics', {})

    # 提取流
    best_video_url = None
    best_height = 0
    for rate in detail.get('video', {}).get('bit_rate', []):
        height = rate.get('play_addr', {}).get('height', 0)
        url_list = rate.get('play_addr', {}).get('url_list', [])
        if height > best_height and url_list:
            best_height = height
            best_video_url = url_list[0]

    bit_rate_audio = detail.get('video', {}).get('bit_rate_audio', [])
    best_audio_url = bit_rate_audio[0].get('audio_meta', {}).get('url_list', {}).get(
        'main_url') if bit_rate_audio else None

    if not best_video_url:
        print("❌ 未找到视频直链。")
        return

    # ---------------- 下载并合并 ----------------
    safe_title = re.sub(r'[\\/*?:"<>|\n\r#]', '', video_title).strip()
    os.makedirs(save_dir, exist_ok=True)
    output_file = os.path.join(save_dir, f"{safe_title[:30]}_{video_id[-6:]}.mp4")

    try:
        # 👇 视频流下载
        download_media(best_video_url, video_temp, desc="[视频流]")

        if best_audio_url:
            # 👇 音频流下载
            download_media(best_audio_url, audio_temp, desc="[音频流]")
            print("✂️ 正在合并...")
            merge_video_and_audio(video_temp, audio_temp, output_file)
            os.remove(video_temp)
            os.remove(audio_temp)
        else:
            # 纯视频直接移动（同盘用 replace 更稳）
            os.replace(video_temp, output_file)

        print(f"🎉 完美！文件已保存：{output_file}")

        # 👇 【重要修改】：下载成功后再记录到 CSV，保证 CSV 里的都是真正下载成功的
        file_exists = os.path.exists(CSV_FILE)
        with open(CSV_FILE, mode='a', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["视频ID", "作者昵称", "视频标题", "点赞量", "评论量", "收藏量", "转发量"])
            writer.writerow([video_id, author_name, video_title, stats.get('digg_count', 0),
                             stats.get('comment_count', 0), stats.get('collect_count', 0),
                             stats.get('share_count', 0)])

    except Exception as e:
        print(f"❌ 下载失败: {e}")
        for f in (video_temp, audio_temp):
            if os.path.exists(f): os.remove(f)


# ================= 主控菜单 =================
if __name__ == "__main__":
    while True:
        input_id = input("\n👉 请输入抖音视频 ID (输入 q 退出): ").strip()
        if input_id.lower() == 'q': break
        if not input_id.isdigit() or len(input_id) != 19:
            print("⚠️ 请输入19位纯数字。")
            continue
        try:
            step2_crawl_and_download(input_id)
        except Exception as e:
            print(f"❌ 发生意外错误: {e}")