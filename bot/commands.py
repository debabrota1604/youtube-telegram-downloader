"""
Telegram command handlers: /start, /help, /video, /audio, /playlist,
/settings, /quality, /bitrate, /format, /queue, /queue-clear, /cancel,
/clear-cache.
"""

import re

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from bot.config import ALLOWED_USERS, DEFAULT_SETTINGS, USER_SETTINGS, IS_DOWNLOADING
from bot.logger import log_user_activity
from bot.queue import get_queue, clear_queue
from bot.downloader import DOWNLOAD_TASKS


def get_user_settings(user_id) -> dict:
    """Get or create settings for a user."""
    if user_id not in USER_SETTINGS:
        USER_SETTINGS[user_id] = DEFAULT_SETTINGS.copy()
    return USER_SETTINGS[user_id]


def is_authorized(user_id) -> bool:
    """Check if user is authorized."""
    if not ALLOWED_USERS:
        return True
    return str(user_id) in ALLOWED_USERS


# ─── Command handlers ────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    user_id = update.effective_user.id

    if not is_authorized(user_id):
        log_user_activity(user_id, "/start", response_text="❌ Not authorized")
        await update.message.reply_text("❌ You are not authorized to use this bot.")
        return

    log_user_activity(user_id, "/start", response_text="Welcome message sent")

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
        f"You'll see a preview before downloading.\n\n"
        f"✨ Features:\n"
        f"📸 Preview  •  📊 Progress  •  🛑 Cancel  •  📋 Queue\n"
        f"🏷️ Auto-embeds metadata (title, artist, album art)\n\n"
        f"Or choose a mode below:\n\n"
        f"Your Chat ID: <code>{user_id}</code>"
    )
    await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    user_id = update.effective_user.id
    log_user_activity(user_id, "/help", response_text="Help message sent")
    help_text = (
        "📱 <b>YouTube Downloader Bot</b>\n\n"
        "<b>Quick Use:</b> Just paste a YouTube link!\n"
        "You'll see a preview before downloading.\n"
        "Playlist URLs are auto-detected.\n\n"
        "<b>Commands:</b>\n"
        "<code>/video</code> — Set mode to video download\n"
        "<code>/audio</code> — Set mode to audio-only\n"
        "<code>/playlist</code> — Set mode to playlist download\n"
        "<code>/quality 720p</code> — Set video quality\n"
        "<code>/bitrate 320k</code> — Set audio bitrate\n"
        "<code>/format mp3</code> — Set output format\n"
        "<code>/settings</code> — Show current settings\n"
        "<code>/queue</code> — Show download queue\n"
        "<code>/queue-clear</code> — Clear download queue\n"
        "<code>/cancel</code> — Cancel current download\n\n"
        "<b>Features:</b>\n"
        "📸 Preview before download\n"
        "📊 Real-time progress tracking\n"
        "🛑 Cancel downloads anytime\n"
        "📋 Queue multiple downloads\n"
        "🏷️ Auto-embed metadata (title, artist, album art)\n\n"
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
    user_id = update.effective_user.id
    settings = get_user_settings(user_id)
    settings["mode"] = "video"
    log_user_activity(user_id, "/video", response_text="Mode set to video")
    await update.message.reply_text(
        "🎬 Mode set to <b>Video</b>\n"
        f"Format: {settings['video_format']} | Quality: {settings['video_quality']}\n\n"
        "Send a YouTube link to start downloading!",
        parse_mode="HTML",
    )


async def cmd_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set mode to audio."""
    user_id = update.effective_user.id
    settings = get_user_settings(user_id)
    settings["mode"] = "audio"
    log_user_activity(user_id, "/audio", response_text="Mode set to audio")
    await update.message.reply_text(
        "🎵 Mode set to <b>Audio Only</b>\n"
        f"Format: {settings['audio_format']} | Bitrate: {settings['audio_bitrate']}\n\n"
        "Send a YouTube link to start downloading!",
        parse_mode="HTML",
    )


async def cmd_playlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set mode to playlist."""
    user_id = update.effective_user.id
    settings = get_user_settings(user_id)
    settings["_playlist_submode"] = settings["mode"]
    settings["mode"] = "playlist"
    log_user_activity(user_id, "/playlist", response_text="Mode set to playlist")
    submode_label = "🎵 Audio" if settings["_playlist_submode"] == "audio" else "🎬 Video"
    await update.message.reply_text(
        f"📺 Mode set to <b>Playlist Download</b> ({submode_label})\n\n"
        "Send a YouTube playlist URL to download all videos.\n"
        "Each video will be sent individually.\n\n"
        "Tip: Use /audio before /playlist for audio-only downloads.",
        parse_mode="HTML",
    )


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current settings."""
    user_id = update.effective_user.id
    settings = get_user_settings(user_id)
    log_user_activity(user_id, "/settings", response_text="Settings shown")
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
    user_id = update.effective_user.id
    if not context.args:
        log_user_activity(user_id, "/quality", response_text="Missing argument")
        await update.message.reply_text("Usage: <code>/quality 720p</code>", parse_mode="HTML")
        return
    quality = context.args[0].lower()
    valid = ["360p", "480p", "720p", "1080p", "1440p", "2160p"]
    if quality not in valid:
        log_user_activity(user_id, "/quality", request_text=quality, response_text="Invalid quality")
        await update.message.reply_text(
            f"Invalid quality. Choose from: {', '.join(valid)}"
        )
        return
    settings = get_user_settings(user_id)
    settings["video_quality"] = quality
    log_user_activity(user_id, "/quality", request_text=quality, response_text="Quality updated")
    await update.message.reply_text(f"✅ Video quality set to <b>{quality}</b>", parse_mode="HTML")


async def cmd_bitrate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set audio bitrate."""
    user_id = update.effective_user.id
    if not context.args:
        log_user_activity(user_id, "/bitrate", response_text="Missing argument")
        await update.message.reply_text("Usage: <code>/bitrate 256k</code>", parse_mode="HTML")
        return
    bitrate = context.args[0].lower()
    valid = ["128k", "192k", "256k", "320k"]
    if bitrate not in valid:
        log_user_activity(user_id, "/bitrate", request_text=bitrate, response_text="Invalid bitrate")
        await update.message.reply_text(
            f"Invalid bitrate. Choose from: {', '.join(valid)}"
        )
        return
    settings = get_user_settings(user_id)
    settings["audio_bitrate"] = bitrate
    log_user_activity(user_id, "/bitrate", request_text=bitrate, response_text="Bitrate updated")
    await update.message.reply_text(f"✅ Audio bitrate set to <b>{bitrate}</b>", parse_mode="HTML")


async def cmd_format(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set output format."""
    user_id = update.effective_user.id
    if not context.args:
        log_user_activity(user_id, "/format", response_text="Missing argument")
        await update.message.reply_text("Usage: <code>/format mp4</code>", parse_mode="HTML")
        return
    fmt = context.args[0].lower()
    settings = get_user_settings(user_id)
    if settings["mode"] == "video":
        valid = ["mp4", "mkv", "webm"]
        if fmt not in valid:
            log_user_activity(user_id, "/format", request_text=fmt, response_text="Invalid video format")
            await update.message.reply_text(
                f"Invalid video format. Choose from: {', '.join(valid)}"
            )
            return
        settings["video_format"] = fmt
    else:
        valid = ["m4a", "mp3", "flac", "aac", "ogg"]
        if fmt not in valid:
            log_user_activity(user_id, "/format", request_text=fmt, response_text="Invalid audio format")
            await update.message.reply_text(
                f"Invalid audio format. Choose from: {', '.join(valid)}"
            )
            return
        settings["audio_format"] = fmt
    log_user_activity(user_id, "/format", request_text=fmt, response_text="Format updated")
    await update.message.reply_text(f"✅ Format set to <b>{fmt}</b>", parse_mode="HTML")


async def cmd_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the download queue."""
    user_id = update.effective_user.id
    log_user_activity(user_id, "/queue", response_text="Queue shown")
    queue = get_queue(user_id)

    if not queue:
        await update.message.reply_text("📋 Your download queue is empty.")
        return

    text = f"📋 <b>Download Queue</b> ({len(queue)} items)\n\n"

    # Show current download if any
    task_id = IS_DOWNLOADING.get(user_id)
    if task_id:
        text += "🔄 <b>Currently downloading...</b>\n\n"

    for i, item in enumerate(queue):
        url = item["url"]
        # Extract video ID for compact display
        vid_match = re.search(r'[vV]=([a-zA-Z0-9_-]+)', url)
        short_id = vid_match.group(1) if vid_match else url[:25]
        text += f"  {i + 1}. <code>{short_id}</code>\n"

    keyboard = [[InlineKeyboardButton("🗑️ Clear Queue", callback_data="queue:clear")]]

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cmd_queue_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear the download queue."""
    user_id = update.effective_user.id
    count = clear_queue(user_id)
    log_user_activity(user_id, "/queue-clear", response_text=f"Queue cleared ({count} items)")
    await update.message.reply_text(
        f"🗑️ Queue cleared ({count} item{'s' if count != 1 else ''} removed)."
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel the current download."""
    user_id = update.effective_user.id
    task_id = IS_DOWNLOADING.get(user_id)

    if not task_id:
        log_user_activity(user_id, "/cancel", response_text="No active download")
        await update.message.reply_text("⚡ No active download to cancel.")
        return

    task = DOWNLOAD_TASKS.get(task_id)
    if not task:
        log_user_activity(user_id, "/cancel", response_text="No active download")
        await update.message.reply_text("⚡ No active download to cancel.")
        IS_DOWNLOADING.pop(user_id, None)
        return

    task["cancelled"] = True
    log_user_activity(user_id, "/cancel", response_text="Cancellation initiated")
    await update.message.reply_text("🛑 Cancellation initiated. Please wait...")


async def cmd_clear_cache(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear all temporary download files."""
    user_id = update.effective_user.id
    from bot.utils import clear_temp_dir
    count = clear_temp_dir()
    log_user_activity(user_id, "/clear-cache", response_text=f"Cache cleared ({count} folders)")
    if count > 0:
        await update.message.reply_text(
            f"🗑️ Cache cleared ({count} temp folder{'s' if count != 1 else ''} removed)."
        )
    else:
        await update.message.reply_text("📁 Cache is already empty.")


# ─── Public list for easy registration ───────────────────────────────────────

COMMAND_HANDLERS = [
    ("start", cmd_start),
    ("help", cmd_help),
    ("video", cmd_video),
    ("audio", cmd_audio),
    ("playlist", cmd_playlist),
    ("settings", cmd_settings),
    ("quality", cmd_quality),
    ("bitrate", cmd_bitrate),
    ("format", cmd_format),
    ("queue", cmd_queue),
    ("queue_clear", cmd_queue_clear),
    ("cancel", cmd_cancel),
    ("clear_cache", cmd_clear_cache),
]