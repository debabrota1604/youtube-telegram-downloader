#!/usr/bin/env python3
"""
YouTube Video/Audio Downloader and Converter
Uses ffmpeg to download from YouTube URLs and convert based on audio/video config.
yt-dlp is used as a helper to extract stream URLs, then ffmpeg handles download + merge.

Default behavior: downloads BOTH video (MP4/1080p/H.265) and audio (MP3/256k) as separate files.

Usage:
    python main.py <youtube_url> [options]

Examples:
    # Default: downloads both video (mp4-1080p-H.265) and audio (mp3-256k)
    python main.py https://www.youtube.com/watch?v=VIDEO_ID

    # Video only (no separate audio file)
    python main.py https://www.youtube.com/watch?v=VIDEO_ID --video-only

    # Audio only
    python main.py https://www.youtube.com/watch?v=VIDEO_ID --audio-only

    # Custom formats
    python main.py https://www.youtube.com/watch?v=VIDEO_ID --format mkv --video-quality 720p
    python main.py https://www.youtube.com/watch?v=VIDEO_ID --audio-format flac

    # URLs work without quotes (shell-split params are auto-rejoined)
    python main.py https://www.youtube.com/watch?v=VIDEO_ID&t=120
"""

import argparse
import json
import os
import subprocess
import sys


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


def check_dependencies():
    """Check if required dependencies (yt-dlp, ffmpeg) are installed."""
    errors = []

    # Check yt-dlp (used for stream URL extraction only)
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

    # Check ffmpeg (does all download + conversion work)
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


