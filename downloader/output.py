"""Output file naming, verification, and file operations."""

import os

from downloader.config import FORMAT_CONFIG, AUDIO_FORMATS


def make_output_filename(title, video_id, media_type, format_name):
    """Generate output filename: {title} [{video_id}][{type}]{ext}.

    Args:
        title: Sanitized video title
        video_id: YouTube video ID
        media_type: 'video' or 'audio'
        format_name: Format key (e.g., 'mp4', 'mp3')
    """
    ext = FORMAT_CONFIG[format_name]["extension"]
    return f"{title} [{video_id}][{media_type}]{ext}"


def make_playlist_output_filename(title, video_id, media_type, format_name):
    """Generate output filename for playlist items."""
    ext = FORMAT_CONFIG[format_name]["extension"]
    return f"{title} [{video_id}][{media_type}]{ext}"


def verify_output(output_path, ext):
    """Verify output file exists, renaming from alternative extension if needed."""
    if os.path.exists(output_path):
        return True

    for test_ext in [".mp4", ".mkv", ".webm"] + AUDIO_FORMATS:
        actual_ext = test_ext if test_ext.startswith(".") else f".{test_ext}"
        alt_path = output_path.replace(ext, actual_ext)
        if os.path.exists(alt_path) and alt_path != output_path:
            os.rename(alt_path, output_path)
            return True

    return False