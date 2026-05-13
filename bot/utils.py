"""
Utility and helper functions.

Formatting helpers, URL validators, progress parsers, file-size
formatters, and the output-file finder are all pure utilities with no
Telegram or async dependencies.
"""

import os
import re
import shutil
from pathlib import Path

from bot.config import (
    TEMP_BASE_DIR,
    YOUTUBE_PATTERN,
    YOUTUBE_PLAYLIST_PATTERN,
    YOUTUBE_VIDEO_WITH_LIST_PATTERN,
)


# ─── Temp-directory cleanup ─────────────────────────────────────────────────

def clear_temp_dir():
    """Delete all timestamped temp folders and return count of removed dirs."""
    if not TEMP_BASE_DIR.exists():
        return 0
    count = 0
    for entry in TEMP_BASE_DIR.iterdir():
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
            count += 1
    return count


# ─── URL helpers ────────────────────────────────────────────────────────────

def is_youtube_link(text: str) -> bool:
    """Check if text contains a YouTube link."""
    return bool(YOUTUBE_PATTERN.search(text))


def extract_youtube_link(text: str) -> str | None:
    """Extract YouTube link from text."""
    match = YOUTUBE_PATTERN.search(text)
    return match.group(0) if match else None


def is_playlist_url(url: str) -> bool:
    """Check if a URL is a YouTube playlist."""
    if YOUTUBE_PLAYLIST_PATTERN.search(url):
        return True
    if YOUTUBE_VIDEO_WITH_LIST_PATTERN.search(url):
        return True
    return False


# ─── Progress parsing ───────────────────────────────────────────────────────

def parse_progress_line(line: str | None) -> dict | None:
    """Parse progress info from yt-dlp or ffmpeg output lines.

    Returns a dict with keys: source, phase, percent, speed, eta, time, raw.
    """
    if not line:
        return None

    # yt-dlp: [download]  78.3% of 45.23MiB at 12.45MiB/s ETA 00:12
    m = re.search(
        r'\[download\]\s+([\d\.]+)%\s+of\s+([\d\.]+[A-Za-z]+)\s+at\s+([\d\.]+[A-Za-z]+/s)\s+ETA\s+([0-9:]+)',
        line,
    )
    if m:
        return {
            'source': 'ytdlp',
            'phase': 'download',
            'percent': float(m.group(1)),
            'total_size': m.group(2),
            'speed': m.group(3),
            'eta': m.group(4),
            'raw': line,
        }

    # yt-dlp simple percent: [download]  78.3% of 45.23MiB
    m = re.search(r'\[download\]\s+([\d\.]+)%', line)
    if m:
        return {
            'source': 'ytdlp',
            'phase': 'download',
            'percent': float(m.group(1)),
            'raw': line,
        }

    # yt-dlp: [download] Destination:
    m = re.search(r'\[download\]\s+Destination:', line, re.IGNORECASE)
    if m:
        return {'source': 'ytdlp', 'phase': 'info', 'raw': line}

    # yt-dlp: [Merger] Merging formats
    m = re.search(r'\[Merger\]', line, re.IGNORECASE)
    if m:
        return {'source': 'ytdlp', 'phase': 'merge', 'raw': line}

    # yt-dlp: [ExtractAudio] / [PostProcess]
    m = re.search(r'\[(?:ExtractAudio|PostProcess|ConvertVideos)\]', line, re.IGNORECASE)
    if m:
        return {'source': 'ytdlp', 'phase': 'postprocess', 'raw': line}

    # yt-dlp: [info] URL / title extraction
    m = re.search(r'\[youtube\]|\[info\]', line, re.IGNORECASE)
    if m:
        return {'source': 'ytdlp', 'phase': 'info', 'raw': line}

    # ffmpeg: time=00:02:15.12 bitrate=1234.56kbits/s speed=1.23x
    m = re.search(r'time=(\d+:\d+:\d+\.\d+)', line)
    if m:
        speed_m = re.search(r'speed=\s*([\d\.]+)x', line)
        return {
            'source': 'ffmpeg',
            'phase': 'convert',
            'time': m.group(1),
            'speed': speed_m.group(1) if speed_m else None,
            'raw': line,
        }

    # Generic percent
    m = re.search(r'([0-9]{1,3})%\s', line)
    if m:
        return {
            'source': 'generic',
            'phase': 'unknown',
            'percent': float(m.group(1)),
            'raw': line,
        }

    return None


# ─── Formatting helpers ─────────────────────────────────────────────────────

def format_progress_bar(percent: float, width: int = 10) -> str:
    """Create a text-based progress bar.

    Example: format_progress_bar(78.3) -> '▓▓▓▓▓▓▓▓▒▒'
    """
    filled = int(width * percent / 100)
    empty = width - filled
    return '\u2593' * filled + '\u2591' * empty


def format_duration(seconds) -> str:
    """Format duration in seconds as HH:MM:SS or M:SS."""
    if not seconds:
        return ""
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_file_size(size_bytes: int) -> str:
    """Format file size in bytes as human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def format_view_count(count) -> str:
    """Format view count as human-readable string."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    elif count >= 1_000:
        return f"{count / 1_000:.1f}K"
    return str(count)


# ─── Output file finder ─────────────────────────────────────────────────────

def find_output_file(output_dir: str, settings: dict) -> str | None:
    """Find the downloaded file in the output directory."""
    if settings["mode"] == "audio":
        ext = f".{settings['audio_format']}"
    else:
        ext = f".{settings['video_format']}"

    for f in Path(output_dir).iterdir():
        if f.is_file() and f.suffix == ext:
            return str(f)

    # Fallback: find any recent media file
    for ext in [".mp4", ".mkv", ".webm", ".m4a", ".mp3", ".flac", ".aac", ".ogg"]:
        for f in Path(output_dir).iterdir():
            if f.is_file() and f.suffix == ext:
                return str(f)

    return None


def find_playlist_output_files(output_dir: str, settings: dict) -> list[str]:
    """Find all downloaded files in the playlist output directory."""
    files = []

    if settings["mode"] == "audio":
        expected_ext = f".{settings['audio_format']}"
    else:
        expected_ext = f".{settings['video_format']}"

    for root, _, filenames in os.walk(output_dir):
        for filename in filenames:
            filepath = os.path.join(root, filename)
            if filename.endswith(expected_ext):
                files.append(filepath)
            elif any(
                filename.endswith(ext)
                for ext in [".mp4", ".mkv", ".webm", ".m4a", ".mp3", ".flac", ".aac", ".ogg"]
            ):
                files.append(filepath)

    return files