def get_stream_urls(url, prefer_audio=False):
    """
    Use yt-dlp to dump video/audio stream info as JSON,
    then return the best matching URLs for ffmpeg to download.
    """
    # Dump format info as JSON
    cmd = [
        "yt-dlp", "--dump-json", "--no-download",
        "--no-check-certificates", url
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        stderr = result.stderr.strip()
        # yt-dlp prints info to stderr
        if "WARNING" in stderr and "Extracting" in stderr:
            # Re-run to get actual output
            result2 = subprocess.run(cmd, capture_output=True, text=True, stderr=subprocess.STDOUT)
            info_lines = result2.stdout.strip().split("\n")
            # Last JSON line is what we want
            for line in reversed(info_lines):
                try:
                    info = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
            else:
                print(f"[ERROR] Failed to extract video info from: {url}")
                print(result.stderr)
                sys.exit(1)
        else:
            print(f"[ERROR] Failed to extract video info from: {url}")
            print(result.stderr)
            sys.exit(1)
    else:
        info = json.loads(result.stdout.strip())

    formats = info.get("formats", [])
    video_url = None
    audio_url = None
    title = info.get("title", "video")
    video_id = info.get("id", "unknown")

    if prefer_audio:
        # Find best audio-only stream
        audio_streams = [
            f for f in formats
            if f.get("vcodec") == "none" and f.get("acodec") != "none"
        ]
        # Prefer m4a/aac for easier ffmpeg handling, then opus, then others
        for pref_ext in ["m4a", "webm", "weba"]:
            for s in sorted(audio_streams, key=lambda x: x.get("abr", 0), reverse=True):
                if s.get("ext") == pref_ext or pref_ext in s.get("url", ""):
                    audio_url = s["url"]
                    break
            if audio_url:
                break

        # Fallback: just pick highest bitrate audio
        if not audio_url and audio_streams:
            audio_url = sorted(audio_streams, key=lambda x: x.get("abr", 0), reverse=True)[0]["url"]

        if not audio_url:
            print("[ERROR] No audio stream found")
            sys.exit(1)
    else:
        # Find best video and audio streams
        video_streams = [
            f for f in formats
            if f.get("acodec") == "none" and f.get("vcodec") != "none"
        ]
        audio_streams = [
            f for f in formats
            if f.get("vcodec") == "none" and f.get("acodec") != "none"
        ]

        # Prefer mp4 video (h264) and m4a audio (aac) for broad compatibility
        for s in sorted(video_streams, key=lambda x: x.get("height", 0), reverse=True):
            if s.get("ext") == "mp4" or "avc1" in s.get("codec", ""):
                video_url = s["url"]
                break
        if not video_url and video_streams:
            video_url = sorted(video_streams, key=lambda x: x.get("height", 0), reverse=True)[0]["url"]

        for s in sorted(audio_streams, key=lambda x: x.get("abr", 0), reverse=True):
            if s.get("ext") == "m4a":
                audio_url = s["url"]
                break
        if not audio_url and audio_streams:
            audio_url = sorted(audio_streams, key=lambda x: x.get("abr", 0), reverse=True)[0]["url"]

        if not video_url:
            print("[ERROR] No video stream found")
            sys.exit(1)
        if not audio_url:
            print("[ERROR] No audio stream found")
            sys.exit(1)

    # Sanitize filename
    safe_title = "".join(c if c.isalnum() or c in " _-" else "_" for c in title)
    safe_title = safe_title[:100]  # Truncate long titles

    return {
        "video_url": video_url,
        "audio_url": audio_url,
        "title": safe_title,
        "video_id": video_id,
        "thumbnail": info.get("thumbnail"),
    }


def get_quality_filter_arg(quality):
    """Map quality string to height value for filtering."""
    return quality.replace("p", "")


def download_with_ffmpeg(stream_info, output_path, format_name, audio_bitrate="192k",
                         video_quality="1080p", audio_only=False, apply_filter=False):
    """
    Use ffmpeg directly to download streams from URLs and convert to target format.
    This is the core conversion pipeline.
    """
    fmt = FORMAT_CONFIG[format_name]
    cmd = ["ffmpeg", "-y"]

    # Add user-agent and referer headers for YouTube streams
    cmd.extend([
        "-user_agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    ])

    if audio_only:
        # Single input: audio stream
        cmd.extend(["-i", stream_info["audio_url"]])

        # Audio output settings
        cmd.extend([
            "-vn",  # No video
            "-acodec", fmt["audio_codec"],
            "-b:a", audio_bitrate,
            "-ar", "44100",
        ])
    else:
        # Two inputs: video + audio
        cmd.extend(["-i", stream_info["video_url"]])
        cmd.extend(["-i", stream_info["audio_url"]])

        # Apply video quality filter if needed
        if apply_filter and video_quality:
            max_height = get_quality_filter_arg(video_quality)
            # Scale video to max height while maintaining aspect ratio
            cmd.extend([
                "-vf", f"scale='if(gte(iw,ih)*-1,{max_height},-2)':'if(gte(ih,iw)*-1,{max_height},-2)'"
            ])

        # Video + Audio output settings
        cmd.extend([
            "-c:v", fmt["video_codec"],
            "-c:a", fmt["audio_codec"],
            "-b:a", audio_bitrate,
            "-preset", "medium",
            "-movflags", "+faststart",  # Web-optimized MP4
        ])

    # Set output format container
    if fmt["container"] == "matroska":
        cmd.extend(["-f", "matroska"])
    elif fmt["container"] == "adts":
        cmd.extend(["-f", "adts"])
    elif fmt["container"] == "ipod":
        cmd.extend(["-f", "ipod"])

    # Output file
    cmd.append(output_path)

    print(f"[FFMPEG] Running: {' '.join(cmd[:10])} ...")
    print()

    # Execute ffmpeg - shows progress in real time
    process = subprocess.run(cmd, capture_output=False)

    if process.returncode != 0:
        print(f"\n[ERROR] ffmpeg failed with return code {process.returncode}")
        # Try to capture error from last lines
        sys.exit(1)

    return True


def download_with_ytdlp_ffmpeg(url, output_path, format_name, audio_bitrate="192k",
                                video_quality="1080p", audio_only=False):
    """
    Fallback: Use yt-dlp with ffmpeg as the external converter/downloader.
    yt-dlp handles stream selection, ffmpeg does the actual download and merge.
    """
    fmt_config = FORMAT_CONFIG[format_name]
    ext = fmt_config["extension"]

    # Build yt-dlp format selection
    if audio_only:
        ytdlp_format = "bestaudio"
        postprocessors = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": format_name,
                "preferredquality": audio_bitrate.replace("k", ""),
            }
        ]
    else:
        max_height = get_quality_filter_arg(video_quality)
        ytdlp_format = f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]/best"
        postprocessors = [
            {
                "key": "FFmpegVideoConvertor",
                "preferedformat": format_name if format_name in VIDEO_FORMATS else "mp4",
            }
        ]

    # Use yt-dlp with ffmpeg as downloader
    # Use a clean output template so the filename matches what the caller expects
    base_name = os.path.splitext(output_path)[0]
    output_template = base_name + "%(ext)s"

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
            "-x",  # Extract audio
            "--audio-format", format_name,
            "--audio-quality", audio_bitrate.replace("k", ""),
        ])

    # Append URL at the end (required by yt-dlp)
    cmd.append(url)

    print(f"[YT-DLP+FFMPEG] Running download with ffmpeg backend...")
    print()

    process = subprocess.run(cmd, capture_output=False)

    if process.returncode != 0:
        print(f"\n[ERROR] Download failed with return code {process.returncode}")
        sys.exit(1)

    # yt-dlp may produce a file with a different extension than expected.
    # Search the output directory for the actual file and rename it.
    import glob as _glob
    candidates = _glob.glob(base_name + ".*")
    actual_file = None
    for c in candidates:
        if os.path.isfile(c) and os.path.getsize(c) > 0:
            actual_file = c
            break
    if actual_file and actual_file != output_path:
        os.rename(actual_file, output_path)
        print(f"[INFO] Renamed {actual_file} -> {output_path}")

    return True


