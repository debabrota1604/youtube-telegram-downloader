#!/usr/bin/env python3
"""
Telegram Bot for YouTube Video/Audio Download
Sends a YouTube link to the bot, it downloads and converts on your Mac,
then sends the file back to you via Telegram.

Usage:
    1. Create a bot via @BotFather on Telegram and get the TOKEN
    2. Get your chat_id by sending /start to the bot
    3. Set BOT_TOKEN in .env or export it
    4. Run: python3 youtube_telegram_bot.py

Commands:
    /start       - Start the bot, shows your chat ID
    /video       - Download as video (MP4/H.264/AAC, 1080p)
    /audio       - Download as audio only (M4A/AAC, 256k)
    /playlist    - Download entire playlist
    /settings    - Show current settings
    /quality     - Set video quality: /quality 720p
    /bitrate     - Set audio bitrate: /bitrate 320k
    /format      - Set format: /format mkv or /format flac
    /queue       - Show download queue
    /queue-clear - Clear download queue
    /cancel      - Cancel current download
    /help        - Show help

Features:
    - Preview before download (thumbnail + metadata)
    - Real-time progress tracking with visual progress bar
    - Cancel downloads anytime
    - Queue multiple downloads
    - Auto-embed metadata (title, artist, album art) for Android Auto

You can also just paste a YouTube link and the bot will show a preview
before downloading using the last used mode (video/audio).
"""

import os
import re
import subprocess
import sys
import asyncio
import tempfile
import shutil
import time
import uuid
import json
import logging
from datetime import datetime
from pathlib import Path
from collections import deque
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
import telegram.error

# Load environment variables from .env file
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Default user IDs (empty = allow all users)
ALLOWED_USERS = os.getenv("ALLOWED_USERS", "").split(",")
ALLOWED_USERS = [u.strip() for u in ALLOWED_USERS if u.strip()]

# Storage for per-user settings and state
USER_STATE = {}
USER_SETTINGS = {}

# Download tracking
DOWNLOAD_TASKS = {}       # task_id -> task_info dict
DOWNLOAD_QUEUE = {}       # user_id -> deque of queue items
IS_DOWNLOADING = {}       # user_id -> task_id (True-like if downloading)
MAX_QUEUE_SIZE = 20

# Default settings
DEFAULT_SETTINGS = {
    "mode": "video",          # video or audio
    "video_quality": "1080p",
    "audio_bitrate": "256k",
    "audio_format": "m4a",    # for audio-only: m4a, mp3, flac
    "video_format": "mp4",    # for video: mp4, mkv
}

# YouTube URL pattern
YOUTUBE_PATTERN = re.compile(
    r"(https?://)?(www\.)?(youtube\.com/(watch\?v=|shorts/|live/|embed/|v/|playlist\?list=)|youtu\.be/)[a-zA-Z0-9_-]+"
)

# YouTube playlist URL pattern
YOUTUBE_PLAYLIST_PATTERN = re.compile(
    r"(https?://)?(www\.)?youtube\.com/playlist\?list=[a-zA-Z0-9_-]+"
)

# YouTube video with playlist parameter
YOUTUBE_VIDEO_WITH_LIST_PATTERN = re.compile(
    r"(https?://)?(www\.)?youtube\.com/watch\?v=[a-zA-Z0-9_-]+&list=[a-zA-Z0-9_-]+"
)

# Path to main.py downloader
DOWNLOADER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")

# Temp directory for downloads (temp/{timestamp} per request)
TEMP_BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp")


# ─── Custom Exception ────────────────────────────────────────────────────────

class DownloadCancelledError(Exception):
    """Raised when a download is cancelled by the user."""
    pass


# ─── Utility Functions ───────────────────────────────────────────────────────

def clear_temp_dir():
    """Delete all timestamped temp folders and return count of removed dirs."""
    if not os.path.exists(TEMP_BASE_DIR):
        return 0
    count = 0
    for entry in os.listdir(TEMP_BASE_DIR):
        entry_path = os.path.join(TEMP_BASE_DIR, entry)
        if os.path.isdir(entry_path):
            shutil.rmtree(entry_path, ignore_errors=True)
            count += 1
    return count


def get_user_settings(user_id):
    """Get or create settings for a user."""
    if user_id not in USER_SETTINGS:
        USER_SETTINGS[user_id] = DEFAULT_SETTINGS.copy()
    return USER_SETTINGS[user_id]


def is_authorized(user_id):
    """Check if user is authorized."""
    if not ALLOWED_USERS:
        return True
    return str(user_id) in ALLOWED_USERS


def is_youtube_link(text):
    """Check if text contains a YouTube link."""
    return bool(YOUTUBE_PATTERN.search(text))


def extract_youtube_link(text):
    """Extract YouTube link from text."""
    match = YOUTUBE_PATTERN.search(text)
    return match.group(0) if match else None


def is_playlist_url(url):
    """Check if a URL is a YouTube playlist."""
    if YOUTUBE_PLAYLIST_PATTERN.search(url):
        return True
    if YOUTUBE_VIDEO_WITH_LIST_PATTERN.search(url):
        return True
    return False


def parse_progress_line(line):
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

    # yt-dlp: [info] extracting video info / downloading webpage
    m = re.search(r'\[download\]\s+Destination:', line, re.IGNORECASE)
    if m:
        return {
            'source': 'ytdlp',
            'phase': 'info',
            'raw': line,
        }

    # yt-dlp: [Merger] Merging formats
    m = re.search(r'\[Merger\]', line, re.IGNORECASE)
    if m:
        return {
            'source': 'ytdlp',
            'phase': 'merge',
            'raw': line,
        }

    # yt-dlp: [ExtractAudio] / [PostProcess]
    m = re.search(r'\[(?:ExtractAudio|PostProcess|ConvertVideos)\]', line, re.IGNORECASE)
    if m:
        return {
            'source': 'ytdlp',
            'phase': 'postprocess',
            'raw': line,
        }

    # yt-dlp: [info] URL / title extraction
    m = re.search(r'\[youtube\]|\[info\]', line, re.IGNORECASE)
    if m:
        return {
            'source': 'ytdlp',
            'phase': 'info',
            'raw': line,
        }

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


def format_progress_bar(percent, width=10):
    """Create a text-based progress bar.

    Example: format_progress_bar(78.3) -> '\u2593\u2593\u2593\u2593\u2593\u2593\u2593\u2593\u2591\u2591'
    """
    filled = int(width * percent / 100)
    empty = width - filled
    return '\u2593' * filled + '\u2591' * empty


def format_duration(seconds):
    """Format duration in seconds as HH:MM:SS or M:SS."""
    if not seconds:
        return ""
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_file_size(size_bytes):
    """Format file size in bytes as human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def format_view_count(count):
    """Format view count as human-readable string."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    elif count >= 1_000:
        return f"{count / 1_000:.1f}K"
    return str(count)


# ─── Video Info Extraction ───────────────────────────────────────────────────

