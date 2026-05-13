"""Argument parsing and CLI entry point for the downloader."""

import argparse
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

from downloader.config import (
    ANDROID_AUTO_PRESETS,
    AUDIO_BITRATES,
    AUDIO_FORMATS,
    DEFAULT_DOWNLOAD_FOLDER,
    VIDEO_FORMATS,
    VIDEO_QUALITIES,
)
from downloader.ffmpeg_dl import check_dependencies
from downloader.playlist import download_playlist
from downloader.single import download_single
from downloader.streams import list_formats
from downloader.url_utils import clean_url, resolve_args, validate_url


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
        default="ytdlp",
        help="Download method: ytdlp (robust, recommended) or ffmpeg (direct). Default: ytdlp",
    )

    parser.add_argument(
        "--playlist",
        action="store_true",
        help="Download all videos in a playlist",
    )

    return parser


def _get_temp_dir():
    """Create a timestamped temp directory inside project temp/ folder."""
    _project_root = Path(__file__).resolve().parent.parent
    temp_base = _project_root / "temp"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmpdir = temp_base / timestamp
    tmpdir.mkdir(parents=True, exist_ok=True)
    return str(tmpdir)


def _move_files_to_output(temp_dir, output_folder):
    """Move all downloaded files from temp_dir to the final output_folder."""
    os.makedirs(output_folder, exist_ok=True)
    moved = 0
    for fname in os.listdir(temp_dir):
        src = os.path.join(temp_dir, fname)
        if os.path.isfile(src):
            dst = os.path.join(output_folder, fname)
            shutil.move(src, dst)
            size_mb = os.path.getsize(dst) / (1024 * 1024)
            print(f"[MOVED] {fname} ({size_mb:.2f} MB) -> {output_folder}")
            moved += 1
    return moved


def main():
    """Main entry point."""
    parser = create_parser()

    # Pre-process args to handle unquoted URLs with special characters
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
        if args.audio_only:
            output_format = args.audio_format if args.audio_format else "mp3"
        else:
            output_format = args.video_format
        preset_label = None

    # Determine download modes
    download_video = not args.audio_only
    download_audio = not args.video_only and not args.audio_only
    if args.audio_only:
        download_video = False
        download_audio = True

    # Create temp directory for intermediate files
    temp_dir = _get_temp_dir()

    # Print configuration
    print()
    print("[CONFIG]")
    print(f"  URL:           {args.url}")
    print(f"  Output Folder: {args.output}")
    print(f"  Temp Dir:      {temp_dir}")
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
        a_format = args.audio_format if args.audio_format else "mp3"
        print(f"  Audio Format:  {a_format}")
    print(f"  Audio Bitrate: {args.audio_bitrate}")
    print(f"  Method:        {args.method}")
    print(f"  Playlist:      {'YES' if args.playlist else 'no'}")
    print()

    try:
        # Handle playlist mode
        if args.playlist:
            download_playlist(
                args.url, temp_dir,
                video_format=output_format,
                video_quality=args.video_quality,
                audio_bitrate=args.audio_bitrate,
                audio_format=args.audio_format if args.audio_format else "mp3",
                audio_only=args.audio_only,
                video_only=args.video_only,
                method=args.method,
                video_codec=args.video_codec,
            )
        else:
            # Download single video
            download_single(
                url=args.url,
                output_folder=temp_dir,
                output_format=output_format,
                video_quality=args.video_quality,
                audio_bitrate=args.audio_bitrate,
                audio_format=args.audio_format if args.audio_format else "mp3",
                audio_only=args.audio_only,
                video_only=args.video_only,
                method=args.method,
                video_codec=args.video_codec,
            )

        # Move downloaded files to final output folder
        print()
        print("--- Moving files to output folder ---")
        moved = _move_files_to_output(temp_dir, args.output)
        print(f"[INFO] Moved {moved} file(s) to {args.output}")

    finally:
        # Cleanup temp directory
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
            print(f"[INFO] Cleaned up temp directory: {temp_dir}")


if __name__ == "__main__":
    main()