def validate_url(url):
    """Basic validation of YouTube URL."""
    youtube_patterns = [
        "youtube.com/watch",
        "youtu.be/",
        "youtube.com/shorts",
        "youtube.com/live",
        "youtube.com/v/",
        "youtube.com/playlist",
    ]
    return any(pattern in url for pattern in youtube_patterns)


def list_formats(url):
    """List available formats using yt-dlp."""
    cmd = ["yt-dlp", "-F", "--no-check-certificates", url]
    subprocess.run(cmd)


def create_parser():
    """Create argument parser."""
    parser = argparse.ArgumentParser(
        description="Download YouTube videos using ffmpeg, convert based on audio/video config.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=True,
        epilog="""
Default Behavior:
  When no format options are specified, downloads TWO separate files:
    - Video: MP4 / 1080p / H.265 (HEVC) with AAC audio
    - Audio: MP3 / 256k

Examples:
  # Default: downloads both video (mp4-1080p-H.265) and audio (mp3-256k)
  python main.py https://www.youtube.com/watch?v=VIDEO_ID

  # Video only (skip audio file)
  python main.py https://www.youtube.com/watch?v=VIDEO_ID --video-only

  # Audio only (skip video file)
  python main.py https://www.youtube.com/watch?v=VIDEO_ID --audio-only

  # Android Auto ready — video (MP4/H.264/AAC, 1080p)
  python main.py https://www.youtube.com/watch?v=VIDEO_ID --android-auto

  # Android Auto ready — audio only (M4A/AAC, 256k)
  python main.py https://www.youtube.com/watch?v=VIDEO_ID --android-auto --audio-only

  # Custom video format and quality
  python main.py https://www.youtube.com/watch?v=VIDEO_ID --format mkv --video-quality 720p

  # Custom audio format
  python main.py https://www.youtube.com/watch?v=VIDEO_ID --audio-format flac

  # Use H.264 codec instead of default H.265
  python main.py https://www.youtube.com/watch?v=VIDEO_ID --video-codec libx264

  # Download with custom output folder
  python main.py https://www.youtube.com/watch?v=VIDEO_ID --output /path/to/folder

  # URLs work without quotes (shell-split params are auto-rejoined)
  python main.py https://www.youtube.com/watch?v=VIDEO_ID&t=120
  python main.py https://youtu.be/VIDEO_ID&feature=share

  Video formats:  mp4, mkv, webm, avi
  Audio formats:  mp3, flac, wav, aac, ogg, m4a
  Video qualities: 144p, 240p, 360p, 480p, 720p, 1080p, 1440p, 2160p, 4320p
  Audio bitrates:  64k, 128k, 192k, 256k, 320k
  Video codecs:    libx264 (H.264), libx265 (H.265), libvpx-vp9 (VP9), mpeg4
        """,
    )

    parser.add_argument(
        "url",
        nargs="?",
        default=None,
        help="YouTube video URL to download (no quotes needed)",
    )

    parser.add_argument(
        "-u", "--url-alternate",
        help="Alternate way to specify YouTube URL (useful for unquoted URLs with special chars)",
    )

    parser.add_argument(
        "--android-auto",
        action="store_true",
        help="Optimize for Android Auto: MP4/H.264+AAC at 1080p (video) or M4A/AAC 256k (audio)",
    )

    parser.add_argument(
        "--audio-only",
        action="store_true",
        help="Download audio only (MP3/256k). Default downloads both video and audio.",
    )

    parser.add_argument(
        "--format",
        "--video-format",
        dest="video_format",
        default="mp4",
        choices=VIDEO_FORMATS,
        help="Video container format (default: mp4). Ignored if --audio-only.",
    )

    parser.add_argument(
        "--audio-format",
        default=None,
        choices=AUDIO_FORMATS,
        help="Audio format for the separate audio file (default: mp3)",
    )

    parser.add_argument(
        "--video-quality",
        default="1080p",
        choices=VIDEO_QUALITIES,
        help="Max video resolution (default: 1080p). Ignored if --audio-only.",
    )

    parser.add_argument(
        "--audio-bitrate",
        default="256k",
        choices=AUDIO_BITRATES,
        help="Audio bitrate (default: 256k)",
    )

    parser.add_argument(
        "--video-only",
        action="store_true",
        help="Download only video (no audio file). By default, both video and audio are downloaded.",
    )

    parser.add_argument(
        "--video-codec",
        choices=["libx264", "libx265", "libvpx-vp9", "mpeg4"],
        default=None,
        help="Video codec (default: libx265 for mp4). Overrides format default.",
    )

    parser.add_argument(
        "--output", "-o",
        default=DEFAULT_DOWNLOAD_FOLDER,
        help=f"Output folder (default: {DEFAULT_DOWNLOAD_FOLDER})",
    )

    parser.add_argument(
        "--list-formats", "-F",
        action="store_true",
        help="List available formats without downloading",
    )

    parser.add_argument(
        "--method",
        choices=["ffmpeg", "ytdlp"],
        default="ffmpeg",
        help="Download method: ffmpeg (direct) or ytdlp (fallback). Default: ffmpeg",
    )

    parser.add_argument(
        "--playlist",
        action="store_true",
        help="Download all videos in a playlist",
    )

    return parser


