"""Constants, format configurations, and defaults for the downloader."""

import os

# Default download folder
DEFAULT_DOWNLOAD_FOLDER = os.path.join(os.path.expanduser("~"), "Downloads")

# Supported output formats with ffmpeg codec mapping
# Default video codec for mp4 is H.265 (libx265) for better compression
FORMAT_CONFIG = {
    "mp4": {
        "container": "mp4",
        "video_codec": "libx265",
        "audio_codec": "aac",
        "extension": ".mp4",
    },
    "mkv": {
        "container": "matroska",
        "video_codec": "libx264",
        "audio_codec": "aac",
        "extension": ".mkv",
    },
    "webm": {
        "container": "webm",
        "video_codec": "libvpx-vp9",
        "audio_codec": "libvorbis",
        "extension": ".webm",
    },
    "avi": {
        "container": "avi",
        "video_codec": "mpeg4",
        "audio_codec": "mp3",
        "extension": ".avi",
    },
    "mp3": {
        "container": "mp3",
        "audio_codec": "libmp3lame",
        "extension": ".mp3",
    },
    "flac": {
        "container": "flac",
        "audio_codec": "flac",
        "extension": ".flac",
    },
    "wav": {
        "container": "wav",
        "audio_codec": "pcm_s16le",
        "extension": ".wav",
    },
    "aac": {
        "container": "adts",
        "audio_codec": "aac",
        "extension": ".aac",
    },
    "ogg": {
        "container": "ogg",
        "audio_codec": "libvorbis",
        "extension": ".ogg",
    },
    "m4a": {
        "container": "ipod",
        "audio_codec": "aac",
        "extension": ".m4a",
    },
}

VIDEO_FORMATS = ["mp4", "mkv", "webm", "avi"]
AUDIO_FORMATS = ["mp3", "flac", "wav", "aac", "ogg", "m4a"]
VIDEO_QUALITIES = ["144p", "240p", "360p", "480p", "720p", "1080p", "1440p", "2160p", "4320p"]
AUDIO_BITRATES = ["64k", "128k", "192k", "256k", "320k"]

# Android Auto preset: H.264/AAC in MP4 is the most universally supported format
# on Android head units. Audio-only uses M4A (AAC) for best quality/compatibility.
ANDROID_AUTO_PRESETS = {
    "video": {
        "format": "mp4",
        "video_codec": "libx264",
        "audio_codec": "aac",
        "video_quality": "1080p",
        "audio_bitrate": "192k",
        "description": "MP4 / H.264 video + AAC audio / 1080p / 192k audio",
    },
    "audio": {
        "format": "m4a",
        "audio_codec": "aac",
        "audio_bitrate": "256k",
        "description": "M4A / AAC audio / 256k (high quality for driving)",
    },
}

# YouTube URL patterns for validation
YOUTUBE_URL_PATTERNS = [
    "youtube.com/watch",
    "youtu.be/",
    "youtube.com/shorts",
    "youtube.com/live",
    "youtube.com/v/",
    "youtube.com/playlist",
]