"""
Message handler and callback query handler.
"""

import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path

import telegram.error

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from bot.config import (
    DOWNLOAD_TASKS,
    IS_DOWNLOADING,
    TEMP_BASE_DIR,
    USER_STATE,
    USER_SETTINGS,
    DEFAULT_SETTINGS,
)
from bot.logger import log_user_activity
from bot.queue import add_to_queue, clear_queue
from bot.downloader import (
    DownloadCancelledError,
    download_and_send,
    drain_queue,
    run_playlist_downloader,
    show_preview,
)
from bot.utils import (
    extract_youtube_link,
    is_playlist_url,
    is_youtube_link,
    find_playlist_output_files,
    format_file_size,
    format_progress_bar,
)
from bot.metadata import get_video_info
from bot.commands import is_authorized, get_user_settings


# ─── Message handler ─────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle YouTube link messages."""
    user_id = update.effective_user.id
    text = update.message.text

    if not is_authorized(user_id):
        log_user_activity(user_id, "message", request_text=text, response_text="❌ Not authorized")
        await update.message.reply_text("❌ You are not authorized to use this bot.")
        return

    if not is_youtube_link(text):
        log_user_activity(user_id, "message", request_text=text, response_text="Invalid YouTube link")
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
    chat_id = update.effective_chat.id
    settings = get_user_settings(user_id)

    # Log the download request
    log_user_activity(user_id, "download_request", request_text=url,
                      response_text=f"Mode: {settings['mode']}")

    # Check if already downloading - add to queue
    if IS_DOWNLOADING.get(user_id):
        added, msg = add_to_queue(user_id, url, settings.copy(), chat_id)
        if added:
            await update.message.reply_text(msg, parse_mode="HTML")
        return

    # Detect playlist
    playlist_mode = is_playlist_url(url) or settings["mode"] == "playlist"

    if playlist_mode:
        await handle_playlist_message(update, context, url, settings)
    else:
        await handle_single_with_preview(update, context, url, settings)