def resolve_args(args):
    """Pre-process command line arguments to handle unquoted YouTube URLs.

    When URLs contain '&' characters and are passed without quotes,
    the shell splits them into separate tokens. This function detects
    and re-joins such fragments back into a single URL.

    Also handles stray trailing quotes (e.g., pasted URL with end-quote).

    Examples handled:
        python main.py https://youtu.be/abc&list=xyz --audio-only
        -> URL is 'https://youtu.be/abc&list=xyz'

        python main.py https://youtube.com/watch?v=abc&t=120 --format mp4
        -> URL is 'https://youtube.com/watch?v=abc&t=120'

        python main.py https://youtu.be/abc" --audio-only
        -> URL is 'https://youtu.be/abc' (strips trailing quote)

        python main.py https://youtube.com/shorts/abc&feature=share'
        -> URL is 'https://youtube.com/shorts/abc&feature=share'
    """
    if not args:
        return args

    # Characters that count as quotes to strip
    quote_chars = set('"\'`')

    def strip_quotes(s):
        """Remove leading and trailing quote characters from a string.

        Also handles cases where a quote appears embedded near the boundary,
        e.g., 'https://youtu.be/abc",' -> 'https://youtu.be/abc'
        """
        if not s:
            return s
        # Strip leading quote characters
        while s and s[0] in quote_chars:
            s = s[1:]
        # Strip trailing quote characters
        while s and s[-1] in quote_chars:
            s = s[:-1]
        # Handle embedded trailing quote: e.g., 'abc",' -> find quote, truncate there
        # Search from the end backwards, but stop at common URL boundaries to avoid
        # false positives on legitimate quote characters in the middle of URLs.
        # Check up to the last '&' or '=' boundary (query param boundaries).
        search_start = len(s) - 1
        for pos in range(search_start, max(len(s) - 32, 0), -1):
            if s[pos] in quote_chars:
                s = s[:pos]
                break
            # Stop searching past a query-parameter boundary
            if s[pos] in ('&', '='):
                break
        # Also handle embedded leading quote near start
        for pos in range(min(5, len(s) - 1), -1, -1):
            if s[pos] in quote_chars:
                s = s[pos + 1:]
                break
        return s.strip()

    def looks_like_url_start(s):
        """Check if a string looks like the beginning of a URL."""
        s = strip_quotes(s)
        return s.startswith('http://') or s.startswith('https://')

    def looks_like_url_fragment(s):
        """Check if a string looks like a continuation fragment of a URL.

        Matches patterns like:
          - key=value  (query parameters: list=xyz, t=120, feature=share)
          - /path      (path segments that got split)
          - bare words after & (e.g., 'feature=share"' with trailing end-quote)
        """
        s = strip_quotes(s)
        # Query parameter pattern: contains '=' but not a Python flag
        if '=' in s and not s.startswith('--') and not s.startswith('-'):
            return True
        # Path segment pattern: starts with '/' and looks like URL path
        if s.startswith('/') and not s.startswith('//'):
            return True
        # YouTube-specific fragment patterns: short alphanumeric segments that
        # could be query params split by the shell (e.g., 'feature=share' where
        # only 'share' remains, or 'has_verified', etc.)
        # Only match if it's a reasonable URL-like token (no spaces, alphanumeric+safe chars)
        if (s
            and not s.startswith('-')
            and ' ' not in s
            and all(c.isalnum() or c in '_-.' for c in s)
            and len(s) < 64):
            return True
        return False

    result = []
    i = 0
    url_found = False

    while i < len(args):
        arg = args[i]

        # Check if this is the start of a URL (not an option flag)
        if not url_found and looks_like_url_start(arg):
            # Start collecting URL fragments
            url_parts = [strip_quotes(arg)]

            # Look ahead and join consecutive URL fragments
            j = i + 1
            while j < len(args):
                next_arg = args[j]
                # Stop if we hit an option flag
                if next_arg.startswith('-'):
                    break
                # Check if this looks like a URL fragment
                if looks_like_url_fragment(strip_quotes(next_arg)):
                    # Check if the fragment is embedded in the argument (after '&')
                    # Shell splits at '&', so "abc&list=xyz" becomes two args
                    cleaned = strip_quotes(next_arg)
                    # Also handle case where fragment has trailing quotes
                    url_parts.append(cleaned)
                    j += 1
                    continue
                # If it doesn't look like a URL fragment, stop
                break

            # Join: first part is the base URL, rest are joined with '&'
            # The first part already has the full base URL (including original query params)
            # Additional fragments are shell-split tokens that need '&' prefix
            if len(url_parts) == 1:
                url = url_parts[0]
            else:
                # Check if the first part already ends with a query-like segment
                # (no trailing '&') - we need to add '&' between parts
                base = url_parts[0]
                fragments = url_parts[1:]
                if base and not base.endswith('&'):
                    url = base + '&' + '&'.join(fragments)
                else:
                    url = base + '&'.join(fragments)
            result.append(url)
            url_found = True
            i = j
        else:
            result.append(arg)
            # Mark URL as found if we encounter a non-flag that's not the URL start
            if not arg.startswith('-') and not looks_like_url_start(arg):
                url_found = True
            i += 1

    return result


