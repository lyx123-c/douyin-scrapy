import os
import re
import time
import json
import subprocess
import requests
import pandas as pd
from tqdm import tqdm                      # 👈 新增
from scrapling.fetchers import StealthySession

# ================= 全局配置 =================
USER_DATA_DIR = r"D:\下载\PythonProject4\爬虫\douyin_user_data"
FFMPEG_PATH = r"E:\360Downloads\ffmpeg.exe"
CSV_FILE = r"D:\下载\PythonProject4\爬虫\scrapling_c\视频\douyin_videos.csv"

REQUEST_HEADERS = {
    "Referer": "https://www.douyin.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"
}


# ================= 基础工具 =================
def step1_ensure_login():
    if os.path.exists(USER_DATA_DIR) and os.listdir(USER_DATA_DIR):
        print("✅ 检测到本地已有登录信息。")
        return True
    print("⚠️ 未检测到登录信息，请扫码...")
    with StealthySession(headless=False, user_data_dir=USER_DATA_DIR) as session:
        session.fetch("https://www.douyin.com/")
        input("✅ 确认登录后按回车...")
    return True


def get_title_from_csv(video_id):
    if not os.path.exists(CSV_FILE): return None
    try:
        df = pd.read_csv(CSV_FILE)
        match = df[df[df.columns[0]].astype(str) == str(video_id)]
        if not match.empty:
            title = match.iloc[0][df.columns[1]]
            if pd.notna(title) and str(title).strip() not in ("", "无标题"):
                return str(title).strip()
    except Exception:
        pass
    return None


def download_media(url, output_file, desc="下载"):
    """流式下载 + 字节进度条"""
    with requests.get(url, headers=REQUEST_HEADERS, stream=True, timeout=300) as response:
        response.raise_for_status()
        total = int(response.headers.get("Content-Length", 0)) or None
        with open(output_file, "wb") as file, \
                tqdm(total=total, unit="B", unit_scale=True, unit_divisor=1024,
                     desc=desc, ncols=80, leave=False) as bar:          # 👈 进度条
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    file.write(chunk)
                    bar.update(len(chunk))                              # 👈 更新


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

        # 👇 【核心修改】：从等 5 秒（10次 * 0.5秒） 改为 等 15 秒（30次 * 0.5秒）
        for _ in range(30):
            time.sleep(0.5)
            found = False
            # 每次半秒去录像带里翻一下
            for req in session.network.search(limit=None):
                if '/aweme/detail/' in req.url and f'aweme_id={video_id}' in req.url and req.status == 200:
                    try:
                        detail_json = json.loads(req.body.decode('utf-8'))
                        found = True
                        break
                    except Exception:
                        pass
            if found:
                break  # 抓到了立刻闪人，一秒都不多等

    return detail_json


def step2_crawl_and_download(video_id, max_retries=3):
    video_title = get_title_from_csv(video_id) or "无标题"
    detail_json = None

    for attempt in range(1, max_retries + 1):
        print(f"\n🚀 拦截详情 JSON (ID: {video_id}) | 尝试 {attempt}/{max_retries} "
              f"[{'█' * attempt}{'░' * (max_retries - attempt)}]")             # 👈 文本进度

        for f in ["temp_video.mp4", "temp_audio.m4a"]:
            if os.path.exists(f): os.remove(f)

        detail_json = _fetch_detail_once(video_id)

        if detail_json and 'aweme_detail' in detail_json:
            print("✅ 成功截获 JSON！")
            break
        else:
            print(f"⚠️ 第 {attempt} 次尝试未截获数据，可能网络卡顿或触发风控。")
            if attempt < max_retries:
                print("⏳ 休息 2 秒后重试...")
                time.sleep(2)

    if not detail_json or 'aweme_detail' not in detail_json:
        print(f"❌ 连续 {max_retries} 次失败，放弃该视频 (ID: {video_id})。")
        return

    # ---------------- 解析 JSON ----------------
    print("✅ 成功截获 JSON，开始提取精准音视频直链...")
    detail = detail_json.get('aweme_detail') or {}

    if video_title == "无标题":
        video_title = detail.get('desc') or "无标题"

    best_video_url, best_height = None, 0
    video_data = detail.get('video') or {}
    for rate in (video_data.get('bit_rate') or []):
        play_addr = rate.get('play_addr') or {}
        height = play_addr.get('height', 0)
        is_h265 = rate.get('is_h265', 0)
        url_list = play_addr.get('url_list') or []
        if not url_list:
            continue
        if height > best_height or (height == best_height and is_h265 == 0):
            best_height, best_video_url = height, url_list[0]

    best_audio_url = None
    bit_rate_audio = video_data.get('bit_rate_audio') or []
    if bit_rate_audio:
        audio_meta = (bit_rate_audio[0] or {}).get('audio_meta') or {}
        ul = audio_meta.get('url_list') or {}
        if isinstance(ul, dict):
            best_audio_url = ul.get('main_url')
        elif isinstance(ul, list) and ul:
            best_audio_url = ul[0]

    if not best_video_url:
        print("❌ 未在 JSON 中找到视频直链。")
        return

    # ---------------- 组装文件名 ----------------
    safe_title = re.sub(r'[\\/*?:"<>|\n\r#]', '', video_title).strip()
    save_dir = r"D:\下载\PythonProject4\爬虫\scrapling_c\视频\mmp"
    os.makedirs(save_dir, exist_ok=True)
    output_file = os.path.join(save_dir, f"{safe_title[:30]}_{video_id[-6:]}.mp4")
    print(f"🏷️ 目标文件名：{output_file}")
    print(f"🎬 最高清画质：{best_height}p")

    # 临时文件放同盘，避免 os.rename 跨盘失败
    video_temp = os.path.join(save_dir, "temp_video.mp4")
    audio_temp = os.path.join(save_dir, "temp_audio.m4a")

    try:
        print("⬇️ 下载视频流...")
        download_media(best_video_url, video_temp, desc="📹 视频")

        if best_audio_url:
            print("⬇️ 下载独立音频流...")
            download_media(best_audio_url, audio_temp, desc="🎵 音频")
            print("✂️ 正在合并...")
            with tqdm(total=1, desc="🔗 合并", ncols=80, leave=False) as bar:  # 👈
                merge_video_and_audio(video_temp, audio_temp, output_file)
                bar.update(1)
            os.remove(video_temp)
            os.remove(audio_temp)
        else:
            print("⚠️ 没找到独立音频流，保存纯视频。")
            os.replace(video_temp, output_file)

        print(f"🎉 完美！文件已保存：{output_file}")
    except Exception as e:
        print(f"❌ 下载或合并失败: {e}")
        for f in (video_temp, audio_temp):
            if os.path.exists(f): os.remove(f)


# ================= 主控菜单 =================
if __name__ == "__main__":
    if step1_ensure_login():
        while True:
            input_id = input("\n👉 请输入抖音视频 ID (输入 q 退出): ").strip()
            if input_id.lower() == 'q': break
            if not input_id.isdigit() or len(input_id) != 19:
                print("⚠️ 格式错误，请输入19位纯数字。")
                continue
            try:
                step2_crawl_and_download(input_id)
            except Exception as e:
                print(f"❌ 发生意外错误: {e}")