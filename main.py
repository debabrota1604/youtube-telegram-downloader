#!/usr/bin/env python3
"""
YouTube Video/Audio Downloader and Converter - Entry Point.

This file delegates to the modular downloader package.
The original monolithic implementation has been refactored into downloader/*.py

Usage:
    python main.py <youtube_url> [options]

Examples:
    python main.py https://www.youtube.com/watch?v=VIDEO_ID
    python main.py https://www.youtube.com/watch?v=VIDEO_ID --audio-only
    python main.py https://www.youtube.com/watch?v=VIDEO_ID --format mkv --video-quality 720p
    python main.py https://www.youtube.com/watch?v=VIDEO_ID --android-auto
    python main.py https://www.youtube.com/watch?v=VIDEO_ID --playlist
"""

import sys
import os

# Ensure the project root is on sys.path so that the 'downloader' package
# is importable when running 'python main.py' from the project directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from downloader.cli import main

if __name__ == "__main__":
    main()