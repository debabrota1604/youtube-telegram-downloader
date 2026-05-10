#!/usr/bin/env python3
"""
YouTube Video/Audio Downloader and Converter
Uses ffmpeg to download from YouTube URLs and convert based on audio/video config.
yt-dlp is used as a helper to extract stream URLs, then ffmpeg handles download + merge.

Usage:
    python main.py <youtube_url> [options]

Examples:
    python main.py https://www.youtube.com/watch?v=VIDEO_ID
    python main.py https://www.youtube.com/watch?v=VIDEO_ID --format mp4 --video-quality 1080p
    python main.py https://www.youtube.com/watch?v=VIDEO_ID --audio-only --audio-format mp3
    python main.py https://www.youtube.com/watch?v=VIDEO_ID --audio-format flac --video-quality 720p
"""

import argparse
import json
import os
import subprocess
import sys


# Default download folder
DEFAULT_DOWNLOAD_FOLDER = os.path.join(os.path.expanduser("~"), "Downloads")

# Supported output formats with ffmpeg codec mapping
FORMAT_CONFIG = {
    "mp4": {
        "container": "mp4",
        "video_codec": "libx264",
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
    cmd = [
        "yt-dlp",
        "--ffmpeg-location", "ffmpeg",
        "-f", ytdlp_format,
        "-o", output_path.replace(ext, "%(ext)s"),
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

    print(f"[YT-DLP+FFMPEG] Running download with ffmpeg backend...")
    print()

    process = subprocess.run(cmd, capture_output=False)

    if process.returncode != 0:
        print(f"\n[ERROR] Download failed with return code {process.returncode}")
        sys.exit(1)

    # Rename if the extension differs
    generated_path = output_path.replace(ext, ".mp4")
    if os.path.exists(generated_path) and generated_path != output_path:
        os.rename(generated_path, output_path)
        print(f"[INFO] Renamed to: {output_path}")

    return True


def validate_url(url):
    """Basic validation of YouTube URL."""
    youtube_patterns = [
        "youtube.com/watch",
        "youtu.be/",
        "youtube.com/shorts",
        "youtube.com/live",
        "youtube.com/v/",
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
        epilog="""
Examples:
  # Android Auto ready — video (MP4/H.264/AAC, 1080p)
  python main.py https://www.youtube.com/watch?v=VIDEO_ID --android-auto

  # Android Auto ready — audio only (M4A/AAC, 256k)
  python main.py https://www.youtube.com/watch?v=VIDEO_ID --android-auto --audio-only

  # Download video in default MP4 format at 1080p
  python main.py https://www.youtube.com/watch?v=VIDEO_ID

  # Download video in MKV format at 720p
  python main.py https://www.youtube.com/watch?v=VIDEO_ID --format mkv --video-quality 720p

  # Download audio only as MP3
  python main.py https://www.youtube.com/watch?v=VIDEO_ID --audio-only

  # Download audio as high-quality FLAC
  python main.py https://www.youtube.com/watch?v=VIDEO_ID --audio-only --audio-format flac

  # Download with custom output folder
  python main.py https://www.youtube.com/watch?v=VIDEO_ID --output /path/to/folder

  Video formats:  mp4, mkv, webm, avi
  Audio formats:  mp3, flac, wav, aac, ogg, m4a
  Video qualities: 144p, 240p, 360p, 480p, 720p, 1080p, 1440p, 2160p, 4320p
  Audio bitrates:  64k, 128k, 192k, 256k, 320k

  For Android Auto: use --android-auto for optimal MP4/H.264/AAC compatibility.
        """,
    )

    parser.add_argument(
        "url",
        help="YouTube video URL to download",
    )

    parser.add_argument(
        "--android-auto",
        action="store_true",
        help="Optimize for Android Auto: MP4/H.264+AAC at 1080p (video) or M4A/AAC 256k (audio)",
    )

    parser.add_argument(
        "--audio-only",
        action="store_true",
        help="Download audio only (default: download video+audio)",
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
        help="Audio format (default: matches container codec, or mp3 for --audio-only)",
    )

    parser.add_argument(
        "--video-quality",
        default="1080p",
        choices=VIDEO_QUALITIES,
        help="Max video resolution (default: 1080p). Ignored if --audio-only.",
    )

    parser.add_argument(
        "--audio-bitrate",
        default="192k",
        choices=AUDIO_BITRATES,
        help="Audio bitrate (default: 192k)",
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

    return parser


def main():
    """Main entry point."""
    parser = create_parser()
    args = parser.parse_args()

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
    print(f"  Audio Only:    {args.audio_only}")
    if not args.audio_only:
        print(f"  Video Format:  {output_format}")
        print(f"  Video Quality: {args.video_quality}")
    print(f"  Output Format: {output_format}")
    print(f"  Audio Bitrate: {args.audio_bitrate}")
    print(f"  Method:        {args.method}")
    print()

    # Extract stream info
    print("[INFO] Extracting stream URLs...")
    stream_info = get_stream_urls(args.url, prefer_audio=args.audio_only)
    print(f"[INFO] Title: {stream_info['title']} [{stream_info['video_id']}]")

    # Build output path
    ext = FORMAT_CONFIG[output_format]["extension"]
    filename = f"{stream_info['title']} [{stream_info['video_id']}]{ext}"
    output_path = os.path.join(args.output, filename)

    print(f"[INFO] Output: {output_path}")
    print()

    # Download and convert
    if args.method == "ffmpeg":
        print("[INFO] Using ffmpeg to download streams and convert...")
        try:
            download_with_ffmpeg(
                stream_info, output_path, output_format,
                audio_bitrate=args.audio_bitrate,
                video_quality=args.video_quality,
                audio_only=args.audio_only,
                apply_filter=True,
            )
        except Exception as e:
            print(f"\n[WARNING] Direct ffmpeg download failed: {e}")
            print("[INFO] Falling back to yt-dlp + ffmpeg method...")
            print()
            download_with_ytdlp_ffmpeg(
                args.url, output_path, output_format,
                audio_bitrate=args.audio_bitrate,
                video_quality=args.video_quality,
                audio_only=args.audio_only,
            )
    else:
        download_with_ytdlp_ffmpeg(
            args.url, output_path, output_format,
            audio_bitrate=args.audio_bitrate,
            video_quality=args.video_quality,
            audio_only=args.audio_only,
        )

    # Verify output
    if os.path.exists(output_path):
        size = os.path.getsize(output_path)
        size_mb = size / (1024 * 1024)
        print()
        print("=" * 60)
        print(f"[SUCCESS] Saved: {output_path}")
        print(f"[INFO]  File size: {size_mb:.2f} MB")
        print("=" * 60)
    else:
        # Check for alternative extensions
        found = False
        for test_ext in [".mp4", ".mkv", ".webm"] + AUDIO_FORMATS:
            alt_path = output_path.replace(ext, f".{test_ext}" if not test_ext.startswith(".") else test_ext)
            if os.path.exists(alt_path) and alt_path != output_path:
                os.rename(alt_path, output_path)
                size = os.path.getsize(output_path)
                size_mb = size / (1024 * 1024)
                print()
                print("=" * 60)
                print(f"[SUCCESS] Saved: {output_path}")
                print(f"[INFO]  File size: {size_mb:.2f} MB")
                print("=" * 60)
                found = True
                break

        if not found:
            print(f"\n[ERROR] Output file not found at: {output_path}")
            sys.exit(1)


if __name__ == "__main__":
    main()