async def handle_single_with_preview(update, context, url, settings):
    """Show a preview of the video and wait for user confirmation."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    # Extract video info for preview
    try:
        video_info = get_video_info(url)
    except Exception:
        video_info = None

    # Store pending download in user state
    USER_STATE[user_id] = {
        "pending": {
            "url": url,
            "settings": settings.copy(),
            "video_info": video_info,
            "chat_id": chat_id,
        }
    }

    # Show preview
    await show_preview(context, chat_id, video_info, settings)


# ─── Playlist handler ────────────────────────────────────────────────────────

async def handle_playlist_message(update, context, url, settings):
    """Handle playlist download with progress tracking and cancel support."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    mode_emoji = "📺"

    # Send initial status message
    status_msg = await update.message.reply_text(
        f"{mode_emoji} <b>Playlist Detected!</b>\n"
        f"⏳ Extracting playlist info...",
        parse_mode="HTML",
    )

    # Create task for cancellation
    task_id = str(uuid.uuid4())[:12]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmpdir = str(TEMP_BASE_DIR / timestamp)
    os.makedirs(tmpdir, exist_ok=True)

    DOWNLOAD_TASKS[task_id] = {
        "user_id": user_id,
        "process": None,
        "status_msg": status_msg,
        "temp_dir": tmpdir,
        "output_dir": tmpdir,
        "settings": settings.copy(),
        "cancelled": False,
    }
    IS_DOWNLOADING[user_id] = task_id

    cancel_keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ Cancel", callback_data=f"download:cancel:{task_id}")
    ]])

    try:
        # Update status
        await status_msg.edit_text(
            f"{mode_emoji} <b>Downloading Playlist...</b>\n"
            f"⏳ This may take a while for large playlists...\n"
            f"📥 Downloading all videos...",
            parse_mode="HTML",
            reply_markup=cancel_keyboard,
        )

        # Progress callback
        async def playlist_progress(parsed):
            current_msg = DOWNLOAD_TASKS[task_id].get("status_msg")
            if not current_msg:
                return

            try:
                if parsed.get("source") == "ytdlp" and parsed.get("percent") is not None:
                    percent = parsed["percent"]
                    bar = format_progress_bar(percent)
                    eta = parsed.get("eta", "")

                    text = (
                        f"{mode_emoji} <b>Downloading Playlist...</b>\n\n"
                        f"{bar} <code>{percent:.1f}%</code>\n"
                    )
                    if eta:
                        text += f"⏱️ ETA: {eta}"
                    text += "\n\n🛑 Use button below to cancel"

                    await current_msg.edit_text(text, parse_mode="HTML", reply_markup=cancel_keyboard)

                elif parsed.get("source") == "ffmpeg":
                    elapsed = parsed.get("time", "")
                    text = f"{mode_emoji} <b>Processing...</b>\n"
                    if elapsed:
                        text += f"⏱️ {elapsed}"
                    text += "\n\n🛑 Use button below to cancel"

                    await current_msg.edit_text(text, parse_mode="HTML", reply_markup=cancel_keyboard)

            except (telegram.error.BadRequest):
                DOWNLOAD_TASKS[task_id]["status_msg"] = None

        # Run playlist downloader
        output = await run_playlist_downloader(
            url, settings, tmpdir, task_id=task_id, progress_callback=playlist_progress,
        )

        # Check for cancellation
        if DOWNLOAD_TASKS[task_id]["cancelled"]:
            raise DownloadCancelledError()

        # Parse output for success/fail count
        output_lines = output.split("\n")
        total_downloaded = sum(1 for line in output_lines if "[SUCCESS]" in line)
        total_failed = sum(1 for line in output_lines if "[FAILED]" in line)

        # Find all downloaded files
        files = find_playlist_output_files(tmpdir, settings)

        if not files:
            raise RuntimeError("Playlist download completed but no output files found.")

        # Send files
        current_msg = DOWNLOAD_TASKS[task_id].get("status_msg")
        if current_msg:
            try:
                await current_msg.edit_text(
                    f"{mode_emoji} <b>Playlist Downloaded!</b>\n"
                    f"✅ {total_downloaded} videos downloaded\n"
                    f"❌ {total_failed} videos failed\n\n"
                    f"📤 Uploading {len(files)} files to Telegram...",
                    parse_mode="HTML",
                    reply_markup=cancel_keyboard,
                )
            except (telegram.error.BadRequest):
                pass

        sent_count = 0
        skipped_count = 0

        for idx, file_path in enumerate(files):
            # Check for cancellation during file sending
            if DOWNLOAD_TASKS[task_id]["cancelled"]:
                raise DownloadCancelledError()

            file_size = os.path.getsize(file_path)
            file_name = Path(file_path).name

            if file_size > 2 * 1024 * 1024 * 1024:
                skipped_count += 1
                continue

            try:
                with open(file_path, "rb") as f:
                    if file_path.lower().endswith((".m4a", ".mp3", ".flac", ".aac", ".ogg")):
                        await context.bot.send_audio(
                            chat_id=chat_id,
                            audio=f,
                            filename=file_name,
                            caption=f"🎵 {file_name} ({idx + 1}/{len(files)})",
                        )
                    else:
                        await context.bot.send_video(
                            chat_id=chat_id,
                            video=f,
                            filename=file_name,
                            supports_streaming=True,
                            caption=f"🎬 {file_name} ({idx + 1}/{len(files)})",
                        )
                sent_count += 1
            except Exception:
                skipped_count += 1

        # Done
        current_msg = DOWNLOAD_TASKS[task_id].get("status_msg")
        if current_msg:
            try:
                await current_msg.edit_text(
                    f"✅ <b>Playlist Complete!</b>\n\n"
                    f"📁 Downloaded: {total_downloaded} videos\n"
                    f"❌ Failed: {total_failed} videos\n"
                    f"📤 Sent: {sent_count} files\n"
                    f"⏭️ Skipped: {skipped_count} files\n\n"
                    f"Send another link to download more.",
                    parse_mode="HTML",
                )
            except (telegram.error.BadRequest):
                pass

    except DownloadCancelledError:
        current_msg = DOWNLOAD_TASKS.get(task_id, {}).get("status_msg")
        if current_msg:
            try:
                await current_msg.edit_text("❌ Download cancelled.", parse_mode="HTML")
            except (telegram.error.BadRequest):
                pass

    except RuntimeError as e:
        error_msg = str(e)
        if len(error_msg) > 500:
            error_msg = error_msg[:497] + "..."

        current_msg = DOWNLOAD_TASKS.get(task_id, {}).get("status_msg")
        if current_msg:
            try:
                await current_msg.edit_text(
                    f"❌ <b>Error:</b>\n<code>{error_msg}</code>",
                    parse_mode="HTML",
                )
            except (telegram.error.BadRequest):
                pass

    except Exception as e:
        error_msg = str(e)[:500]

        current_msg = DOWNLOAD_TASKS.get(task_id, {}).get("status_msg")
        if current_msg:
            try:
                await current_msg.edit_text(
                    f"❌ <b>Unexpected error:</b>\n<code>{error_msg}</code>",
                    parse_mode="HTML",
                )
            except (telegram.error.BadRequest):
                pass

    finally:
        # Cleanup
        if tmpdir and os.path.exists(tmpdir):
            shutil.rmtree(tmpdir, ignore_errors=True)

        if DOWNLOAD_TASKS.get(task_id):
            del DOWNLOAD_TASKS[task_id]

        if IS_DOWNLOADING.get(user_id) == task_id:
            del IS_DOWNLOADING[user_id]

    # Drain queue after playlist completes
    await drain_queue(user_id, context)


