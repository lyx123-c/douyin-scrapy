import os
import re
import time
import subprocess
import requests
import pandas as pd  # 新增 pandas 用于读取 CSV
from scrapling.fetchers import StealthySession

# ================= 全局配置 =================
USER_DATA_DIR = r"/爬虫/douyin_user_data"  # 你的持久化登录目录
FFMPEG_PATH = r"E:\360Downloads\ffmpeg.exe"  # ffmpeg 路径
PROBE_BYTES = 1024 * 1024  # 探测媒体头部的字节数(1MB)

# 新增：你的 CSV 路径（假设就在代码同级目录，或者写绝对路径）
CSV_FILE = r"/爬虫/scrapling_c/douyin_videos.csv"

# 用于筛选候选媒体流的 URL 特征
MEDIA_URL_MARKERS = (
    "mime_type=video_mp4", "mime_type=audio_mp4",
    "media-video", "media-audio", ".mp4", ".m4a"
)

REQUEST_HEADERS = {
    "Referer": "https://www.douyin.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"
}


# ================= 工具函数 =================

def step1_ensure_login():
    """第一步：检查登录状态，如果没有则打开浏览器扫码"""
    if os.path.exists(USER_DATA_DIR) and os.listdir(USER_DATA_DIR):
        print("✅ 检测到本地已有登录信息，跳过扫码步骤。")
        return True

    print("⚠️ 未检测到登录信息，正在启动浏览器...")
    with StealthySession(headless=False, user_data_dir=USER_DATA_DIR, network_idle=True) as session:
        print("\n📱 正在打开抖音主页，请在弹出的浏览器中扫码登录。")
        session.fetch("https://www.douyin.com/")
        print("👉 登录成功并看到首页推荐后，请回到终端。")
        input("✅ 确认已登录？按 [回车键] 继续...")
    print("💾 登录状态已保存！")
    return True


def get_title_from_csv(video_id):
    """新增函数：根据传入的 ID 去 CSV 里查标题"""
    if not os.path.exists(CSV_FILE):
        return None
    try:
        df = pd.read_csv(CSV_FILE)
        # 假设 CSV 的第一列是 ID，第二列是标题
        id_col = df.columns[0]
        title_col = df.columns[1]

        # 转成字符串匹配
        match = df[df[id_col].astype(str) == str(video_id)]
        if not match.empty:
            title = match.iloc[0][title_col]
            if pd.notna(title) and str(title).strip() not in ("", "无标题"):
                return str(title).strip()
    except Exception as e:
        print(f"⚠️ 读取 CSV 出错: {e}")
    return None


