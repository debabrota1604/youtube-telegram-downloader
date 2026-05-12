#!/usr/bin/env python3
"""
Telegram Bot for YouTube Video/Audio Download
Sends a YouTube link to the bot, it downloads and converts on your Mac,
then sends the file back to you via Telegram.

Usage:
    1. Create a bot via @BotFather on Telegram and get the TOKEN
    2. Get your chat_id by sending /start to the bot
    3. Set BOT_TOKEN in .env or export it
    4. Run: python3 youtube_telegram_bot.py

Commands:
    /start       - Start the bot, shows your chat ID
    /video       - Download as video (MP4/H.264/AAC, 1080p)
    /audio       - Download as audio only (M4A/AAC, 256k)
    /playlist    - Download entire playlist
    /settings    - Show current settings
    /quality     - Set video quality: /quality 720p
    /bitrate     - Set audio bitrate: /bitrate 320k
    /format      - Set format: /format mkv or /format flac
    /help        - Show help

You can also just paste a YouTube link and the bot will download it
using the last used mode (video/audio). Playlist URLs are auto-detected.
"""

import os
import re
import subprocess
import sys
import asyncio
import tempfile
from pathlib import Path
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# Load environment variables from .env file
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Default user IDs (empty = allow all users)
ALLOWED_USERS = os.getenv("ALLOWED_USERS", "").split(",")
ALLOWED_USERS = [u.strip() for u in ALLOWED_USERS if u.strip()]

# Storage for per-user settings and state
USER_STATE = {}
USER_SETTINGS = {}

# Default settings
DEFAULT_SETTINGS = {
    "mode": "video",          # video or audio
    "video_quality": "1080p",
    "audio_bitrate": "256k",
    "audio_format": "m4a",    # for audio-only: m4a, mp3, flac
    "video_format": "mp4",    # for video: mp4, mkv
}

# YouTube URL pattern
YOUTUBE_PATTERN = re.compile(
    r"(https?://)?(www\.)?(youtube\.com/(watch\?v=|shorts/|live/|embed/|v/|playlist\?list=)|youtu\.be/)[a-zA-Z0-9_-]+"
)

# YouTube playlist URL pattern
YOUTUBE_PLAYLIST_PATTERN = re.compile(
    r"(https?://)?(www\.)?youtube\.com/playlist\?list=[a-zA-Z0-9_-]+"
)

# YouTube video with playlist parameter
YOUTUBE_VIDEO_WITH_LIST_PATTERN = re.compile(
    r"(https?://)?(www\.)?youtube\.com/watch\?v=[a-zA-Z0-9_-]+&list=[a-zA-Z0-9_-]+"
)

# Path to main.py downloader
DOWNLOADER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")


def get_user_settings(user_id):
    """Get or create settings for a user."""
    if user_id not in USER_SETTINGS:
        USER_SETTINGS[user_id] = DEFAULT_SETTINGS.copy()
    return USER_SETTINGS[user_id]


def is_authorized(user_id):
    """Check if user is authorized."""
    if not ALLOWED_USERS:
        return True
    return str(user_id) in ALLOWED_USERS


def is_youtube_link(text):
    """Check if text contains a YouTube link."""
    return bool(YOUTUBE_PATTERN.search(text))


def extract_youtube_link(text):
    """Extract YouTube link from text."""
    match = YOUTUBE_PATTERN.search(text)
    return match.group(0) if match else None


def is_playlist_url(url):
    """Check if a URL is a YouTube playlist."""
    if YOUTUBE_PLAYLIST_PATTERN.search(url):
        return True
    if YOUTUBE_VIDEO_WITH_LIST_PATTERN.search(url):
        return True
    return False


async def run_downloader(url, settings, output_dir):
    """Run main.py to download and convert the YouTube video."""
    cmd = [
        "python3", DOWNLOADER_SCRIPT,
        url,
        "--method", "ytdlp",  # Use ytdlp method for reliability in bot context
        "--output", output_dir,
    ]

    mode = settings["mode"]
    if mode == "audio":
        cmd.extend(["--audio-only", "--audio-format", settings["audio_format"],
                     "--audio-bitrate", settings["audio_bitrate"]])
    else:
        cmd.extend(["--format", settings["video_format"],
                     "--video-quality", settings["video_quality"],
                     "--audio-bitrate", settings["audio_bitrate"]])

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        error_msg = stderr.decode() if stderr else "Unknown error"
        raise RuntimeError(f"Download failed:\n{error_msg[:500]}")

    return stdout.decode()