def get_video_info(url, timeout=30):
    """Extract video metadata using yt-dlp. Returns dict or None on failure."""
    try:
        cmd = [
            "yt-dlp", "--dump-json", "--no-download",
            "--no-check-certificates", url
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            return None

        # Parse JSON output (yt-dlp may output warnings before the JSON line)
        info = None
        for line in result.stdout.strip().split("\n"):
            try:
                info = json.loads(line)
                break
            except json.JSONDecodeError:
                continue

        if info is None:
            return None

        return {
            "title": info.get("title", ""),
            "uploader": info.get("uploader", "") or info.get("channel", ""),
            "duration": info.get("duration", 0),
            "thumbnail": info.get("thumbnail", ""),
            "view_count": info.get("view_count", 0),
            "upload_date": info.get("upload_date", ""),
        }
    except (subprocess.TimeoutExpired, Exception):
        return None


# ─── Metadata Embedding ──────────────────────────────────────────────────────

def _download_thumbnail(thumbnail_url, timeout=10):
    """Download thumbnail image data. Returns bytes or None."""
    if not thumbnail_url:
        return None
    try:
        import urllib.request
        req = urllib.request.Request(thumbnail_url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        })
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read()
    except Exception:
        return None


def _embed_mp3_metadata(file_path, title, artist, year, thumbnail_data):
    """Embed ID3v2.3 tags into MP3 file."""
    try:
        from mutagen.id3 import ID3, APIC, TIT2, TPE1, TDRC
        from mutagen.mp3 import MP3
        mp = MP3(file_path, ID3=ID3)
        try:
            mp.add_tags()
        except Exception:
            pass
        if title:
            mp.tags.add(TIT2(encoding=3, text=[title]))
        if artist:
            mp.tags.add(TPE1(encoding=3, text=[artist]))
        if year:
            mp.tags.add(TDRC(encoding=3, text=[year]))
        if thumbnail_data:
            mp.tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=thumbnail_data))
        mp.save(v2_version=3)
    except Exception:
        pass


def _embed_m4a_metadata(file_path, title, artist, year, thumbnail_data):
    """Embed MP4 atoms into M4A/AAC file."""
    try:
        from mutagen.mp4 import MP4, MP4Cover
        m4 = MP4(file_path)
        if title:
            m4['\xa9nam'] = [title]
        if artist:
            m4['\xa9ART'] = [artist]
        if year:
            m4['\xa9day'] = [year]
        if thumbnail_data:
            m4['covr'] = [MP4Cover(thumbnail_data, imageformat=MP4Cover.FORMAT_JPEG)]
        m4.save()
    except Exception:
        pass


def _embed_flac_metadata(file_path, title, artist, year, thumbnail_data):
    """Embed Vorbis comments into FLAC file."""
    try:
        from mutagen.flac import FLAC, Picture
        f = FLAC(file_path)
        if title:
            f['title'] = [title]
        if artist:
            f['artist'] = [artist]
        if year:
            f['date'] = [year]
        if thumbnail_data:
            pic = Picture()
            pic.data = thumbnail_data
            pic.type = 3
            pic.mime = 'image/jpeg'
            pic.desc = 'Cover'
            f.add_picture(pic)
        f.save()
    except Exception:
        pass


def embed_audio_metadata(file_path, video_info, audio_format):
    """Embed metadata (title, artist, year, album art) into audio file.

    Supports MP3 (ID3v2.3), M4A/AAC (MP4 atoms), and FLAC (Vorbis comments).
    Silently skips if mutagen is not installed or on any error.
    """
    if not video_info:
        return

    # Lazy import - skip entirely if mutagen not available
    try:
        import mutagen  # noqa: F401
    except ImportError:
        return

    title = video_info.get("title", "")
    artist = video_info.get("uploader", "")
    year = video_info.get("upload_date", "")[:4] if video_info.get("upload_date") else None
    thumbnail_url = video_info.get("thumbnail", "")

    # Download thumbnail
    thumbnail_data = _download_thumbnail(thumbnail_url) if thumbnail_url else None

    try:
        ext = Path(file_path).suffix.lower()

        if ext == ".mp3":
            _embed_mp3_metadata(file_path, title, artist, year, thumbnail_data)
        elif ext in (".m4a", ".aac"):
            _embed_m4a_metadata(file_path, title, artist, year, thumbnail_data)
        elif ext == ".flac":
            _embed_flac_metadata(file_path, title, artist, year, thumbnail_data)
        else:
            # Fallback: try easy tags for other formats
            try:
                from mutagen import File as MutagenFile
                easy = MutagenFile(file_path, easy=True)
                if easy:
                    if title:
                        easy["title"] = [title]
                    if artist:
                        easy["artist"] = [artist]
                    easy.save()
            except Exception:
                pass
    except Exception:
        pass  # Silently fail - metadata embedding is optional


# ─── Queue Management ────────────────────────────────────────────────────────

def get_queue(user_id):
    """Get or create the download queue for a user."""
    if user_id not in DOWNLOAD_QUEUE:
        DOWNLOAD_QUEUE[user_id] = deque()
    return DOWNLOAD_QUEUE[user_id]


def add_to_queue(user_id, url, settings, chat_id):
    """Add a URL to the user's download queue.

    Returns (success: bool, message: str).
    """
    queue = get_queue(user_id)
    if len(queue) >= MAX_QUEUE_SIZE:
        return False, (
            f"❌ Queue is full (max {MAX_QUEUE_SIZE} items).\n"
            f"Use <code>/queue-clear</code> to free space.",
        )

    queue.append({
        "url": url,
        "settings": settings.copy(),
        "chat_id": chat_id,
    })

    return True, f"⏳ Download in progress. Added to queue (position <code>{len(queue)}</code>)."


def clear_queue(user_id):
    """Clear the user's download queue. Returns the number of items removed."""
    queue = DOWNLOAD_QUEUE.get(user_id, deque())
    count = len(queue)
    DOWNLOAD_QUEUE[user_id] = deque()
    return count


# ─── Downloader Functions ────────────────────────────────────────────────────

async def run_downloader(url, settings, output_dir, task_id=None, progress_callback=None):
    """Run main.py to download and convert the YouTube video.

    Streams stdout and calls progress_callback(parsed_dict) for progress lines.
    Checks DOWNLOAD_TASKS[task_id]["cancelled"] for cancellation.
    """
    cmd = [
        sys.executable, DOWNLOADER_SCRIPT,
        url,
        "--method", "ytdlp",
        "--output", output_dir,
    ]

    mode = settings["mode"]
    if mode == "audio":
        cmd.extend(["--audio-only", "--audio-format", settings["audio_format"],
                     "--audio-bitrate", settings["audio_bitrate"]])
    else:
        cmd.extend(["--format", settings["video_format"],
                     "--video-quality", settings["video_quality"],
                     "--audio-bitrate", settings["audio_bitrate"]])

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    # Store process reference for cancellation
    if task_id and task_id in DOWNLOAD_TASKS:
        DOWNLOAD_TASKS[task_id]["process"] = process

    collected = []
    last_update = 0
    last_percent = None

    while True:
        line = await process.stdout.readline()
        if not line:
            break

        # Check for cancellation
        if task_id and DOWNLOAD_TASKS.get(task_id, {}).get("cancelled"):
            if process.returncode is None:
                try:
                    process.kill()
                except Exception:
                    pass
            raise DownloadCancelledError()

        text = line.decode(errors="ignore").rstrip()
        collected.append(text)

        parsed = parse_progress_line(text)
        if parsed and progress_callback:
            now = time.time()
            percent = parsed.get("percent")
            if percent is not None:
                if last_percent is None or abs(percent - (last_percent or 0)) >= 1 or (now - last_update) >= 2:
                    try:
                        await progress_callback(parsed)
                    except Exception:
                        pass
                    last_update = now
                    last_percent = percent
            else:
                if now - last_update >= 3:
                    try:
                        await progress_callback(parsed)
                    except Exception:
                        pass
                    last_update = now

    await process.wait()

    # Check for cancellation after process exits
    if task_id and (DOWNLOAD_TASKS.get(task_id, {}).get("cancelled") or process.returncode < 0):
        raise DownloadCancelledError()

    output_text = "\n".join(collected)
    if process.returncode != 0:
        raise RuntimeError(f"Download failed:\n{output_text[-2000:]}")
    return output_text