def probe_media(source_file):
    """用 ffmpeg 探测本地文件是否包含视频/音频轨"""
    result = subprocess.run(
        [FFMPEG_PATH, "-hide_banner", "-i", source_file],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    output = result.stderr or ""
    has_video = bool(re.search(r"Stream #\S+.*Video:", output))
    has_audio = bool(re.search(r"Stream #\S+.*Audio:", output))
    return has_video, has_audio


def probe_remote_media(url):
    """打假神器：流式下载前1MB做探测"""
    probe_file = "temp_probe_stream.bin"
    headers = {**REQUEST_HEADERS, "Range": f"bytes=0-{PROBE_BYTES - 1}"}

    try:
        with requests.get(url, headers=headers, stream=True, timeout=30) as response:
            response.raise_for_status()
            if "text/html" in response.headers.get("Content-Type", "").lower():
                return False, False

            written = 0
            with open(probe_file, "wb") as file:
                for chunk in response.iter_content(chunk_size=256 * 1024):
                    if not chunk: continue
                    file.write(chunk)
                    written += len(chunk)
                    if written >= PROBE_BYTES: break

        if written < 1024: return False, False
        return probe_media(probe_file)
    except Exception:
        return False, False
    finally:
        if os.path.exists(probe_file):
            os.remove(probe_file)


def download_media(url, output_file):
    """流式下载大文件"""
    part_file = f"{output_file}.part"
    try:
        with requests.get(url, headers=REQUEST_HEADERS, stream=True, timeout=300) as response:
            response.raise_for_status()
            if "text/html" in response.headers.get("Content-Type", "").lower():
                raise RuntimeError("下载地址返回 HTML，Cookie 或签名可能已失效")

            downloaded = 0
            with open(part_file, "wb") as file:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk: continue
                    file.write(chunk)
                    downloaded += len(chunk)

        if downloaded < 1024:
            raise RuntimeError(f"下载文件过小: {downloaded} 字节")
        os.replace(part_file, output_file)
        return downloaded
    finally:
        if os.path.exists(part_file):
            os.remove(part_file)


def merge_video_and_audio(video_file, audio_file, output_file):
    """调用 ffmpeg 合并"""
    merged_file = f"{output_file}.merge.mp4"
    command = [
        FFMPEG_PATH, "-hide_banner", "-y",
        "-i", video_file, "-i", audio_file,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart", merged_file,
    ]
    try:
        result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        if result.returncode != 0:
            raise RuntimeError(result.stderr[-1500:])

        has_video, has_audio = probe_media(merged_file)
        if not (has_video and has_audio):
            raise RuntimeError(f"合并结果不完整: video={has_video}, audio={has_audio}")
        os.replace(merged_file, output_file)
    finally:
        if os.path.exists(merged_file):
            os.remove(merged_file)


# ================= 核心主流程 =================

def step2_crawl_and_download(video_id):
    target_url = f"https://www.douyin.com/video/{video_id}"
    video_title = get_title_from_csv(video_id) or "无标题"

    print(f"\n🚀 正在请求页面并拦截详情 JSON (ID: {video_id})...")

    detail_json = None

    # 修改点 1：将 network_idle 设为 True，让 Scrapling 等待网络平静下来
    # 同时 headless 保持 True 后台静默运行
    with StealthySession(record_requests=True, headless=True, user_data_dir=USER_DATA_DIR,
                         network_idle=True) as session:
        session.fetch(target_url)

        # 修改点 2：动态轮询等待，最多等 8 秒
        print("⏳ 正在等待并监听接口响应...")
        for _ in range(16):  # 每次循环等 0.5 秒
            # 去录像带里找
            for req in session.network.search(limit=None):
                # 修改点 3：模糊匹配 detail 接口，兼容多种路径
                if 'aweme' in req.url and 'detail' in req.url and req.status == 200:
                    try:
                        data = json.loads(req.body.decode('utf-8'))
                        # 确保抓到的数据确实是当前视频的
                        if data.get('aweme_detail', {}).get('aweme_id') == str(video_id):
                            detail_json = data
                            break
                    except Exception:
                        pass

            if detail_json:
                print("✅ 成功截获详情数据！")
                break

            time.sleep(0.5)  # 没抓到就等半秒再试

    if not detail_json:
        print("❌ 未能截获详情数据。可能视频被删、被限制，或者触发了滑块验证。")
        return

    # ---------------- 解析 JSON ----------------
    print("开始提取精准音视频直链...")
    detail = detail_json.get('aweme_detail') or {}

    if video_title == "无标题":
        video_title = detail.get('desc', '无标题')

    # 1. 提取最高清视频 (逻辑保持不变)
    best_video_url = None
    best_height = 0
    video_data = detail.get('video') or {}
    bit_rate_list = video_data.get('bit_rate') or []

    for rate in bit_rate_list:
        play_addr = rate.get('play_addr') or {}
        height = play_addr.get('height', 0)
        is_h265 = rate.get('is_h265', 0)
        url_list = play_addr.get('url_list') or []

        if not url_list: continue

        # 如果你被 2160p 搞黑屏了，可以把这里改成 height <= 1080
        if height > best_height or (height == best_height and is_h265 == 0):
            best_height = height
            best_video_url = url_list[0]

    # 2. 提取音频
    best_audio_url = None
    bit_rate_audio = video_data.get('bit_rate_audio') or []
    if bit_rate_audio:
        audio_meta = bit_rate_audio[0].get('audio_meta') or {}
        best_audio_url = (audio_meta.get('url_list') or {}).get('main_url')

    if not best_video_url:
        print("❌ 未在 JSON 中找到视频直链。")
        return

    # ---------------- 组装文件名 ----------------
    safe_title = re.sub(r'[\\/*?:"<>|\n\r]', '', video_title).strip()
    output_file = f"{safe_title[:30]}_{video_id[-6:]}.mp4"
    print(f"🏷️ 目标文件名：{output_file}")
    print(f"🎬 最高清画质：{best_height}p")

    # ---------------- 极速下载与合并 ----------------
    video_temp = "temp_video.mp4"
    audio_temp = "temp_audio.m4a"

    try:
        print("⬇️ 下载视频流...")
        download_media(best_video_url, video_temp)

        if best_audio_url:
            print("⬇️ 下载独立音频流...")
            download_media(best_audio_url, audio_temp)
            print("✂️ 正在合并...")
            merge_video_and_audio(video_temp, audio_temp, output_file)
            os.remove(video_temp)
            os.remove(audio_temp)
        else:
            print("⚠️ 没找到独立音频流，保存纯视频。")
            os.rename(video_temp, output_file)

        print(f"🎉 完美！文件已保存：{output_file}")
    except Exception as e:
        print(f"❌ 下载或合并失败: {e}")
        for f in (video_temp, audio_temp):
            if os.path.exists(f): os.remove(f)


if __name__ == "__main__":
    # 先确保登录
    if step1_ensure_login():
        print("\n" + "=" * 40)
        print("🎯 抖音视频精准下载器")
        print("=" * 40)

        # 👇 改成手动输入循环
        while True:
            input_id = input("\n👉 请输入抖音视频 ID (输入 q 退出): ").strip()

            if input_id.lower() == 'q':
                print("再见！")
                break

            if not input_id.isdigit() or len(input_id) != 19:
                print("⚠️ 格式好像不对（应为19位纯数字），请重新输入。")
                continue

            try:
                step2_crawl_and_download(input_id)
            except Exception as e:
                print(f"❌ 发生意外错误: {e}")