"""Stream URL extraction via yt-dlp."""

import json
import subprocess
import sys


def get_stream_urls(url, prefer_audio=False):
    """Use yt-dlp to dump video/audio stream info as JSON,
    then return the best matching URLs for ffmpeg to download.
    """
    cmd = [
        "yt-dlp", "--dump-json", "--no-download",
        "--no-check-certificates", url
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "WARNING" in stderr and "Extracting" in stderr:
            result2 = subprocess.run(cmd, capture_output=True, text=True, stderr=subprocess.STDOUT)
            info_lines = result2.stdout.strip().split("\n")
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
        audio_streams = [
            f for f in formats
            if f.get("vcodec") == "none" and f.get("acodec") != "none"
        ]
        for pref_ext in ["m4a", "webm", "weba"]:
            for s in sorted(audio_streams, key=lambda x: x.get("abr", 0), reverse=True):
                if s.get("ext") == pref_ext or pref_ext in s.get("url", ""):
                    audio_url = s["url"]
                    break
            if audio_url:
                break

        if not audio_url and audio_streams:
            audio_url = sorted(audio_streams, key=lambda x: x.get("abr", 0), reverse=True)[0]["url"]

        if not audio_url:
            print("[ERROR] No audio stream found")
            sys.exit(1)
    else:
        video_streams = [
            f for f in formats
            if f.get("acodec") == "none" and f.get("vcodec") != "none"
        ]
        audio_streams = [
            f for f in formats
            if f.get("vcodec") == "none" and f.get("acodec") != "none"
        ]

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

    safe_title = "".join(c if c.isalnum() or c in " _-" else "_" for c in title)
    safe_title = safe_title[:100]

    return {
        "video_url": video_url,
        "audio_url": audio_url,
        "title": safe_title,
        "video_id": video_id,
        "thumbnail": info.get("thumbnail"),
    }


def list_formats(url):
    """List available formats using yt-dlp."""
    cmd = ["yt-dlp", "-F", "--no-check-certificates", url]
    subprocess.run(cmd)


def get_quality_filter_arg(quality):
    """Map quality string to height value for filtering."""
    return quality.replace("p", "")