async def run_playlist_downloader(url, settings, output_dir, task_id=None, progress_callback=None):
    """Run main.py to download an entire YouTube playlist.

    Streams stdout and calls progress_callback(parsed_dict) for progress lines.
    Checks DOWNLOAD_TASKS[task_id]["cancelled"] for cancellation.
    """
    cmd = [
        sys.executable, DOWNLOADER_SCRIPT,
        url,
        "--playlist",
        "--method", "ytdlp",
        "--output", output_dir,
    ]

    submode = settings.get("_playlist_submode", "video")
    if submode == "audio":
        cmd.extend(["--audio-only", "--audio-format", settings["audio_format"],
                     "--audio-bitrate", settings["audio_bitrate"]])
    else:
        cmd.extend(["--format", settings["video_format"],
                     "--video-quality", settings["video_quality"],
                     "--audio-bitrate", settings["audio_bitrate"]])

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    if task_id and task_id in DOWNLOAD_TASKS:
        DOWNLOAD_TASKS[task_id]["process"] = process

    collected = []
    last_update = 0

    while True:
        line = await process.stdout.readline()
        if not line:
            break

        # Check for cancellation
        if task_id and DOWNLOAD_TASKS.get(task_id, {}).get("cancelled"):
            if process.returncode is None:
                try:
                    process.kill()
                except Exception:
                    pass
            raise DownloadCancelledError()

        text = line.decode(errors="ignore").rstrip()
        collected.append(text)

        parsed = parse_progress_line(text)
        if parsed and progress_callback:
            now = time.time()
            if parsed.get("percent") is not None:
                if now - last_update >= 2:
                    try:
                        await progress_callback(parsed)
                    except Exception:
                        pass
                    last_update = now
            else:
                if now - last_update >= 3:
                    try:
                        await progress_callback(parsed)
                    except Exception:
                        pass
                    last_update = now

    await process.wait()

    # Check for cancellation after process exits
    if task_id and (DOWNLOAD_TASKS.get(task_id, {}).get("cancelled") or process.returncode < 0):
        raise DownloadCancelledError()

    output_text = "\n".join(collected)
    if process.returncode != 0:
        raise RuntimeError(f"Playlist download failed:\n{output_text[-2000:]}")
    return output_text


# ─── Output File Finding ─────────────────────────────────────────────────────

def find_output_file(output_dir, settings):
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


def find_playlist_output_files(output_dir, settings):
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
            elif any(filename.endswith(ext) for ext in [".mp4", ".mkv", ".webm", ".m4a", ".mp3", ".flac", ".aac", ".ogg"]):
                files.append(filepath)

    return files