# ─── Callback handler ────────────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button callbacks."""
    query = update.callback_query
    await query.answer()

    data = query.data
    user_id = query.from_user.id

    # --- Mode selection ---
    if data.startswith("mode:"):
        mode = data.split(":")[1]
        settings = get_user_settings(user_id)
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

    # --- Settings ---
    elif data == "settings":
        settings = get_user_settings(user_id)
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

    # --- Preview: Confirm download ---
    elif data == "preview:confirm":
        pending = USER_STATE.get(user_id, {}).get("pending")
        if not pending:
            try:
                await query.edit_message_text(
                    "⚡ No pending download. Send a YouTube link to start."
                )
            except telegram.error.BadRequest:
                pass
            return

        url = pending["url"]
        settings = pending["settings"]
        video_info = pending.get("video_info")
        chat_id = pending.get("chat_id", query.from_user.id)

        # Clear pending state
        USER_STATE[user_id]["pending"] = None

        try:
            await download_and_send(
                context, user_id, chat_id, url, settings, video_info=video_info,
            )
        except DownloadCancelledError:
            pass  # Handled inside download_and_send
        except Exception:
            pass  # Handled inside download_and_send

        # Drain queue after download completes
        await drain_queue(user_id, context)

    # --- Preview: Cancel ---
    elif data == "preview:cancel":
        pending = USER_STATE.get(user_id, {}).get("pending")

        if pending:
            USER_STATE[user_id]["pending"] = None

        try:
            await query.edit_message_text("❌ Cancelled. Send a YouTube link to start downloading.")
        except telegram.error.BadRequest:
            pass

        # Drain queue if there are pending items
        await drain_queue(user_id, context)

    # --- Download: Cancel ---
    elif data.startswith("download:cancel:"):
        task_id = data.split(":")[2]

        task = DOWNLOAD_TASKS.get(task_id)
        if not task:
            await query.answer("Download already finished.")
            return

        if task["cancelled"]:
            await query.answer("Cancellation in progress...")
            return

        # Set cancelled flag - download_and_send will handle cleanup
        task["cancelled"] = True
        await query.answer("Cancelling download...")

    # --- Queue: Clear ---
    elif data == "queue:clear":
        count = clear_queue(user_id)
        try:
            await query.edit_message_text(
                f"🗑️ Queue cleared ({count} item{'s' if count != 1 else ''} removed)."
            )
        except telegram.error.BadRequest:
            pass