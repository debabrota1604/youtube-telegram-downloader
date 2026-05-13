"""
Video info extraction and audio metadata embedding.
"""

import json
import subprocess
import urllib.request
from pathlib import Path


# ─── Video info extraction ──────────────────────────────────────────────────

def get_video_info(url: str, timeout: int = 30) -> dict | None:
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


# ─── Thumbnail download ─────────────────────────────────────────────────────

def _download_thumbnail(thumbnail_url: str, timeout: int = 10) -> bytes | None:
    """Download thumbnail image data. Returns bytes or None."""
    if not thumbnail_url:
        return None
    try:
        req = urllib.request.Request(thumbnail_url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        })
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read()
    except Exception:
        return None


# ─── Format-specific metadata embedding ──────────────────────────────────────

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


# ─── Public API ──────────────────────────────────────────────────────────────

def embed_audio_metadata(file_path: str, video_info: dict | None, audio_format: str):
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