def clean_url(url):
    """Strip surrounding quotes and whitespace from URL.

    Handles matched quotes, unmatched trailing/leading quotes,
    and partial quoting (e.g., URL copied with a stray end-quote).
    """
    url = url.strip()
    # Remove matched surrounding quotes (single, double, or backtick)
    if (url.startswith('"') and url.endswith('"')) or \
       (url.startswith("'") and url.endswith("'")) or \
       (url.startswith("`") and url.endswith("`")):
        url = url[1:-1]
    else:
        # Remove any stray leading quote characters
        while url and url[0] in ('"', "'", "`"):
            url = url[1:]
        # Remove any stray trailing quote characters
        while url and url[-1] in ('"', "'", "`"):
            url = url[:-1]
    # Remove any trailing whitespace or newlines
    url = url.strip()
    return url


def verify_output(output_path, ext):
    """Verify output file exists, renaming from alternative extension if needed."""
    if os.path.exists(output_path):
        return True

    # Check for alternative extensions
    for test_ext in [".mp4", ".mkv", ".webm"] + AUDIO_FORMATS:
        actual_ext = test_ext if test_ext.startswith(".") else f".{test_ext}"
        alt_path = output_path.replace(ext, actual_ext)
        if os.path.exists(alt_path) and alt_path != output_path:
            os.rename(alt_path, output_path)
            return True

    return False


def embed_audio_metadata(file_path, info, audio_format):
    """Embed basic metadata and album art into the audio file using mutagen.

    This supports MP3, M4A (MP4), and FLAC. If mutagen is not installed,
    this function will quietly skip embedding.
    """
    thumbnail = info.get("thumbnail") if isinstance(info, dict) else None
    title = info.get("title") if isinstance(info, dict) else None
    uploader = info.get("uploader") or info.get("artist") if isinstance(info, dict) else None
    upload_date = info.get("upload_date") if isinstance(info, dict) else None
    year = None
    if upload_date and len(upload_date) >= 4:
        year = upload_date[:4]

    # Lazy import mutagen
    try:
        import urllib.request
        from mutagen import File as MutagenFile
    except Exception:
        print("[WARN] mutagen not available; skipping metadata embedding")
        return

    try:
        audio = MutagenFile(file_path, easy=False)
        if audio is None:
            print("[WARN] mutagen could not open file for tagging")
            return

        # MP3 (ID3)
        if audio_format == "mp3" or file_path.lower().endswith('.mp3'):
            try:
                from mutagen.id3 import ID3, APIC, TIT2, TPE1, TALB, TDRC
                from mutagen.mp3 import MP3
                mp = MP3(file_path, ID3=ID3)
                try:
                    mp.add_tags()
                except Exception:
                    pass
                if title:
                    mp.tags.add(TIT2(encoding=3, text=title))
                if uploader:
                    mp.tags.add(TPE1(encoding=3, text=uploader))
                if year:
                    mp.tags.add(TDRC(encoding=3, text=year))
                if thumbnail:
                    try:
                        img = urllib.request.urlopen(thumbnail).read()
                        mp.tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=img))
                    except Exception:
                        pass
                mp.save(v2_version=3)
            except Exception:
                pass

        # MP4/M4A
        elif audio_format in ("m4a", "mp4") or file_path.lower().endswith('.m4a'):
            try:
                from mutagen.mp4 import MP4, MP4Cover
                m4 = MP4(file_path)
                if title:
                    m4['\xa9nam'] = [title]
                if uploader:
                    m4['\xa9ART'] = [uploader]
                if year:
                    m4['\xa9day'] = [year]
                if thumbnail:
                    try:
                        img = urllib.request.urlopen(thumbnail).read()
                        m4['covr'] = [MP4Cover(img, imageformat=MP4Cover.FORMAT_JPEG)]
                    except Exception:
                        pass
                m4.save()
            except Exception:
                pass

        # FLAC
        elif audio_format == "flac" or file_path.lower().endswith('.flac'):
            try:
                from mutagen.flac import FLAC, Picture
                f = FLAC(file_path)
                if title:
                    f['title'] = title
                if uploader:
                    f['artist'] = uploader
                if year:
                    f['date'] = year
                if thumbnail:
                    try:
                        img = urllib.request.urlopen(thumbnail).read()
                        pic = Picture()
                        pic.data = img
                        pic.type = 3
                        pic.mime = 'image/jpeg'
                        pic.desc = 'Cover'
                        f.add_picture(pic)
                    except Exception:
                        pass
                f.save()
            except Exception:
                pass

        else:
            # Fallback: try to set common easy tags
            try:
                easy = MutagenFile(file_path, easy=True)
                if easy is not None:
                    if title:
                        easy['title'] = title
                    if uploader:
                        easy['artist'] = uploader
                    easy.save()
            except Exception:
                pass

    except Exception:
        print("[WARN] Failed to embed metadata")
        return


