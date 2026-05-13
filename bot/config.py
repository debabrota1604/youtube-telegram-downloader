"""
Bot configuration, constants, and shared state.

All global settings, defaults, regex patterns, paths, and shared
mutable state (user settings, queues, download tasks) live here so
every other module can import a single source of truth.
"""

import os
import re
from pathlib import Path
from collections import deque
from dotenv import load_dotenv

load_dotenv()

# ── Bot credentials ──────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Allowed user IDs (empty = allow all users)
ALLOWED_USERS = os.getenv("ALLOWED_USERS", "").split(",")
ALLOWED_USERS = [u.strip() for u in ALLOWED_USERS if u.strip()]

# ── Path constants ───────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOWNLOADER_SCRIPT = _PROJECT_ROOT / "main.py"
TEMP_BASE_DIR = _PROJECT_ROOT / "temp"
LOG_DIR = _PROJECT_ROOT / "logs"

# ── Default settings ─────────────────────────────────────────────────────────
DEFAULT_SETTINGS = {
    "mode": "video",          # video or audio
    "video_quality": "1080p",
    "audio_bitrate": "256k",
    "audio_format": "m4a",    # for audio-only: m4a, mp3, flac
    "video_format": "mp4",    # for video: mp4, mkv
}

# ── URL patterns ─────────────────────────────────────────────────────────────
YOUTUBE_PATTERN = re.compile(
    r"(https?://)?(www\.)?(youtube\.com/(watch\?v=|shorts/|live/|embed/|v/|playlist\?list=)|youtu\.be/)[a-zA-Z0-9_-]+"
)

YOUTUBE_PLAYLIST_PATTERN = re.compile(
    r"(https?://)?(www\.)?youtube\.com/playlist\?list=[a-zA-Z0-9_-]+"
)

YOUTUBE_VIDEO_WITH_LIST_PATTERN = re.compile(
    r"(https?://)?(www\.)?youtube\.com/watch\?v=[a-zA-Z0-9_-]+&list=[a-zA-Z0-9_-]+"
)

# ── Queue limits ─────────────────────────────────────────────────────────────
MAX_QUEUE_SIZE = 20

# ── Shared mutable state ─────────────────────────────────────────────────────
# Per-user settings and state
USER_STATE: dict = {}
USER_SETTINGS: dict = {}

# Download tracking
DOWNLOAD_TASKS: dict = {}       # task_id -> task_info dict
DOWNLOAD_QUEUE: dict = {}       # user_id -> deque of queue items
IS_DOWNLOADING: dict = {}       # user_id -> task_id (True-like if downloading)