# ─── Telegram Commands ───────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    user_id = update.effective_user.id

    if not is_authorized(user_id):
        await update.message.reply_text("❌ You are not authorized to use this bot.")
        return

    name = update.effective_user.first_name
    keyboard = [
        [
            InlineKeyboardButton("🎬 Video (MP4)", callback_data="mode:video"),
            InlineKeyboardButton("🎵 Audio (M4A)", callback_data="mode:audio"),
        ],
        [
            InlineKeyboardButton("📺 Playlist", callback_data="mode:playlist"),
            InlineKeyboardButton("⚙️ Settings", callback_data="settings"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        f"👋 Hello {name}!\n\n"
        f"I can download YouTube videos/audio for your Android Auto.\n\n"
        f"📎 Just send me a YouTube link!\n"
        f"You'll see a preview before downloading.\n\n"
        f"✨ Features:\n"
        f"📸 Preview  •  📊 Progress  •  🛑 Cancel  •  📋 Queue\n"
        f"🏷️ Auto-embeds metadata (title, artist, album art)\n\n"
        f"Or choose a mode below:\n\n"
        f"Your Chat ID: <code>{user_id}</code>"
    )
    await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    help_text = (
        "📱 <b>YouTube Downloader Bot</b>\n\n"
        "<b>Quick Use:</b> Just paste a YouTube link!\n"
        "You'll see a preview before downloading.\n"
        "Playlist URLs are auto-detected.\n\n"
        "<b>Commands:</b>\n"
        "<code>/video</code> — Set mode to video download\n"
        "<code>/audio</code> — Set mode to audio-only\n"
        "<code>/playlist</code> — Set mode to playlist download\n"
        "<code>/quality 720p</code> — Set video quality\n"
        "<code>/bitrate 320k</code> — Set audio bitrate\n"
        "<code>/format mp3</code> — Set output format\n"
        "<code>/settings</code> — Show current settings\n"
        "<code>/queue</code> — Show download queue\n"
        "<code>/queue-clear</code> — Clear download queue\n"
        "<code>/cancel</code> — Cancel current download\n\n"
        "<b>Features:</b>\n"
        "📸 Preview before download\n"
        "📊 Real-time progress tracking\n"
        "🛑 Cancel downloads anytime\n"
        "📋 Queue multiple downloads\n"
        "🏷️ Auto-embed metadata (title, artist, album art)\n\n"
        "<b>Supported:</b>\n"
        "Video: mp4, mkv | Quality: 360p-2160p\n"
        "Audio: m4a, mp3, flac | Bitrate: 128k-320k\n\n"
        "<b>Playlist:</b>\n"
        "Send a playlist URL or use /playlist mode.\n"
        "Each video is sent individually.\n\n"
        "Files are sent directly to you via Telegram.\n"
        "Max file size: 2GB"
    )
    await update.message.reply_text(help_text, parse_mode="HTML")


async def cmd_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set mode to video."""
    settings = get_user_settings(update.effective_user.id)
    settings["mode"] = "video"
    await update.message.reply_text(
        "🎬 Mode set to <b>Video</b>\n"
        f"Format: {settings['video_format']} | Quality: {settings['video_quality']}\n\n"
        "Send a YouTube link to start downloading!",
        parse_mode="HTML",
    )


async def cmd_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set mode to audio."""
    settings = get_user_settings(update.effective_user.id)
    settings["mode"] = "audio"
    await update.message.reply_text(
        "🎵 Mode set to <b>Audio Only</b>\n"
        f"Format: {settings['audio_format']} | Bitrate: {settings['audio_bitrate']}\n\n"
        "Send a YouTube link to start downloading!",
        parse_mode="HTML",
    )


async def cmd_playlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set mode to playlist."""
    settings = get_user_settings(update.effective_user.id)
    settings["_playlist_submode"] = settings["mode"]
    settings["mode"] = "playlist"
    submode_label = "🎵 Audio" if settings["_playlist_submode"] == "audio" else "🎬 Video"
    await update.message.reply_text(
        f"📺 Mode set to <b>Playlist Download</b> ({submode_label})\n\n"
        "Send a YouTube playlist URL to download all videos.\n"
        "Each video will be sent individually.\n\n"
        "Tip: Use /audio before /playlist for audio-only downloads.",
        parse_mode="HTML",
    )


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current settings."""
    settings = get_user_settings(update.effective_user.id)
    text = (
        "⚙️ <b>Current Settings</b>\n\n"
        f"Mode: <b>{'🎬 Video' if settings['mode'] == 'video' else '🎵 Audio' if settings['mode'] == 'audio' else '📺 Playlist'}</b>\n"
    )
    if settings["mode"] == "video":
        text += f"Video Format: <code>{settings['video_format']}</code>\n"
        text += f"Video Quality: <code>{settings['video_quality']}</code>\n"
    elif settings["mode"] == "audio":
        text += f"Audio Format: <code>{settings['audio_format']}</code>\n"
    else:
        text += f"Video Format: <code>{settings['video_format']}</code>\n"
        text += f"Video Quality: <code>{settings['video_quality']}</code>\n"
        text += f"Audio Format: <code>{settings['audio_format']}</code>\n"
    text += f"Audio Bitrate: <code>{settings['audio_bitrate']}</code>\n\n"
    text += "Use /quality, /bitrate, /format to change settings."
    await update.message.reply_text(text, parse_mode="HTML")


async def cmd_quality(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set video quality."""
    if not context.args:
        await update.message.reply_text("Usage: <code>/quality 720p</code>", parse_mode="HTML")
        return
    quality = context.args[0].lower()
    valid = ["360p", "480p", "720p", "1080p", "1440p", "2160p"]
    if quality not in valid:
        await update.message.reply_text(
            f"Invalid quality. Choose from: {', '.join(valid)}"
        )
        return
    settings = get_user_settings(update.effective_user.id)
    settings["video_quality"] = quality
    await update.message.reply_text(f"✅ Video quality set to <b>{quality}</b>", parse_mode="HTML")


async def cmd_bitrate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set audio bitrate."""
    if not context.args:
        await update.message.reply_text("Usage: <code>/bitrate 256k</code>", parse_mode="HTML")
        return
    bitrate = context.args[0].lower()
    valid = ["128k", "192k", "256k", "320k"]
    if bitrate not in valid:
        await update.message.reply_text(
            f"Invalid bitrate. Choose from: {', '.join(valid)}"
        )
        return
    settings = get_user_settings(update.effective_user.id)
    settings["audio_bitrate"] = bitrate
    await update.message.reply_text(f"✅ Audio bitrate set to <b>{bitrate}</b>", parse_mode="HTML")


async def cmd_format(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set output format."""
    if not context.args:
        await update.message.reply_text("Usage: <code>/format mp4</code>", parse_mode="HTML")
        return
    fmt = context.args[0].lower()
    settings = get_user_settings(update.effective_user.id)
    if settings["mode"] == "video":
        valid = ["mp4", "mkv", "webm"]
        if fmt not in valid:
            await update.message.reply_text(
                f"Invalid video format. Choose from: {', '.join(valid)}"
            )
            return
        settings["video_format"] = fmt
    else:
        valid = ["m4a", "mp3", "flac", "aac", "ogg"]
        if fmt not in valid:
            await update.message.reply_text(
                f"Invalid audio format. Choose from: {', '.join(valid)}"
            )
            return
        settings["audio_format"] = fmt
    await update.message.reply_text(f"✅ Format set to <b>{fmt}</b>", parse_mode="HTML")


async def cmd_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the download queue."""
    user_id = update.effective_user.id
    queue = get_queue(user_id)

    if not queue:
        await update.message.reply_text("📋 Your download queue is empty.")
        return

    text = f"📋 <b>Download Queue</b> ({len(queue)} items)\n\n"

    # Show current download if any
    task_id = IS_DOWNLOADING.get(user_id)
    if task_id:
        text += "🔄 <b>Currently downloading...</b>\n\n"

    for i, item in enumerate(queue):
        url = item["url"]
        # Extract video ID for compact display
        vid_match = re.search(r'[vV]=([a-zA-Z0-9_-]+)', url)
        short_id = vid_match.group(1) if vid_match else url[:25]
        text += f"  {i + 1}. <code>{short_id}</code>\n"

    keyboard = [[InlineKeyboardButton("🗑️ Clear Queue", callback_data="queue:clear")]]

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cmd_queue_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear the download queue."""
    user_id = update.effective_user.id
    count = clear_queue(user_id)
    await update.message.reply_text(
        f"🗑️ Queue cleared ({count} item{'s' if count != 1 else ''} removed)."
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel the current download."""
    user_id = update.effective_user.id
    task_id = IS_DOWNLOADING.get(user_id)

    if not task_id:
        await update.message.reply_text("⚡ No active download to cancel.")
        return

    task = DOWNLOAD_TASKS.get(task_id)
    if not task:
        await update.message.reply_text("⚡ No active download to cancel.")
        IS_DOWNLOADING.pop(user_id, None)
        return

    task["cancelled"] = True
    await update.message.reply_text("🛑 Cancellation initiated. Please wait...")


async def cmd_clear_cache(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear all temporary download files."""
    count = clear_temp_dir()
    if count > 0:
        await update.message.reply_text(
            f"🗑️ Cache cleared ({count} temp folder{'s' if count != 1 else ''} removed)."
        )
    else:
        await update.message.reply_text("📁 Cache is already empty.")


# ─── Preview ─────────────────────────────────────────────────────────────────

async def show_preview(context, chat_id, video_info, settings):
    """Show a preview of the video before downloading.

    Displays thumbnail (if available), title, duration, uploader,
    estimated file size, and Download/Cancel buttons.
    """
    title = (video_info.get("title", "Unknown") if video_info else "Unknown")
    uploader = video_info.get("uploader", "") if video_info else ""
    duration = video_info.get("duration", 0) if video_info else 0
    thumbnail = video_info.get("thumbnail", "") if video_info else ""
    view_count = video_info.get("view_count", 0) if video_info else 0

    dur_str = format_duration(duration) if duration else ""

    # Estimate file size
    mode = settings["mode"]
    if mode == "audio":
        bitrate_kbps = int(settings["audio_bitrate"].replace("k", ""))
        estimated_mb = duration * bitrate_kbps * 1000 / 8 / 1024 / 1024 if duration else 0
    else:
        quality = settings["video_quality"]
        mb_per_min = {"360p": 3, "480p": 5, "720p": 8, "1080p": 12, "1440p": 20, "2160p": 40}
        estimated_mb = (duration / 60) * mb_per_min.get(quality, 10) if duration else 0

    # Build caption
    caption_parts = [f"📎 <b>Preview</b>"]
    if title and title != "Unknown":
        # Truncate long titles
        display_title = title[:60] + "..." if len(title) > 60 else title
        caption_parts.append(f"\n🎬 <b>{display_title}</b>")
    if uploader:
        caption_parts.append(f"👤 {uploader}")
    if dur_str:
        caption_parts.append(f"⏱️ {dur_str}")
    if view_count:
        caption_parts.append(f"👁️ {format_view_count(view_count)}")

    fmt_label = settings["audio_format"].upper() if mode == "audio" else settings["video_format"].upper()
    caption_parts.append(f"📁 {fmt_label} ≈ {estimated_mb:.1f} MB")
    caption_parts.append("\n\nTap ✅ to start downloading")

    caption = "\n".join(caption_parts)

    # Create buttons
    keyboard = [[
        InlineKeyboardButton("✅ Download", callback_data="preview:confirm"),
        InlineKeyboardButton("❌ Cancel", callback_data="preview:cancel"),
    ]]

    # Send with thumbnail if available
    if thumbnail:
        try:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=thumbnail,
                caption=caption,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return
        except telegram.error.BadRequest:
            pass  # Thumbnail URL invalid, fall back to text

    # Fallback: text-only preview
    await context.bot.send_message(
        chat_id=chat_id,
        text=caption,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ─── Download And Send ───────────────────────────────────────────────────────

async def download_and_send(context, user_id, chat_id, url, settings,
                            video_info=None, status_msg=None, silent=False):
    """Download a YouTube video/audio and send it to the user via Telegram.

    This is the core download function used by both interactive downloads
    (after preview confirmation) and queue processing.

    Args:
        context: Telegram context
        user_id: User ID
        chat_id: Chat ID
        url: YouTube URL
        settings: User settings dict
        video_info: Pre-extracted video info (optional, for metadata)
        status_msg: Existing message to update (optional, creates new if None)
        silent: If True, skip the final "Done!" message (used for queue items)
    """
    task_id = str(uuid.uuid4())[:12]
    # Create temp/{timestamp} folder for this request
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmpdir = os.path.join(TEMP_BASE_DIR, timestamp)
    os.makedirs(tmpdir, exist_ok=True)

    mode = settings["mode"]
    mode_emoji = "🎬" if mode == "video" else "🎵"

    # Get title for display
    title = (video_info.get("title", "") if video_info else "")
    display_title = (title[:50] + "..." if len(title) > 50 else title) if title else "File"

    # Log download start
    print(f"\n{'─' * 40}")
    print(f"[{task_id}] 📥 Download started: {display_title}")
    print(f"[{task_id}] Mode: {mode} | User: @{user_id}")
    print(f"{'─' * 40}")

    # Register task for cancellation tracking
    DOWNLOAD_TASKS[task_id] = {
        "user_id": user_id,
        "process": None,
        "status_msg": status_msg,
        "temp_dir": tmpdir,
        "output_dir": tmpdir,
        "settings": settings.copy(),
        "cancelled": False,
    }
    IS_DOWNLOADING[user_id] = task_id

    # Create cancel button keyboard
    cancel_keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ Cancel", callback_data=f"download:cancel:{task_id}")
    ]])

    try:
        # Phase 1: Start download
        start_text = (
            f"{mode_emoji} <b>Downloading</b>\n\n"
            f"📁 {display_title}\n"
            f"⏳ Extracting video info..."
        )

        if not status_msg:
            status_msg = await context.bot.send_message(
                chat_id=chat_id,
                text=start_text,
                parse_mode="HTML",
                reply_markup=cancel_keyboard,
            )
        else:
            try:
                await status_msg.edit_text(
                    start_text,
                    parse_mode="HTML",
                    reply_markup=cancel_keyboard,
                )
            except telegram.error.BadRequest:
                status_msg = await context.bot.send_message(
                    chat_id=chat_id,
                    text=start_text,
                    parse_mode="HTML",
                    reply_markup=cancel_keyboard,
                )

        DOWNLOAD_TASKS[task_id]["status_msg"] = status_msg

        # Log Phase 1
        print(f"[{task_id}] ⏳ Phase 1: Extracting video info...")

        # Phase 2: Download with progress tracking
        async def progress_callback(parsed):
            """Update the progress message with download status."""
            if DOWNLOAD_TASKS.get(task_id, {}).get("cancelled"):
                return

            current_msg = DOWNLOAD_TASKS[task_id].get("status_msg")
            if not current_msg:
                return

            try:
                phase = parsed.get("phase", "unknown")

                if parsed.get("source") == "ytdlp" and parsed.get("percent") is not None:
                    percent = parsed["percent"]
                    bar = format_progress_bar(percent)
                    speed = parsed.get("speed", "")
                    eta = parsed.get("eta", "")

                    text = (
                        f"{mode_emoji} <b>Downloading</b>\n\n"
                        f"{bar} <code>{percent:.1f}%</code>\n"
                    )
                    if speed:
                        text += f"📊 {speed}"
                    if eta:
                        text += f"  ⏱️ ETA: {eta}"
                    text += f"\n\n📁 {display_title}"

                    await current_msg.edit_text(text, parse_mode="HTML", reply_markup=cancel_keyboard)

                elif phase == "info":
                    text = f"{mode_emoji} <b>Fetching video info...</b>\n\n📁 {display_title}"
                    await current_msg.edit_text(text, parse_mode="HTML", reply_markup=cancel_keyboard)

                elif phase == "merge":
                    text = f"{mode_emoji} <b>Merging streams...</b>\n\n🔗 Combining video + audio\n\n📁 {display_title}"
                    await current_msg.edit_text(text, parse_mode="HTML", reply_markup=cancel_keyboard)

                elif phase == "postprocess":
                    text = f"{mode_emoji} <b>Post-processing...</b>\n\n🔧 Converting & optimizing\n\n📁 {display_title}"
                    await current_msg.edit_text(text, parse_mode="HTML", reply_markup=cancel_keyboard)

                elif parsed.get("source") == "ffmpeg":
                    elapsed = parsed.get("time", "")
                    speed = parsed.get("speed", "")

                    text = f"{mode_emoji} <b>Processing</b>\n\n"
                    if elapsed:
                        text += f"⏱️ {elapsed}"
                    if speed:
                        text += f"  ⚡ {speed}x"
                    text += f"\n\n📁 {display_title}"

                    await current_msg.edit_text(text, parse_mode="HTML", reply_markup=cancel_keyboard)

            except telegram.error.BadRequest:
                DOWNLOAD_TASKS[task_id]["status_msg"] = None

        # Run downloader
        print(f"[{task_id}] ⬇️  Phase 2: Downloading...")
        output = await run_downloader(url, settings, tmpdir, task_id=task_id, progress_callback=progress_callback)

        # Check for cancellation after download
        if DOWNLOAD_TASKS[task_id]["cancelled"]:
            raise DownloadCancelledError()

        # Phase 3: Find output file
        print(f"[{task_id}] 🔍 Phase 3: Finding output file...")
        current_msg = DOWNLOAD_TASKS[task_id].get("status_msg")
        if current_msg:
            try:
                await current_msg.edit_text(
                    f"{mode_emoji} <b>Finding output file...</b>",
                    parse_mode="HTML",
                    reply_markup=cancel_keyboard,
                )
            except (telegram.error.BadRequest):
                pass

        file_path = find_output_file(tmpdir, settings)
        if not file_path:
            raise RuntimeError("Download completed but output file not found.")

        file_size = os.path.getsize(file_path)
        file_size_mb = file_size / (1024 * 1024)
        file_name = Path(file_path).name
        print(f"[{task_id}] ✅ Found: {file_name} ({file_size_mb:.1f} MB)")

        # Phase 4: Embed metadata (for audio files)
        if mode == "audio" and video_info:
            print(f"[{task_id}] 🏷️  Phase 4: Embedding metadata...")
            current_msg = DOWNLOAD_TASKS[task_id].get("status_msg")
            if current_msg:
                try:
                    await current_msg.edit_text(
                        f"🏷️ <b>Embedding metadata...</b>\n📁 {file_name}",
                        parse_mode="HTML",
                        reply_markup=cancel_keyboard,
                    )
                except (telegram.error.BadRequest):
                    pass

            embed_audio_metadata(file_path, video_info, settings["audio_format"])

        # Phase 5: Upload to Telegram
        print(f"[{task_id}] 📤 Phase 5: Uploading to Telegram...")
        current_msg = DOWNLOAD_TASKS[task_id].get("status_msg")
        if current_msg:
            try:
                await current_msg.edit_text(
                    f"📤 <b>Uploading to Telegram...</b>\n"
                    f"📁 {file_name} ({format_file_size(file_size)})",
                    parse_mode="HTML",
                    reply_markup=cancel_keyboard,
                )
            except (telegram.error.BadRequest):
                pass

        # Send file
        with open(file_path, "rb") as f:
            if mode == "audio":
                send_title = video_info.get("title", "") if video_info else ""
                send_performer = video_info.get("uploader", "") if video_info else ""
                await context.bot.send_audio(
                    chat_id=chat_id,
                    audio=f,
                    filename=file_name,
                    title=send_title,
                    performer=send_performer,
                    timeout=600,
                )
            else:
                await context.bot.send_video(
                    chat_id=chat_id,
                    video=f,
                    filename=file_name,
                    supports_streaming=True,
                    timeout=600,
                )

        # Phase 6: Done
        print(f"[{task_id}] ✅ Done! Sent {file_name} ({file_size_mb:.1f} MB)")
        print(f"{'─' * 40}\n")

        if not silent and current_msg:
            try:
                await current_msg.edit_text(
                    f"✅ <b>Done!</b>\n\n"
                    f"📁 {file_name} ({format_file_size(file_size)})\n\n"
                    f"Send another link to download more.",
                    parse_mode="HTML",
                )
            except (telegram.error.BadRequest):
                pass

    except DownloadCancelledError:
        # Download was cancelled - cleanup handled in finally
        current_msg = DOWNLOAD_TASKS.get(task_id, {}).get("status_msg")
        if current_msg:
            try:
                await current_msg.edit_text(
                    "❌ Download cancelled.",
                    parse_mode="HTML",
                )
            except (telegram.error.BadRequest):
                pass

    except RuntimeError as e:
        error_msg = str(e)
        if len(error_msg) > 500:
            error_msg = error_msg[:497] + "..."

        current_msg = DOWNLOAD_TASKS.get(task_id, {}).get("status_msg")
        if current_msg:
            try:
                await current_msg.edit_text(
                    f"❌ <b>Error:</b>\n<code>{error_msg}</code>",
                    parse_mode="HTML",
                )
            except (telegram.error.BadRequest):
                pass

    except Exception as e:
        error_msg = str(e)[:500]

        current_msg = DOWNLOAD_TASKS.get(task_id, {}).get("status_msg")
        if current_msg:
            try:
                await current_msg.edit_text(
                    f"❌ <b>Unexpected error:</b>\n<code>{error_msg}</code>",
                    parse_mode="HTML",
                )
            except (telegram.error.BadRequest):
                pass

    finally:
        # Cleanup temp directory
        if tmpdir and os.path.exists(tmpdir):
            shutil.rmtree(tmpdir, ignore_errors=True)

        # Remove task tracking
        if DOWNLOAD_TASKS.get(task_id):
            del DOWNLOAD_TASKS[task_id]

        # Clear downloading flag
        if IS_DOWNLOADING.get(user_id) == task_id:
            del IS_DOWNLOADING[user_id]


# ─── Queue Drain ─────────────────────────────────────────────────────────────

async def drain_queue(user_id, context):
    """Process all items in the user's download queue."""
    queue = DOWNLOAD_QUEUE.get(user_id)
    if not queue:
        return

    chat_id = queue[0].get("chat_id")
    processed = 0
    failed = 0

    while queue:
        item = queue.popleft()
        url = item["url"]
        settings = item["settings"]

        try:
            # Extract video info for metadata embedding
            try:
                video_info = get_video_info(url)
            except Exception:
                video_info = None

            # Send notification for queue item
            notify_msg = await context.bot.send_message(
                chat_id=chat_id,
                text=f"📋 Processing queued item...",
            )

            # Download and send (silent mode - no "Done!" message per item)
            await download_and_send(
                context, user_id, chat_id, url, settings,
                video_info=video_info, status_msg=notify_msg, silent=True,
            )
            processed += 1

        except DownloadCancelledError:
            failed += 1
        except Exception as e:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"❌ Failed: <code>{url[:50]}</code>\n<code>{str(e)[:200]}</code>",
                    parse_mode="HTML",
                )
            except Exception:
                pass
            failed += 1
        finally:
            # Ensure IS_DOWNLOADING is cleared
            if user_id in IS_DOWNLOADING:
                del IS_DOWNLOADING[user_id]
            await asyncio.sleep(1)  # Rate limit delay between items

    # Notify completion
    if processed > 0:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"✅ <b>Queue complete!</b>\n"
                     f"📁 {processed} downloaded"
                     f"{', ❌ ' + str(failed) + ' failed' if failed else ''}.",
                parse_mode="HTML",
            )
        except Exception:
            pass


# ─── Message Handler ─────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle YouTube link messages."""
    user_id = update.effective_user.id

    if not is_authorized(user_id):
        await update.message.reply_text("❌ You are not authorized to use this bot.")
        return

    text = update.message.text
    if not is_youtube_link(text):
        await update.message.reply_text(
            "🔗 Please send a valid YouTube link.\n"
            "Examples:\n"
            "https://www.youtube.com/watch?v=VIDEO_ID\n"
            "https://youtu.be/VIDEO_ID\n"
            "https://youtube.com/shorts/VIDEO_ID\n"
            "https://youtube.com/playlist?list=PLAYLIST_ID"
        )
        return

    url = extract_youtube_link(text)
    chat_id = update.effective_chat.id
    settings = get_user_settings(user_id)

    # Check if already downloading - add to queue
    if IS_DOWNLOADING.get(user_id):
        added, msg = add_to_queue(user_id, url, settings.copy(), chat_id)
        if added:
            await update.message.reply_text(msg, parse_mode="HTML")
        return

    # Detect playlist
    playlist_mode = is_playlist_url(url) or settings["mode"] == "playlist"

    if playlist_mode:
        await handle_playlist_message(update, context, url, settings)
    else:
        await handle_single_with_preview(update, context, url, settings)


async def handle_single_with_preview(update, context, url, settings):
    """Show a preview of the video and wait for user confirmation."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    # Extract video info for preview
    try:
        video_info = get_video_info(url)
    except Exception:
        video_info = None

    # Store pending download in user state
    USER_STATE[user_id] = {
        "pending": {
            "url": url,
            "settings": settings.copy(),
            "video_info": video_info,
            "chat_id": chat_id,
        }
    }

    # Show preview
    await show_preview(context, chat_id, video_info, settings)


# ─── Playlist Handler ────────────────────────────────────────────────────────

async def handle_playlist_message(update, context, url, settings):
    """Handle playlist download with progress tracking and cancel support."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    mode_emoji = "📺"

    # Send initial status message
    status_msg = await update.message.reply_text(
        f"{mode_emoji} <b>Playlist Detected!</b>\n"
        f"⏳ Extracting playlist info...",
        parse_mode="HTML",
    )

    # Create task for cancellation
    task_id = str(uuid.uuid4())[:12]
    # Create temp/{timestamp} folder for this request
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmpdir = os.path.join(TEMP_BASE_DIR, timestamp)
    os.makedirs(tmpdir, exist_ok=True)

    DOWNLOAD_TASKS[task_id] = {
        "user_id": user_id,
        "process": None,
        "status_msg": status_msg,
        "temp_dir": tmpdir,
        "output_dir": tmpdir,
        "settings": settings.copy(),
        "cancelled": False,
    }
    IS_DOWNLOADING[user_id] = task_id

    cancel_keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ Cancel", callback_data=f"download:cancel:{task_id}")
    ]])

    try:
        # Update status
        await status_msg.edit_text(
            f"{mode_emoji} <b>Downloading Playlist...</b>\n"
            f"⏳ This may take a while for large playlists...\n"
            f"📥 Downloading all videos...",
            parse_mode="HTML",
            reply_markup=cancel_keyboard,
        )

        # Progress callback
        async def playlist_progress(parsed):
            current_msg = DOWNLOAD_TASKS[task_id].get("status_msg")
            if not current_msg:
                return

            try:
                if parsed.get("source") == "ytdlp" and parsed.get("percent") is not None:
                    percent = parsed["percent"]
                    bar = format_progress_bar(percent)
                    eta = parsed.get("eta", "")

                    text = (
                        f"{mode_emoji} <b>Downloading Playlist...</b>\n\n"
                        f"{bar} <code>{percent:.1f}%</code>\n"
                    )
                    if eta:
                        text += f"⏱️ ETA: {eta}"
                    text += "\n\n🛑 Use button below to cancel"

                    await current_msg.edit_text(text, parse_mode="HTML", reply_markup=cancel_keyboard)

                elif parsed.get("source") == "ffmpeg":
                    elapsed = parsed.get("time", "")
                    text = f"{mode_emoji} <b>Processing...</b>\n"
                    if elapsed:
                        text += f"⏱️ {elapsed}"
                    text += "\n\n🛑 Use button below to cancel"

                    await current_msg.edit_text(text, parse_mode="HTML", reply_markup=cancel_keyboard)

            except (telegram.error.BadRequest):
                DOWNLOAD_TASKS[task_id]["status_msg"] = None

        # Run playlist downloader
        output = await run_playlist_downloader(
            url, settings, tmpdir, task_id=task_id, progress_callback=playlist_progress,
        )

        # Check for cancellation
        if DOWNLOAD_TASKS[task_id]["cancelled"]:
            raise DownloadCancelledError()

        # Parse output for success/fail count
        output_lines = output.split("\n")
        total_downloaded = sum(1 for line in output_lines if "[SUCCESS]" in line)
        total_failed = sum(1 for line in output_lines if "[FAILED]" in line)

        # Find all downloaded files
        files = find_playlist_output_files(tmpdir, settings)

        if not files:
            raise RuntimeError("Playlist download completed but no output files found.")

        # Send files
        current_msg = DOWNLOAD_TASKS[task_id].get("status_msg")
        if current_msg:
            try:
                await current_msg.edit_text(
                    f"{mode_emoji} <b>Playlist Downloaded!</b>\n"
                    f"✅ {total_downloaded} videos downloaded\n"
                    f"❌ {total_failed} videos failed\n\n"
                    f"📤 Uploading {len(files)} files to Telegram...",
                    parse_mode="HTML",
                    reply_markup=cancel_keyboard,
                )
            except (telegram.error.BadRequest):
                pass

        sent_count = 0
        skipped_count = 0

        for idx, file_path in enumerate(files):
            # Check for cancellation during file sending
            if DOWNLOAD_TASKS[task_id]["cancelled"]:
                raise DownloadCancelledError()

            file_size = os.path.getsize(file_path)
            file_name = Path(file_path).name

            if file_size > 2 * 1024 * 1024 * 1024:
                skipped_count += 1
                continue

            try:
                with open(file_path, "rb") as f:
                    if file_path.lower().endswith((".m4a", ".mp3", ".flac", ".aac", ".ogg")):
                        await context.bot.send_audio(
                            chat_id=chat_id,
                            audio=f,
                            filename=file_name,
                            caption=f"🎵 {file_name} ({idx + 1}/{len(files)})",
                            timeout=600,
                        )
                    else:
                        await context.bot.send_video(
                            chat_id=chat_id,
                            video=f,
                            filename=file_name,
                            supports_streaming=True,
                            caption=f"🎬 {file_name} ({idx + 1}/{len(files)})",
                            timeout=600,
                        )
                sent_count += 1
            except Exception:
                skipped_count += 1

        # Done
        current_msg = DOWNLOAD_TASKS[task_id].get("status_msg")
        if current_msg:
            try:
                await current_msg.edit_text(
                    f"✅ <b>Playlist Complete!</b>\n\n"
                    f"📁 Downloaded: {total_downloaded} videos\n"
                    f"❌ Failed: {total_failed} videos\n"
                    f"📤 Sent: {sent_count} files\n"
                    f"⏭️ Skipped: {skipped_count} files\n\n"
                    f"Send another link to download more.",
                    parse_mode="HTML",
                )
            except (telegram.error.BadRequest):
                pass

    except DownloadCancelledError:
        current_msg = DOWNLOAD_TASKS.get(task_id, {}).get("status_msg")
        if current_msg:
            try:
                await current_msg.edit_text("❌ Download cancelled.", parse_mode="HTML")
            except (telegram.error.BadRequest):
                pass

    except RuntimeError as e:
        error_msg = str(e)
        if len(error_msg) > 500:
            error_msg = error_msg[:497] + "..."

        current_msg = DOWNLOAD_TASKS.get(task_id, {}).get("status_msg")
        if current_msg:
            try:
                await current_msg.edit_text(
                    f"❌ <b>Error:</b>\n<code>{error_msg}</code>",
                    parse_mode="HTML",
                )
            except (telegram.error.BadRequest):
                pass

    except Exception as e:
        error_msg = str(e)[:500]

        current_msg = DOWNLOAD_TASKS.get(task_id, {}).get("status_msg")
        if current_msg:
            try:
                await current_msg.edit_text(
                    f"❌ <b>Unexpected error:</b>\n<code>{error_msg}</code>",
                    parse_mode="HTML",
                )
            except (telegram.error.BadRequest):
                pass

    finally:
        # Cleanup
        if tmpdir and os.path.exists(tmpdir):
            shutil.rmtree(tmpdir, ignore_errors=True)

        if DOWNLOAD_TASKS.get(task_id):
            del DOWNLOAD_TASKS[task_id]

        if IS_DOWNLOADING.get(user_id) == task_id:
            del IS_DOWNLOADING[user_id]

    # Drain queue after playlist completes
    await drain_queue(user_id, context)