async def run_playlist_downloader(url, settings, output_dir):
    """Run main.py to download an entire YouTube playlist."""
    cmd = [
        "python3", DOWNLOADER_SCRIPT,
        url,
        "--playlist",
        "--method", "ytdlp",
        "--output", output_dir,
    ]

    mode = settings["mode"]
    if mode == "audio":
        cmd.extend(["--audio-only", "--audio-format", settings["audio_format"],
                     "--audio-bitrate", settings["audio_bitrate"]])
    else:
        cmd.extend(["--format", settings["video_format"],
                     "--video-quality", settings["video_quality"],
                     "--audio-bitrate", settings["audio_bitrate"]])

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        error_msg = stderr.decode() if stderr else "Unknown error"
        raise RuntimeError(f"Playlist download failed:\n{error_msg[:500]}")

    return stdout.decode()


def find_output_file(output_dir, settings):
    """Find the downloaded file in the output directory."""
    if settings["mode"] == "audio":
        ext = f".{settings['audio_format']}"
    else:
        ext = f".{settings['video_format']}"

    for f in Path(output_dir).iterdir():
        if f.is_file() and f.suffix == ext:
            return str(f)

    # Fallback: find any recent media file
    for ext in [".mp4", ".mkv", ".webm", ".m4a", ".mp3", ".flac", ".aac", ".ogg"]:
        for f in Path(output_dir).iterdir():
            if f.is_file() and f.suffix == ext:
                return str(f)

    return None


