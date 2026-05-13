"""ffmpeg download/conversion and yt-dlp fallback methods."""

import glob
import os
import subprocess
import sys

from downloader.config import FORMAT_CONFIG
from downloader.streams import get_quality_filter_arg


def check_dependencies():
    """Check if required dependencies (yt-dlp, ffmpeg) are installed."""
    errors = []

    yt_dlp_found = False
    result = subprocess.run(
        ["which", "yt-dlp"],
        capture_output=True,
        text=True
    )
    if result.returncode == 0:
        version_result = subprocess.run(
            ["yt-dlp", "--version"],
            capture_output=True,
            text=True
        )
        if version_result.returncode == 0:
            print(f"[INFO] yt-dlp version: {version_result.stdout.strip()}")
        yt_dlp_found = True

    if not yt_dlp_found:
        errors.append(
            "yt-dlp is not installed (used for URL extraction).\n"
            "  Install with: pip install yt-dlp"
        )

    ffmpeg_found = False
    result = subprocess.run(
        ["which", "ffmpeg"],
        capture_output=True,
        text=True
    )
    if result.returncode == 0:
        ffmpeg_path = result.stdout.strip()
        version_result = subprocess.run(
            [ffmpeg_path, "-version"],
            capture_output=True,
            text=True
        )
        if version_result.returncode == 0:
            version_line = version_result.stdout.split("\n")[0]
            print(f"[INFO] {version_line}")
        ffmpeg_found = True

    if not ffmpeg_found:
        errors.append(
            "ffmpeg is not installed (required for download + conversion).\n"
            "  Install on macOS: brew install ffmpeg\n"
            "  Install on Ubuntu: sudo apt install ffmpeg\n"
            "  Install on Windows: choco install ffmpeg"
        )

    if errors:
        print("\n[ERROR] Missing dependencies:\n")
        for err in errors:
            print(err)
        sys.exit(1)


def download_with_ffmpeg(stream_info, output_path, format_name, audio_bitrate="192k",
                         video_quality="1080p", audio_only=False, apply_filter=False):
    """Use ffmpeg directly to download streams from URLs and convert to target format."""
    fmt = FORMAT_CONFIG[format_name]
    cmd = ["ffmpeg", "-y"]

    cmd.extend([
        "-user_agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    ])

    if audio_only:
        cmd.extend(["-i", stream_info["audio_url"]])
        cmd.extend([
            "-vn",
            "-acodec", fmt["audio_codec"],
            "-b:a", audio_bitrate,
            "-ar", "44100",
        ])
    else:
        cmd.extend(["-i", stream_info["video_url"]])
        cmd.extend(["-i", stream_info["audio_url"]])

        if apply_filter and video_quality:
            max_height = get_quality_filter_arg(video_quality)
            cmd.extend([
                "-vf", f"scale='if(gte(iw,ih)*-1,{max_height},-2)':'if(gte(ih,iw)*-1,{max_height},-2)'"
            ])

        cmd.extend([
            "-c:v", fmt["video_codec"],
            "-c:a", fmt["audio_codec"],
            "-b:a", audio_bitrate,
            "-preset", "medium",
            "-movflags", "+faststart",
        ])

    if fmt["container"] == "matroska":
        cmd.extend(["-f", "matroska"])
    elif fmt["container"] == "adts":
        cmd.extend(["-f", "adts"])
    elif fmt["container"] == "ipod":
        cmd.extend(["-f", "ipod"])

    cmd.append(output_path)

    print(f"[FFMPEG] Running: {' '.join(cmd[:10])} ...")
    print()

    process = subprocess.run(cmd, capture_output=False)

    if process.returncode != 0:
        print(f"\n[ERROR] ffmpeg failed with return code {process.returncode}")
        sys.exit(1)

    return True


def download_with_ytdlp_ffmpeg(url, output_path, format_name, audio_bitrate="192k",
                               video_quality="1080p", audio_only=False, video_codec=None):
    """Use yt-dlp with ffmpeg as the external converter/downloader."""
    from downloader.config import VIDEO_FORMATS

    fmt_config = FORMAT_CONFIG[format_name]
    if video_codec is None:
        video_codec = fmt_config.get("video_codec", "libx264")

    if audio_only:
        ytdlp_format = "bestaudio"
    else:
        max_height = get_quality_filter_arg(video_quality)
        # Prefer video streams that are native to the target container to avoid
        # codec-incompatible files (e.g. VP9/AV1 inside MP4 which many players
        # cannot play). Fall back to any bestvideo + re-encode if needed.
        if format_name == "mp4":
            ytdlp_format = (
                f"bestvideo[ext=mp4][height<={max_height}]+bestaudio[ext=m4a]/"
                f"bestvideo[ext=mp4][height<={max_height}]+bestaudio/"
                f"best[height<={max_height}]/best"
            )
        elif format_name == "webm":
            ytdlp_format = (
                f"bestvideo[ext=webm][height<={max_height}]+bestaudio[ext=webm]/"
                f"bestvideo[ext=webm][height<={max_height}]+bestaudio/"
                f"best[height<={max_height}]/best"
            )
        elif format_name == "mkv":
            ytdlp_format = (
                f"bestvideo[ext=webm][height<={max_height}]+bestaudio[ext=webm]/"
                f"bestvideo[ext=mkv][height<={max_height}]+bestaudio/"
                f"best[height<={max_height}]/best"
            )
        else:
            ytdlp_format = f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]/best"

    # Use output_path directly as the template - no %(ext)s substitution needed
    # since we already know the exact target extension we want.
    # Using %(ext)s caused issues where yt-dlp would produce filenames like
    # "[video]mp4.mp4" instead of "[video].mp4" because %(ext)s is replaced
    # with the extension without a dot.
    output_template = output_path

    cmd = [
        "yt-dlp",
        "-f", ytdlp_format,
        "-o", output_template,
        "--merge-output-format", format_name if format_name in VIDEO_FORMATS else "mp4",
        "--no-playlist",
        "--no-check-certificates",
    ]

    if audio_only:
        cmd.extend([
            "-x",
            "--audio-format", format_name,
            "--audio-quality", audio_bitrate.replace("k", ""),
        ])

    cmd.append(url)

    print(f"[YT-DLP+FFMPEG] Running download with ffmpeg backend...")
    print()

    process = subprocess.run(cmd, capture_output=False)

    if process.returncode != 0:
        print(f"\n[ERROR] Download failed with return code {process.returncode}")
        sys.exit(1)

    candidates = glob.glob(output_path + ".*")
    actual_file = None
    for c in candidates:
        if os.path.isfile(c) and os.path.getsize(c) > 0:
            actual_file = c
            break
    if actual_file and actual_file != output_path:
        os.rename(actual_file, output_path)
        print(f"[INFO] Renamed {actual_file} -> {output_path}")

    return True