# ─── Callback Handler ────────────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button callbacks."""
    query = update.callback_query
    await query.answer()

    data = query.data
    user_id = query.from_user.id

    # --- Mode selection (existing) ---
    if data.startswith("mode:"):
        mode = data.split(":")[1]
        settings = get_user_settings(user_id)
        settings["mode"] = mode
        if mode == "video":
            await query.edit_message_text(
                "🎬 Mode set to <b>Video</b>\nSend a YouTube link!",
                parse_mode="HTML",
            )
        elif mode == "audio":
            await query.edit_message_text(
                "🎵 Mode set to <b>Audio Only</b>\nSend a YouTube link!",
                parse_mode="HTML",
            )
        elif mode == "playlist":
            await query.edit_message_text(
                "📺 Mode set to <b>Playlist</b>\nSend a YouTube playlist URL!",
                parse_mode="HTML",
            )

    # --- Settings (existing) ---
    elif data == "settings":
        settings = get_user_settings(user_id)
        text = (
            "⚙️ <b>Settings</b>\n\n"
            f"Mode: <b>{'Video' if settings['mode'] == 'video' else 'Audio' if settings['mode'] == 'audio' else 'Playlist'}</b>\n"
        )
        if settings["mode"] in ("video", "playlist"):
            text += f"Format: <code>{settings['video_format']}</code>\n"
            text += f"Quality: <code>{settings['video_quality']}</code>\n"
        else:
            text += f"Format: <code>{settings['audio_format']}</code>\n"
        text += f"Bitrate: <code>{settings['audio_bitrate']}</code>"
        await query.edit_message_text(text, parse_mode="HTML")

    # --- Preview: Confirm download ---
    elif data == "preview:confirm":
        pending = USER_STATE.get(user_id, {}).get("pending")
        if not pending:
            try:
                await query.edit_message_text(
                    "⚡ No pending download. Send a YouTube link to start."
                )
            except telegram.error.BadRequest:
                pass
            return

        url = pending["url"]
        settings = pending["settings"]
        video_info = pending.get("video_info")
        chat_id = pending.get("chat_id", query.from_user.id)

        # Clear pending state
        USER_STATE[user_id]["pending"] = None

        try:
            await download_and_send(
                context, user_id, chat_id, url, settings, video_info=video_info,
            )
        except DownloadCancelledError:
            pass  # Handled inside download_and_send
        except Exception:
            pass  # Handled inside download_and_send

        # Drain queue after download completes
        await drain_queue(user_id, context)

    # --- Preview: Cancel ---
    elif data == "preview:cancel":
        pending = USER_STATE.get(user_id, {}).get("pending")

        if pending:
            USER_STATE[user_id]["pending"] = None

        try:
            await query.edit_message_text("❌ Cancelled. Send a YouTube link to start downloading.")
        except telegram.error.BadRequest:
            pass

        # Drain queue if there are pending items
        await drain_queue(user_id, context)

    # --- Download: Cancel ---
    elif data.startswith("download:cancel:"):
        task_id = data.split(":")[2]

        task = DOWNLOAD_TASKS.get(task_id)
        if not task:
            await query.answer("Download already finished.")
            return

        if task["cancelled"]:
            await query.answer("Cancellation in progress...")
            return

        # Set cancelled flag - download_and_send will handle cleanup
        task["cancelled"] = True
        await query.answer("Cancelling download...")

    # --- Queue: Clear ---
    elif data == "queue:clear":
        count = clear_queue(user_id)
        try:
            await query.edit_message_text(
                f"🗑️ Queue cleared ({count} item{'s' if count != 1 else ''} removed)."
            )
        except telegram.error.BadRequest:
            pass


