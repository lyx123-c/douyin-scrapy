import subprocess

from config import FFMPEG_PATH


def merge_video_and_audio(video_file, audio_file, output_file):
    command = [
        FFMPEG_PATH,
        "-hide_banner",
        "-y",
        "-i",
        video_file,
        "-i",
        audio_file,
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
        output_file,
    ]
    result = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "")[-1200:])
