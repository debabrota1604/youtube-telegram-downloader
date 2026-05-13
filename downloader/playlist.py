"""Playlist download processing."""

import os
import subprocess
import sys

from downloader.config import FORMAT_CONFIG
from downloader.ffmpeg_dl import download_with_ffmpeg, download_with_ytdlp_ffmpeg
from downloader.output import verify_output
from downloader.streams import get_stream_urls


def download_playlist(url, output_folder, video_format="mp4", video_quality="1080p",
                      audio_bitrate="256k", audio_format="mp3", audio_only=False,
                      video_only=False, method="ffmpeg", video_codec=None):
    """Download all videos in a YouTube playlist."""
    print("[INFO] Fetching playlist items...")

    cmd = [
        "yt-dlp", "--flat-playlist", "--print", "%(id)s",
        "--no-check-certificates", url
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[ERROR] Failed to get playlist items: {result.stderr}")
        return

    video_ids = [v.strip() for v in result.stdout.strip().split("\n") if v.strip()]
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
                                video_codec=vc,
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
                            video_codec=vc,
                        )
                    except (Exception, SystemExit):
                        pass

                FORMAT_CONFIG[video_format]["video_codec"] = original_codec

                if verify_output(video_output_path, video_ext):
                    downloaded_any = True

            # Download AUDIO
            if not video_only:
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