# ─── Dependencies Check ──────────────────────────────────────────────────────

def check_dependencies():
    """Check that all required packages are installed."""
    missing = []

    try:
        import telegram  # noqa: F401
    except ImportError:
        missing.append("python-telegram-bot")

    try:
        import dotenv  # noqa: F401
    except ImportError:
        missing.append("python-dotenv")

    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        missing.append("yt-dlp")

    # Check ffmpeg
    result = subprocess.run(["which", "ffmpeg"], capture_output=True)
    if result.returncode != 0:
        missing.append("ffmpeg (install: brew install ffmpeg)")

    # Check downloader script exists
    if not os.path.exists(DOWNLOADER_SCRIPT):
        print(f"[ERROR] Downloader script not found: {DOWNLOADER_SCRIPT}")
        print("Make sure main.py is in the same directory.")
        sys.exit(1)

    if missing:
        print("[ERROR] Missing dependencies:")
        for m in missing:
            print(f"  - {m}")
        print("\nInstall with:")
        print("  pip install python-telegram-bot python-dotenv yt-dlp")
        sys.exit(1)

    # Check mutagen (optional, for metadata embedding)
    try:
        import mutagen  # noqa: F401
        print("[INFO] mutagen available - metadata embedding enabled")
    except ImportError:
        print("[WARN] mutagen not installed - metadata embedding disabled")
        print("  Install with: pip install mutagen")


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    """Bot entry point."""
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    logger = logging.getLogger(__name__)

    print("=" * 50)
    print("  YouTube Telegram Bot")
    print("=" * 50)

    # Check dependencies
    check_dependencies()

    # Check bot token
    if not BOT_TOKEN:
        print("\n[ERROR] BOT_TOKEN not set!")
        print("Create a .env file with:")
        print("  BOT_TOKEN=your_bot_token_here")
        print("\nGet a token from @BotFather on Telegram.")
        sys.exit(1)

    print(f"\n[BOT] Token configured")
    if ALLOWED_USERS:
        print(f"[BOT] Restricted to users: {ALLOWED_USERS}")
    else:
        print("[BOT] Open to all users")
    print(f"[BOT] Downloader: {DOWNLOADER_SCRIPT}")
    print("\n[BOT] Starting bot... Press Ctrl+C to stop.\n")

    # Build and run bot
    app = Application.builder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("video", cmd_video))
    app.add_handler(CommandHandler("audio", cmd_audio))
    app.add_handler(CommandHandler("playlist", cmd_playlist))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("quality", cmd_quality))
    app.add_handler(CommandHandler("bitrate", cmd_bitrate))
    app.add_handler(CommandHandler("format", cmd_format))
    app.add_handler(CommandHandler("queue", cmd_queue))
    app.add_handler(CommandHandler("queue_clear", cmd_queue_clear))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("clear_cache", cmd_clear_cache))

    # Inline buttons
    app.add_handler(CallbackQueryHandler(handle_callback))

    # YouTube links (text messages, not commands)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Run bot
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