def find_playlist_output_files(output_dir, settings):
    """Find all downloaded files in the playlist output directory."""
    files = []

    # Determine expected extensions
    if settings["mode"] == "audio":
        expected_ext = f".{settings['audio_format']}"
    else:
        expected_ext = f".{settings['video_format']}"

    # Walk through all subdirectories (playlist creates subdirs)
    for root, _, filenames in os.walk(output_dir):
        for filename in filenames:
            filepath = os.path.join(root, filename)
            # Match expected extension or common media extensions
            if filename.endswith(expected_ext):
                files.append(filepath)
            elif any(filename.endswith(ext) for ext in [".mp4", ".mkv", ".webm", ".m4a", ".mp3", ".flac", ".aac", ".ogg"]):
                files.append(filepath)

    return files


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    user_id = update.effective_user.id

    if not is_authorized(user_id):
        await update.message.reply_text("❌ You are not authorized to use this bot.")
        return

    name = update.effective_user.first_name
    keyboard = [
        [
            InlineKeyboardButton("🎬 Video (MP4)", callback_data="mode:video"),
            InlineKeyboardButton("🎵 Audio (M4A)", callback_data="mode:audio"),
        ],
        [
            InlineKeyboardButton("📺 Playlist", callback_data="mode:playlist"),
            InlineKeyboardButton("⚙️ Settings", callback_data="settings"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        f"👋 Hello {name}!\n\n"
        f"I can download YouTube videos/audio for your Android Auto.\n\n"
        f"📎 Just send me a YouTube link!\n"
        f"Or choose a mode below:\n\n"
        f"Your Chat ID: <code>{user_id}</code>"
    )
    await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    help_text = (
        "📱 <b>YouTube Downloader Bot</b>\n\n"
        "<b>Quick Use:</b> Just paste a YouTube link!\n"
        "Playlist URLs are auto-detected.\n\n"
        "<b>Commands:</b>\n"
        "<code>/video</code> — Set mode to video download\n"
        "<code>/audio</code> — Set mode to audio-only\n"
        "<code>/playlist</code> — Set mode to playlist download\n"
        "<code>/quality 720p</code> — Set video quality\n"
        "<code>/bitrate 320k</code> — Set audio bitrate\n"
        "<code>/format mp4</code> — Set output format\n"
        "<code>/settings</code> — Show current settings\n\n"
        "<b>Supported:</b>\n"
        "Video: mp4, mkv | Quality: 360p-2160p\n"
        "Audio: m4a, mp3, flac | Bitrate: 128k-320k\n\n"
        "<b>Playlist:</b>\n"
        "Send a playlist URL or use /playlist mode.\n"
        "Each video is sent individually.\n\n"
        "Files are sent directly to you via Telegram.\n"
        "Max file size: 2GB"
    )
    await update.message.reply_text(help_text, parse_mode="HTML")


async def cmd_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set mode to video."""
    settings = get_user_settings(update.effective_user.id)
    settings["mode"] = "video"
    await update.message.reply_text(
        "🎬 Mode set to <b>Video</b>\n"
        f"Format: {settings['video_format']} | Quality: {settings['video_quality']}\n\n"
        "Send a YouTube link to start downloading!",
        parse_mode="HTML",
    )


async def cmd_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set mode to audio."""
    settings = get_user_settings(update.effective_user.id)
    settings["mode"] = "audio"
    await update.message.reply_text(
        "🎵 Mode set to <b>Audio Only</b>\n"
        f"Format: {settings['audio_format']} | Bitrate: {settings['audio_bitrate']}\n\n"
        "Send a YouTube link to start downloading!",
        parse_mode="HTML",
    )


async def cmd_playlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set mode to playlist."""
    settings = get_user_settings(update.effective_user.id)
    settings["mode"] = "playlist"
    mode_label = "audio" if settings.get("_playlist_submode") == "audio" else "video"
    await update.message.reply_text(
        "📺 Mode set to <b>Playlist Download</b>\n\n"
        "Send a YouTube playlist URL to download all videos.\n"
        "Each video will be sent individually.\n\n"
        "Tip: Use /audio before /playlist for audio-only downloads.",
        parse_mode="HTML",
    )


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current settings."""
    settings = get_user_settings(update.effective_user.id)
    text = (
        "⚙️ <b>Current Settings</b>\n\n"
        f"Mode: <b>{'🎬 Video' if settings['mode'] == 'video' else '🎵 Audio' if settings['mode'] == 'audio' else '📺 Playlist'}</b>\n"
    )
    if settings["mode"] == "video":
        text += f"Video Format: <code>{settings['video_format']}</code>\n"
        text += f"Video Quality: <code>{settings['video_quality']}</code>\n"
    elif settings["mode"] == "audio":
        text += f"Audio Format: <code>{settings['audio_format']}</code>\n"
    else:
        text += f"Video Format: <code>{settings['video_format']}</code>\n"
        text += f"Video Quality: <code>{settings['video_quality']}</code>\n"
        text += f"Audio Format: <code>{settings['audio_format']}</code>\n"
    text += f"Audio Bitrate: <code>{settings['audio_bitrate']}</code>\n\n"
    text += "Use /quality, /bitrate, /format to change settings."
    await update.message.reply_text(text, parse_mode="HTML")


async def cmd_quality(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set video quality."""
    if not context.args:
        await update.message.reply_text("Usage: <code>/quality 720p</code>", parse_mode="HTML")
        return
    quality = context.args[0].lower()
    valid = ["360p", "480p", "720p", "1080p", "1440p", "2160p"]
    if quality not in valid:
        await update.message.reply_text(
            f"Invalid quality. Choose from: {', '.join(valid)}"
        )
        return
    settings = get_user_settings(update.effective_user.id)
    settings["video_quality"] = quality
    await update.message.reply_text(f"✅ Video quality set to <b>{quality}</b>", parse_mode="HTML")


async def cmd_bitrate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set audio bitrate."""
    if not context.args:
        await update.message.reply_text("Usage: <code>/bitrate 256k</code>", parse_mode="HTML")
        return
    bitrate = context.args[0].lower()
    valid = ["128k", "192k", "256k", "320k"]
    if bitrate not in valid:
        await update.message.reply_text(
            f"Invalid bitrate. Choose from: {', '.join(valid)}"
        )
        return
    settings = get_user_settings(update.effective_user.id)
    settings["audio_bitrate"] = bitrate
    await update.message.reply_text(f"✅ Audio bitrate set to <b>{bitrate}</b>", parse_mode="HTML")


async def cmd_format(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set output format."""
    if not context.args:
        await update.message.reply_text("Usage: <code>/format mp4</code>", parse_mode="HTML")
        return
    fmt = context.args[0].lower()
    settings = get_user_settings(update.effective_user.id)
    if settings["mode"] == "video":
        valid = ["mp4", "mkv", "webm"]
        if fmt not in valid:
            await update.message.reply_text(
                f"Invalid video format. Choose from: {', '.join(valid)}"
            )
            return
        settings["video_format"] = fmt
    else:
        valid = ["m4a", "mp3", "flac", "aac", "ogg"]
        if fmt not in valid:
            await update.message.reply_text(
                f"Invalid audio format. Choose from: {', '.join(valid)}"
            )
            return
        settings["audio_format"] = fmt
    await update.message.reply_text(f"✅ Format set to <b>{fmt}</b>", parse_mode="HTML")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button callbacks."""
    query = update.callback_query
    await query.answer()

    data = query.data
    user_id = query.from_user.id
    settings = get_user_settings(user_id)

    if data.startswith("mode:"):
        mode = data.split(":")[1]
        settings["mode"] = mode
        if mode == "video":
            await query.edit_message_text(
                "🎬 Mode set to <b>Video</b>\nSend a YouTube link!",
                parse_mode="HTML",
            )
        elif mode == "audio":
            await query.edit_message_text(
                "🎵 Mode set to <b>Audio Only</b>\nSend a YouTube link!",
                parse_mode="HTML",
            )
        elif mode == "playlist":
            await query.edit_message_text(
                "📺 Mode set to <b>Playlist</b>\nSend a YouTube playlist URL!",
                parse_mode="HTML",
            )

    elif data == "settings":
        text = (
            "⚙️ <b>Settings</b>\n\n"
            f"Mode: <b>{'Video' if settings['mode'] == 'video' else 'Audio' if settings['mode'] == 'audio' else 'Playlist'}</b>\n"
        )
        if settings["mode"] in ("video", "playlist"):
            text += f"Format: <code>{settings['video_format']}</code>\n"
            text += f"Quality: <code>{settings['video_quality']}</code>\n"
        else:
            text += f"Format: <code>{settings['audio_format']}</code>\n"
        text += f"Bitrate: <code>{settings['audio_bitrate']}</code>"
        await query.edit_message_text(text, parse_mode="HTML")


async def send_file_to_user(update, file_path, settings, total_files=None, current_index=None):
    """Send a downloaded file to the user via Telegram."""
    file_size = os.path.getsize(file_path)
    file_size_mb = file_size / (1024 * 1024)
    file_name = Path(file_path).name

    # Telegram file limit is 2GB
    if file_size > 2 * 1024 * 1024 * 1024:
        return f"SKIPPED:{file_name}(too large:{file_size_mb:.1f}MB)"

    progress_text = ""
    if total_files and current_index is not None:
        progress_text = f" ({current_index + 1}/{total_files})"

    with open(file_path, "rb") as f:
        if settings["mode"] == "audio" or (settings["mode"] == "playlist" and False):
            # For playlist, detect by extension
            if file_path.lower().endswith((".m4a", ".mp3", ".flac", ".aac", ".ogg")):
                await update.message.reply_audio(
                    audio=f,
                    filename=file_name,
                    caption=f"🎵 {file_name}{progress_text}",
                    timeout=600,
                )
            else:
                await update.message.reply_video(
                    video=f,
                    filename=file_name,
                    supports_streaming=True,
                    caption=f"🎬 {file_name}{progress_text}",
                    timeout=600,
                )
        else:
            await update.message.reply_video(
                video=f,
                filename=file_name,
                supports_streaming=True,
                caption=f"🎬 {file_name}{progress_text}",
                timeout=600,
            )

    return f"OK:{file_name}"


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle YouTube link messages."""
    text = update.message.text
    if not is_youtube_link(text):
        await update.message.reply_text(
            "🔗 Please send a valid YouTube link.\n"
            "Examples:\n"
            "https://www.youtube.com/watch?v=VIDEO_ID\n"
            "https://youtu.be/VIDEO_ID\n"
            "https://youtube.com/shorts/VIDEO_ID\n"
            "https://youtube.com/playlist?list=PLAYLIST_ID"
        )
        return

    url = extract_youtube_link(text)
    user_id = update.effective_user.id
    settings = get_user_settings(user_id)

    # Detect if this is a playlist URL
    playlist_mode = is_playlist_url(url) or settings["mode"] == "playlist"

    if playlist_mode:
        await handle_playlist_message(update, url, settings)
    else:
        await handle_single_video_message(update, url, settings)


async def handle_single_video_message(update, url, settings):
    """Handle single video download."""
    mode_emoji = "🎬" if settings["mode"] == "video" else "🎵"
    status_msg = await update.message.reply_text(
        f"{mode_emoji} <b>Processing...</b>\n"
        f"⏳ Extracting video info...",
        parse_mode="HTML",
    )

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            await status_msg.edit_text(
                f"{mode_emoji} <b>Downloading...</b>\n"
                f"⏳ This may take a few minutes...",
                parse_mode="HTML",
            )

            output = await run_downloader(url, settings, tmpdir)

            file_path = find_output_file(tmpdir, settings)

            if not file_path:
                raise RuntimeError("Download completed but output file not found.")

            file_size = os.path.getsize(file_path)
            file_size_mb = file_size / (1024 * 1024)

            if file_size > 2 * 1024 * 1024 * 1024:
                raise RuntimeError(f"File is too large ({file_size_mb:.1f}MB). Telegram limit is 2GB.")

            await status_msg.edit_text(
                f"{mode_emoji} <b>Uploading to Telegram...</b>\n"
                f"📁 Size: {file_size_mb:.1f} MB",
                parse_mode="HTML",
            )

            file_name = Path(file_path).name
            with open(file_path, "rb") as f:
                if settings["mode"] == "audio":
                    await update.message.reply_audio(
                        audio=f,
                        filename=file_name,
                        timeout=600,
                    )
                else:
                    await update.message.reply_video(
                        video=f,
                        filename=file_name,
                        supports_streaming=True,
                        timeout=600,
                    )

            await status_msg.edit_text(
                f"✅ <b>Done!</b>\n"
                f"📁 {file_name} ({file_size_mb:.1f} MB)\n\n"
                f"Send another link to download more.",
                parse_mode="HTML",
            )

    except RuntimeError as e:
        error_msg = str(e)
        if len(error_msg) > 2000:
            error_msg = error_msg[:1997] + "..."
        await status_msg.edit_text(
            f"❌ <b>Error:</b>\n<code>{error_msg[:1000]}</code>",
            parse_mode="HTML",
        )
    except Exception as e:
        await status_msg.edit_text(
            f"❌ <b>Unexpected error:</b>\n<code>{str(e)[:1000]}</code>",
            parse_mode="HTML",
        )


async def handle_playlist_message(update, url, settings):
    """Handle playlist download."""
    mode_emoji = "📺"
    status_msg = await update.message.reply_text(
        f"{mode_emoji} <b>Playlist Detected!</b>\n"
        f"⏳ Extracting playlist info...",
        parse_mode="HTML",
    )

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            await status_msg.edit_text(
                f"{mode_emoji} <b>Downloading Playlist...</b>\n"
                f"⏳ This may take a while for large playlists...\n"
                f"📥 Downloading all videos...",
                parse_mode="HTML",
            )

            output = await run_playlist_downloader(url, settings, tmpdir)

            # Parse output to get success/fail count
            output_lines = output.split("\n")
            total_downloaded = 0
            total_failed = 0
            for line in output_lines:
                if "[SUCCESS]" in line:
                    total_downloaded += 1
                if "[FAILED]" in line:
                    total_failed += 1

            # Find all downloaded files
            files = find_playlist_output_files(tmpdir, settings)

            if not files:
                raise RuntimeError("Playlist download completed but no output files found.")

            await status_msg.edit_text(
                f"{mode_emoji} <b>Playlist Downloaded!</b>\n"
                f"✅ {total_downloaded} videos downloaded\n"
                f"❌ {total_failed} videos failed\n\n"
                f"📤 Uploading {len(files)} files to Telegram...",
                parse_mode="HTML",
            )

            # Send each file to the user
            sent_count = 0
            skipped_count = 0
            for idx, file_path in enumerate(files):
                file_size = os.path.getsize(file_path)
                file_size_mb = file_size / (1024 * 1024)
                file_name = Path(file_path).name

                # Skip files too large for Telegram
                if file_size > 2 * 1024 * 1024 * 1024:
                    skipped_count += 1
                    continue

                try:
                    with open(file_path, "rb") as f:
                        if file_path.lower().endswith((".m4a", ".mp3", ".flac", ".aac", ".ogg")):
                            await update.message.reply_audio(
                                audio=f,
                                filename=file_name,
                                caption=f"🎵 {file_name} ({idx + 1}/{len(files)})",
                                timeout=600,
                            )
                        else:
                            await update.message.reply_video(
                                video=f,
                                filename=file_name,
                                supports_streaming=True,
                                caption=f"🎬 {file_name} ({idx + 1}/{len(files)})",
                                timeout=600,
                            )
                    sent_count += 1
                except Exception as e:
                    skipped_count += 1
                    continue

            await status_msg.edit_text(
                f"✅ <b>Playlist Complete!</b>\n\n"
                f"📁 Downloaded: {total_downloaded} videos\n"
                f"❌ Failed: {total_failed} videos\n"
                f"📤 Sent: {sent_count} files\n"
                f"⏭️ Skipped: {skipped_count} files\n\n"
                f"Send another link to download more.",
                parse_mode="HTML",
            )

    except RuntimeError as e:
        error_msg = str(e)
        if len(error_msg) > 2000:
            error_msg = error_msg[:1997] + "..."
        await status_msg.edit_text(
            f"❌ <b>Error:</b>\n<code>{error_msg[:1000]}</code>",
            parse_mode="HTML",
        )
    except Exception as e:
        await status_msg.edit_text(
            f"❌ <b>Unexpected error:</b>\n<code>{str(e)[:1000]}</code>",
            parse_mode="HTML",
        )


def check_dependencies():
    """Check that all required packages are installed."""
    missing = []

    try:
        import telegram
    except ImportError:
        missing.append("python-telegram-bot")

    try:
        import dotenv
    except ImportError:
        missing.append("python-dotenv")

    try:
        import yt_dlp
    except ImportError:
        missing.append("yt-dlp")

    # Check ffmpeg
    result = subprocess.run(["which", "ffmpeg"], capture_output=True)
    if result.returncode != 0:
        missing.append("ffmpeg (install: brew install ffmpeg)")

    # Check downloader script exists
    if not os.path.exists(DOWNLOADER_SCRIPT):
        print(f"[ERROR] Downloader script not found: {DOWNLOADER_SCRIPT}")
        print("Make sure main.py is in the same directory.")
        sys.exit(1)

    if missing:
        print("[ERROR] Missing dependencies:")
        for m in missing:
            print(f"  - {m}")
        print("\nInstall with:")
        print("  pip install python-telegram-bot python-dotenv yt-dlp")
        sys.exit(1)


def main():
    """Bot entry point."""
    print("=" * 50)
    print("  YouTube Telegram Bot")
    print("=" * 50)

    # Check dependencies
    check_dependencies()

    # Check bot token
    if not BOT_TOKEN:
        print("\n[ERROR] BOT_TOKEN not set!")
        print("Create a .env file with:")
        print("  BOT_TOKEN=your_bot_token_here")
        print("\nGet a token from @BotFather on Telegram.")
        sys.exit(1)

    print(f"\n[BOT] Token configured")
    if ALLOWED_USERS:
        print(f"[BOT] Restricted to users: {ALLOWED_USERS}")
    else:
        print("[BOT] Open to all users")
    print(f"[BOT] Downloader: {DOWNLOADER_SCRIPT}")
    print("\n[BOT] Starting bot... Press Ctrl+C to stop.\n")

    # Build and run bot
    app = Application.builder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("video", cmd_video))
    app.add_handler(CommandHandler("audio", cmd_audio))
    app.add_handler(CommandHandler("playlist", cmd_playlist))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("quality", cmd_quality))
    app.add_handler(CommandHandler("bitrate", cmd_bitrate))
    app.add_handler(CommandHandler("format", cmd_format))

    # Inline buttons
    app.add_handler(CallbackQueryHandler(handle_callback))

    # YouTube links (text messages, not commands)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Run bot
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()