def download_playlist(url, output_folder, video_format="mp4", video_quality="1080p",
                      audio_bitrate="256k", audio_format="mp3", audio_only=False,
                      video_only=False, method="ffmpeg", video_codec=None):
    """Download all videos in a YouTube playlist."""
    print("[INFO] Fetching playlist items...")

    # Get playlist video IDs using yt-dlp --flat-playlist
    cmd = [
        "yt-dlp", "--flat-playlist", "--print", "%(id)s",
        "--no-check-certificates", url
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[ERROR] Failed to get playlist items: {result.stderr}")
        return

    video_ids = [v.strip() for v in result.stdout.strip().split("\n") if v.strip()]
    # YouTube video IDs are exactly 11 alphanumeric characters
    video_ids = [v for v in video_ids if len(v) == 11 and v.isalnum()]

    if not video_ids:
        print("[ERROR] No videos found in playlist")
        return

    print(f"[INFO] Found {len(video_ids)} videos in playlist\n")

    success_count = 0
    fail_count = 0

    for idx, vid in enumerate(video_ids):
        video_url = f"https://www.youtube.com/watch?v={vid}"
        print(f"\n{'=' * 50}")
        print(f"[{idx + 1}/{len(video_ids)}] Downloading: {vid}")
        print(f"{'=' * 50}")

        try:
            # Get stream info
            stream_info = get_stream_urls(video_url, prefer_audio=audio_only)
            safe_title = stream_info["title"]

            downloaded_any = False

            # Download VIDEO
            if not audio_only:
                video_ext = FORMAT_CONFIG[video_format]["extension"]
                video_filename = f"{safe_title} [{vid}][video]{video_ext}"
                video_output_path = os.path.join(output_folder, video_filename)

                vc = video_codec
                if vc is None:
                    vc = FORMAT_CONFIG[video_format]["video_codec"]

                original_codec = FORMAT_CONFIG[video_format]["video_codec"]
                FORMAT_CONFIG[video_format]["video_codec"] = vc

                if method == "ffmpeg":
                    try:
                        download_with_ffmpeg(
                            stream_info, video_output_path, video_format,
                            audio_bitrate=audio_bitrate,
                            video_quality=video_quality,
                            audio_only=False,
                            apply_filter=True,
                        )
                    except (Exception, SystemExit):
                        print("[WARNING] ffmpeg download failed, trying ytdlp fallback...")
                        try:
                            download_with_ytdlp_ffmpeg(
                                video_url, video_output_path, video_format,
                                audio_bitrate=audio_bitrate,
                                video_quality=video_quality,
                                audio_only=False,
                            )
                        except (Exception, SystemExit):
                            pass
                else:
                    try:
                        download_with_ytdlp_ffmpeg(
                            video_url, video_output_path, video_format,
                            audio_bitrate=audio_bitrate,
                            video_quality=video_quality,
                            audio_only=False,
                        )
                    except (Exception, SystemExit):
                        pass

                FORMAT_CONFIG[video_format]["video_codec"] = original_codec

                if verify_output(video_output_path, video_ext):
                    downloaded_any = True

            # Download AUDIO
            if not video_only:
                # Re-extract stream info for audio
                if not audio_only:
                    stream_info = get_stream_urls(video_url, prefer_audio=True)

                audio_ext = FORMAT_CONFIG[audio_format]["extension"]
                audio_filename = f"{safe_title} [{vid}][audio]{audio_ext}"
                audio_output_path = os.path.join(output_folder, audio_filename)

                if method == "ffmpeg":
                    try:
                        download_with_ffmpeg(
                            stream_info, audio_output_path, audio_format,
                            audio_bitrate=audio_bitrate,
                            video_quality=video_quality,
                            audio_only=True,
                            apply_filter=False,
                        )
                    except (Exception, SystemExit):
                        print("[WARNING] ffmpeg download failed, trying ytdlp fallback...")
                        try:
                            download_with_ytdlp_ffmpeg(
                                video_url, audio_output_path, audio_format,
                                audio_bitrate=audio_bitrate,
                                video_quality=video_quality,
                                audio_only=True,
                            )
                        except (Exception, SystemExit):
                            pass
                else:
                    try:
                        download_with_ytdlp_ffmpeg(
                            video_url, audio_output_path, audio_format,
                            audio_bitrate=audio_bitrate,
                            video_quality=video_quality,
                            audio_only=True,
                        )
                    except (Exception, SystemExit):
                        pass

                if verify_output(audio_output_path, audio_ext):
                    downloaded_any = True

            if downloaded_any:
                success_count += 1
                print(f"[SUCCESS] {vid}")
            else:
                fail_count += 1
                print(f"[FAILED] {vid}: no output file created")

        except SystemExit:
            fail_count += 1
            print(f"[FAILED] {vid}")
        except Exception as e:
            fail_count += 1
            print(f"[FAILED] {vid}: {e}")

    # Summary
    print(f"\n{'=' * 60}")
    print(f"[PLAYLIST COMPLETE] Success: {success_count}, Failed: {fail_count}")
    print(f"{'=' * 60}")


def main():
    """Main entry point."""
    parser = create_parser()

    # Pre-process args to handle unquoted URLs with special characters
    # Join consecutive non-option tokens to handle URLs split by shell at '&'
    processed_args = resolve_args(sys.argv[1:])
    args = parser.parse_args(processed_args)

    # Use alternate URL if provided, otherwise use positional URL
    if args.url_alternate:
        args.url = args.url_alternate
    elif not args.url:
        parser.error("No URL provided. Use positional argument or --url-alternate/-u")

    # Clean the URL (remove surrounding quotes)
    args.url = clean_url(args.url)

    print("=" * 60)
    print("  YouTube Downloader (ffmpeg + yt-dlp)")
    print("=" * 60)

    # Validate URL
    if not validate_url(args.url):
        print(f"[WARNING] URL may not be a valid YouTube link: {args.url}")
        response = input("Continue anyway? (y/N): ").lower()
        if response != "y":
            print("[INFO] Aborted.")
            sys.exit(0)

    # Check dependencies
    print()
    check_dependencies()

    # List formats if requested
    if args.list_formats:
        list_formats(args.url)
        return

    # Apply Android Auto presets if requested
    if args.android_auto:
        if args.audio_only:
            preset = ANDROID_AUTO_PRESETS["audio"]
            output_format = preset["format"]
            args.audio_bitrate = preset["audio_bitrate"]
            preset_label = preset["description"]
        else:
            preset = ANDROID_AUTO_PRESETS["video"]
            output_format = preset["format"]
            args.video_format = preset["format"]
            args.video_quality = preset["video_quality"]
            args.audio_bitrate = preset["audio_bitrate"]
            preset_label = preset["description"]
    else:
        # Determine output format from args
        if args.audio_only:
            output_format = args.audio_format if args.audio_format else "mp3"
        else:
            output_format = args.video_format
        preset_label = None

    # Determine download modes
    # By default (no --video-only and no --audio-only), download BOTH video and audio separately
    download_video = not args.audio_only  # Download video unless --audio-only is set
    download_audio = not args.video_only and not args.audio_only  # Download audio unless --video-only or --audio-only
    if args.audio_only:
        download_video = False
        download_audio = True

    # Create output folder
    os.makedirs(args.output, exist_ok=True)

    # Print configuration
    print()
    print("[CONFIG]")
    print(f"  URL:           {args.url}")
    print(f"  Output Folder: {args.output}")
    print(f"  Android Auto:  {'YES' if args.android_auto else 'no'}")
    if args.android_auto:
        print(f"  Preset:        {preset_label}")
    print(f"  Download Video: {download_video}")
    print(f"  Download Audio: {download_audio}")
    if download_video:
        print(f"  Video Format:  {output_format}")
        print(f"  Video Quality: {args.video_quality}")
        if args.video_codec:
            print(f"  Video Codec:   {args.video_codec}")
    if download_audio:
        audio_format = args.audio_format if args.audio_format else "mp3"
        print(f"  Audio Format:  {audio_format}")
    print(f"  Audio Bitrate: {args.audio_bitrate}")
    print(f"  Method:        {args.method}")
    print(f"  Playlist:      {'YES' if args.playlist else 'no'}")
    print()

    # Handle playlist mode
    if args.playlist:
        download_playlist(
            args.url, args.output,
            video_format=output_format,
            video_quality=args.video_quality,
            audio_bitrate=args.audio_bitrate,
            audio_format=args.audio_format if args.audio_format else "mp3",
            audio_only=args.audio_only,
            video_only=args.video_only,
            method=args.method,
            video_codec=args.video_codec,
        )
        return

    # Extract stream info
    print("[INFO] Extracting stream URLs...")
    stream_info = get_stream_urls(args.url, prefer_audio=download_audio and not download_video)
    print(f"[INFO] Title: {stream_info['title']} [{stream_info['video_id']}]")

    # Determine video codec
    video_codec = args.video_codec
    if video_codec is None:
        video_codec = FORMAT_CONFIG[output_format]["video_codec"]

    # Track successful downloads
    successful_downloads = []

    # Download VIDEO (mp4-1080p-H.265 by default)
    if download_video:
        print()
        print("--- Downloading Video ---")
        video_ext = FORMAT_CONFIG[output_format]["extension"]
        video_filename = f"{stream_info['title']} [{stream_info['video_id']}][video]{video_ext}"
        video_output_path = os.path.join(args.output, video_filename)

        print(f"[INFO] Output: {video_output_path}")
        print()

        # Temporarily override video codec for this download
        original_codec = FORMAT_CONFIG[output_format]["video_codec"]
        FORMAT_CONFIG[output_format]["video_codec"] = video_codec

        if args.method == "ffmpeg":
            print("[INFO] Using ffmpeg to download video streams and convert...")
            try:
                download_with_ffmpeg(
                    stream_info, video_output_path, output_format,
                    audio_bitrate=args.audio_bitrate,
                    video_quality=args.video_quality,
                    audio_only=False,
                    apply_filter=True,
                )
            except Exception as e:
                print(f"\n[WARNING] Direct ffmpeg download failed: {e}")
                print("[INFO] Falling back to yt-dlp + ffmpeg method...")
                print()
                download_with_ytdlp_ffmpeg(
                    args.url, video_output_path, output_format,
                    audio_bitrate=args.audio_bitrate,
                    video_quality=args.video_quality,
                    audio_only=False,
                )
        else:
            download_with_ytdlp_ffmpeg(
                args.url, video_output_path, output_format,
                audio_bitrate=args.audio_bitrate,
                video_quality=args.video_quality,
                audio_only=False,
            )

        # Restore original codec
        FORMAT_CONFIG[output_format]["video_codec"] = original_codec

        # Verify video output
        if verify_output(video_output_path, video_ext):
            successful_downloads.append(video_output_path)

    # Download AUDIO (mp3-256k by default)
    if download_audio:
        print()
        print("--- Downloading Audio ---")
        audio_format = args.audio_format if args.audio_format else "mp3"
        audio_ext = FORMAT_CONFIG[audio_format]["extension"]
        audio_filename = f"{stream_info['title']} [{stream_info['video_id']}][audio]{audio_ext}"
        audio_output_path = os.path.join(args.output, audio_filename)

        print(f"[INFO] Output: {audio_output_path}")
        print()

        # Re-extract stream info for audio (may need different streams)
        if download_video:
            stream_info = get_stream_urls(args.url, prefer_audio=True)

        if args.method == "ffmpeg":
            print("[INFO] Using ffmpeg to download audio stream and convert...")
            try:
                download_with_ffmpeg(
                    stream_info, audio_output_path, audio_format,
                    audio_bitrate=args.audio_bitrate,
                    video_quality=args.video_quality,
                    audio_only=True,
                    apply_filter=False,
                )
            except Exception as e:
                print(f"\n[WARNING] Direct ffmpeg download failed: {e}")
                print("[INFO] Falling back to yt-dlp + ffmpeg method...")
                print()
                download_with_ytdlp_ffmpeg(
                    args.url, audio_output_path, audio_format,
                    audio_bitrate=args.audio_bitrate,
                    video_quality=args.video_quality,
                    audio_only=True,
                )
        else:
            download_with_ytdlp_ffmpeg(
                args.url, audio_output_path, audio_format,
                audio_bitrate=args.audio_bitrate,
                video_quality=args.video_quality,
                audio_only=True,
            )

        # Verify audio output
        if verify_output(audio_output_path, audio_ext):
            try:
                embed_audio_metadata(audio_output_path, stream_info, audio_format)
            except Exception as e:
                print(f"[WARN] Embedding metadata failed: {e}")
            successful_downloads.append(audio_output_path)

    # Final summary
    print()
    print("=" * 60)
    if successful_downloads:
        for path in successful_downloads:
            size = os.path.getsize(path)
            size_mb = size / (1024 * 1024)
            print(f"[SUCCESS] Saved: {path}")
            print(f"[INFO]  File size: {size_mb:.2f} MB")
    else:
        print("[ERROR] No files were successfully downloaded.")
        sys.exit(1)
    print("=" * 60)


if __name__ == "__main__":
    main()