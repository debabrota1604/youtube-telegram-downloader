"""Single video download orchestration."""

import os
import sys

from downloader.config import FORMAT_CONFIG
from downloader.ffmpeg_dl import download_with_ffmpeg, download_with_ytdlp_ffmpeg
from downloader.metadata import embed_audio_metadata
from downloader.output import verify_output
from downloader.streams import get_stream_urls


def download_single(url, output_folder, output_format, video_quality="1080p",
                    audio_bitrate="256k", audio_format=None, audio_only=False,
                    video_only=False, method="ffmpeg", video_codec=None):
    """Download a single YouTube video/audio.

    Returns a list of successfully downloaded file paths.
    """
    if audio_format is None:
        audio_format = "mp3"

    # Determine download modes
    download_video = not audio_only
    download_audio = not video_only and not audio_only

    # Extract stream info
    print("[INFO] Extracting stream URLs...")
    stream_info = get_stream_urls(url, prefer_audio=download_audio and not download_video)
    print(f"[INFO] Title: {stream_info['title']} [{stream_info['video_id']}]")

    if video_codec is None:
        video_codec = FORMAT_CONFIG[output_format]["video_codec"]

    successful_downloads = []

    # Download VIDEO
    if download_video:
        print()
        print("--- Downloading Video ---")
        video_ext = FORMAT_CONFIG[output_format]["extension"]
        video_filename = f"{stream_info['title']} [{stream_info['video_id']}][video]{video_ext}"
        video_output_path = os.path.join(output_folder, video_filename)

        print(f"[INFO] Output: {video_output_path}")
        print()

        # Temporarily override video codec for this download
        original_codec = FORMAT_CONFIG[output_format]["video_codec"]
        FORMAT_CONFIG[output_format]["video_codec"] = video_codec

        if method == "ffmpeg":
            print("[INFO] Using ffmpeg to download video streams and convert...")
            try:
                download_with_ffmpeg(
                    stream_info, video_output_path, output_format,
                    audio_bitrate=audio_bitrate,
                    video_quality=video_quality,
                    audio_only=False,
                    apply_filter=True,
                )
            except Exception as e:
                print(f"\n[WARNING] Direct ffmpeg download failed: {e}")
                print("[INFO] Falling back to yt-dlp + ffmpeg method...")
                print()
                download_with_ytdlp_ffmpeg(
                    url, video_output_path, output_format,
                    audio_bitrate=audio_bitrate,
                    video_quality=video_quality,
                    audio_only=False,
                    video_codec=video_codec,
                )
        else:
            download_with_ytdlp_ffmpeg(
                url, video_output_path, output_format,
                audio_bitrate=audio_bitrate,
                video_quality=video_quality,
                audio_only=False,
                video_codec=video_codec,
            )

        # Restore original codec
        FORMAT_CONFIG[output_format]["video_codec"] = original_codec

        # Verify video output
        if verify_output(video_output_path, video_ext):
            successful_downloads.append(video_output_path)

    # Download AUDIO
    if download_audio:
        print()
        print("--- Downloading Audio ---")
        a_format = audio_format
        audio_ext = FORMAT_CONFIG[a_format]["extension"]
        audio_filename = f"{stream_info['title']} [{stream_info['video_id']}][audio]{audio_ext}"
        audio_output_path = os.path.join(output_folder, audio_filename)

        print(f"[INFO] Output: {audio_output_path}")
        print()

        # Re-extract stream info for audio (may need different streams)
        if download_video:
            stream_info = get_stream_urls(url, prefer_audio=True)

        if method == "ffmpeg":
            print("[INFO] Using ffmpeg to download audio stream and convert...")
            try:
                download_with_ffmpeg(
                    stream_info, audio_output_path, a_format,
                    audio_bitrate=audio_bitrate,
                    video_quality=video_quality,
                    audio_only=True,
                    apply_filter=False,
                )
            except Exception as e:
                print(f"\n[WARNING] Direct ffmpeg download failed: {e}")
                print("[INFO] Falling back to yt-dlp + ffmpeg method...")
                print()
                download_with_ytdlp_ffmpeg(
                    url, audio_output_path, a_format,
                    audio_bitrate=audio_bitrate,
                    video_quality=video_quality,
                    audio_only=True,
                )
        else:
            download_with_ytdlp_ffmpeg(
                url, audio_output_path, a_format,
                audio_bitrate=audio_bitrate,
                video_quality=video_quality,
                audio_only=True,
            )

        # Verify audio output
        if verify_output(audio_output_path, audio_ext):
            try:
                embed_audio_metadata(audio_output_path, stream_info, a_format)
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

    return successful_downloads