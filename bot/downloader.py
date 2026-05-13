"""
Downloader functions: run yt-dlp via main.py, track progress, handle cancellation.
"""

import asyncio
import os
import shutil
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import telegram.error

from bot.config import (
    DOWNLOAD_TASKS,
    DOWNLOADER_SCRIPT,
    IS_DOWNLOADING,
    TEMP_BASE_DIR,
    USER_STATE,
)
from bot.logger import log_user_activity
from bot.metadata import embed_audio_metadata, get_video_info
from bot.queue import get_queue, clear_queue
from bot.utils import (
    find_output_file,
    find_playlist_output_files,
    format_file_size,
    format_progress_bar,
    parse_progress_line,
)


# ─── Custom Exception ────────────────────────────────────────────────────────

class DownloadCancelledError(Exception):
    """Raised when a download is cancelled by the user."""
    pass


# ─── Sub-process runners ────────────────────────────────────────────────────

async def run_downloader(url, settings, output_dir, task_id=None, progress_callback=None):
    """Run main.py to download and convert the YouTube video.

    Streams stdout and calls progress_callback(parsed_dict) for progress lines.
    Checks DOWNLOAD_TASKS[task_id]["cancelled"] for cancellation.
    """
    cmd = [
        sys.executable, str(DOWNLOADER_SCRIPT),
        url,
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
        stderr=asyncio.subprocess.STDOUT,
    )

    # Store process reference for cancellation
    if task_id and task_id in DOWNLOAD_TASKS:
        DOWNLOAD_TASKS[task_id]["process"] = process

    collected = []
    last_update = 0
    last_percent = None

    while True:
        line = await process.stdout.readline()
        if not line:
            break

        # Check for cancellation
        if task_id and DOWNLOAD_TASKS.get(task_id, {}).get("cancelled"):
            if process.returncode is None:
                try:
                    process.kill()
                except Exception:
                    pass
            raise DownloadCancelledError()

        text = line.decode(errors="ignore").rstrip()
        collected.append(text)

        parsed = parse_progress_line(text)
        if parsed and progress_callback:
            now = time.time()
            percent = parsed.get("percent")
            if percent is not None:
                if last_percent is None or abs(percent - (last_percent or 0)) >= 1 or (now - last_update) >= 2:
                    try:
                        await progress_callback(parsed)
                    except Exception:
                        pass
                    last_update = now
                    last_percent = percent
            else:
                if now - last_update >= 3:
                    try:
                        await progress_callback(parsed)
                    except Exception:
                        pass
                    last_update = now

    await process.wait()

    # Check for cancellation after process exits
    if task_id and (DOWNLOAD_TASKS.get(task_id, {}).get("cancelled") or process.returncode < 0):
        raise DownloadCancelledError()

    output_text = "\n".join(collected)
    if process.returncode != 0:
        raise RuntimeError(f"Download failed:\n{output_text[-2000:]}")
    return output_text


async def run_playlist_downloader(url, settings, output_dir, task_id=None, progress_callback=None):
    """Run main.py to download an entire YouTube playlist."""
    cmd = [
        sys.executable, str(DOWNLOADER_SCRIPT),
        url,
        "--playlist",
        "--method", "ytdlp",
        "--output", output_dir,
    ]

    submode = settings.get("_playlist_submode", "video")
    if submode == "audio":
        cmd.extend(["--audio-only", "--audio-format", settings["audio_format"],
                     "--audio-bitrate", settings["audio_bitrate"]])
    else:
        cmd.extend(["--format", settings["video_format"],
                     "--video-quality", settings["video_quality"],
                     "--audio-bitrate", settings["audio_bitrate"]])

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    if task_id and task_id in DOWNLOAD_TASKS:
        DOWNLOAD_TASKS[task_id]["process"] = process

    collected = []
    last_update = 0

    while True:
        line = await process.stdout.readline()
        if not line:
            break

        # Check for cancellation
        if task_id and DOWNLOAD_TASKS.get(task_id, {}).get("cancelled"):
            if process.returncode is None:
                try:
                    process.kill()
                except Exception:
                    pass
            raise DownloadCancelledError()

        text = line.decode(errors="ignore").rstrip()
        collected.append(text)

        parsed = parse_progress_line(text)
        if parsed and progress_callback:
            now = time.time()
            if parsed.get("percent") is not None:
                if now - last_update >= 2:
                    try:
                        await progress_callback(parsed)
                    except Exception:
                        pass
                    last_update = now
            else:
                if now - last_update >= 3:
                    try:
                        await progress_callback(parsed)
                    except Exception:
                        pass
                    last_update = now

    await process.wait()

    # Check for cancellation after process exits
    if task_id and (DOWNLOAD_TASKS.get(task_id, {}).get("cancelled") or process.returncode < 0):
        raise DownloadCancelledError()

    output_text = "\n".join(collected)
    if process.returncode != 0:
        raise RuntimeError(f"Playlist download failed:\n{output_text[-2000:]}")
    return output_text


# ─── Preview ─────────────────────────────────────────────────────────────────

async def show_preview(context, chat_id, video_info, settings):
    """Show a preview of the video before downloading."""
    from bot.utils import format_duration, format_view_count

    title = (video_info.get("title", "Unknown") if video_info else "Unknown")
    uploader = video_info.get("uploader", "") if video_info else ""
    duration = video_info.get("duration", 0) if video_info else 0
    thumbnail = video_info.get("thumbnail", "") if video_info else ""
    view_count = video_info.get("view_count", 0) if video_info else 0

    dur_str = format_duration(duration) if duration else ""

    # Estimate file size
    mode = settings["mode"]
    if mode == "audio":
        bitrate_kbps = int(settings["audio_bitrate"].replace("k", ""))
        estimated_mb = duration * bitrate_kbps * 1000 / 8 / 1024 / 1024 if duration else 0
    else:
        quality = settings["video_quality"]
        mb_per_min = {"360p": 3, "480p": 5, "720p": 8, "1080p": 12, "1440p": 20, "2160p": 40}
        estimated_mb = (duration / 60) * mb_per_min.get(quality, 10) if duration else 0

    # Build caption
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    caption_parts = [f"📎 <b>Preview</b>"]
    if title and title != "Unknown":
        display_title = title[:60] + "..." if len(title) > 60 else title
        caption_parts.append(f"\n🎬 <b>{display_title}</b>")
    if uploader:
        caption_parts.append(f"👤 {uploader}")
    if dur_str:
        caption_parts.append(f"⏱️ {dur_str}")
    if view_count:
        caption_parts.append(f"👁️ {format_view_count(view_count)}")

    fmt_label = settings["audio_format"].upper() if mode == "audio" else settings["video_format"].upper()
    caption_parts.append(f"📁 {fmt_label} ≈ {estimated_mb:.1f} MB")
    caption_parts.append("\n\nTap ✅ to start downloading")

    caption = "\n".join(caption_parts)

    # Create buttons
    keyboard = [[
        InlineKeyboardButton("✅ Download", callback_data="preview:confirm"),
        InlineKeyboardButton("❌ Cancel", callback_data="preview:cancel"),
    ]]

    # Send with thumbnail if available
    if thumbnail:
        try:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=thumbnail,
                caption=caption,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return
        except telegram.error.BadRequest:
            pass  # Thumbnail URL invalid, fall back to text

    # Fallback: text-only preview
    await context.bot.send_message(
        chat_id=chat_id,
        text=caption,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ─── Download and send ───────────────────────────────────────────────────────

async def download_and_send(context, user_id, chat_id, url, settings,
                            video_info=None, status_msg=None, silent=False):
    """Download a YouTube video/audio and send it to the user via Telegram."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    task_id = str(uuid.uuid4())[:12]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmpdir = str(TEMP_BASE_DIR / timestamp)
    os.makedirs(tmpdir, exist_ok=True)

    mode = settings["mode"]
    mode_emoji = "🎬" if mode == "video" else "🎵"

    # Get title for display
    title = (video_info.get("title", "") if video_info else "")
    display_title = (title[:50] + "..." if len(title) > 50 else title) if title else "File"

    # Log download start
    print(f"\n{'─' * 40}")
    print(f"[{task_id}] 📥 Download started: {display_title}")
    print(f"[{task_id}] Mode: {mode} | User: @{user_id}")
    print(f"{'─' * 40}")

    # Register task for cancellation tracking
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

    # Create cancel button keyboard
    cancel_keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ Cancel", callback_data=f"download:cancel:{task_id}")
    ]])

    try:
        # Phase 1: Start download
        start_text = (
            f"{mode_emoji} <b>Downloading</b>\n\n"
            f"📁 {display_title}\n"
            f"⏳ Extracting video info..."
        )

        if not status_msg:
            status_msg = await context.bot.send_message(
                chat_id=chat_id,
                text=start_text,
                parse_mode="HTML",
                reply_markup=cancel_keyboard,
            )
        else:
            try:
                await status_msg.edit_text(
                    start_text,
                    parse_mode="HTML",
                    reply_markup=cancel_keyboard,
                )
            except telegram.error.BadRequest:
                status_msg = await context.bot.send_message(
                    chat_id=chat_id,
                    text=start_text,
                    parse_mode="HTML",
                    reply_markup=cancel_keyboard,
                )

        DOWNLOAD_TASKS[task_id]["status_msg"] = status_msg

        # Log Phase 1
        print(f"[{task_id}] ⏳ Phase 1: Extracting video info...")

        # Phase 2: Download with progress tracking
        async def progress_callback(parsed):
            """Update the progress message with download status."""
            if DOWNLOAD_TASKS.get(task_id, {}).get("cancelled"):
                return

            current_msg = DOWNLOAD_TASKS[task_id].get("status_msg")
            if not current_msg:
                return

            try:
                phase = parsed.get("phase", "unknown")

                if parsed.get("source") == "ytdlp" and parsed.get("percent") is not None:
                    percent = parsed["percent"]
                    bar = format_progress_bar(percent)
                    speed = parsed.get("speed", "")
                    eta = parsed.get("eta", "")

                    text = (
                        f"{mode_emoji} <b>Downloading</b>\n\n"
                        f"{bar} <code>{percent:.1f}%</code>\n"
                    )
                    if speed:
                        text += f"📊 {speed}"
                    if eta:
                        text += f"  ⏱️ ETA: {eta}"
                    text += f"\n\n📁 {display_title}"

                    await current_msg.edit_text(text, parse_mode="HTML", reply_markup=cancel_keyboard)

                elif phase == "info":
                    text = f"{mode_emoji} <b>Fetching video info...</b>\n\n📁 {display_title}"
                    await current_msg.edit_text(text, parse_mode="HTML", reply_markup=cancel_keyboard)

                elif phase == "merge":
                    text = f"{mode_emoji} <b>Merging streams...</b>\n\n🔗 Combining video + audio\n\n📁 {display_title}"
                    await current_msg.edit_text(text, parse_mode="HTML", reply_markup=cancel_keyboard)

                elif phase == "postprocess":
                    text = f"{mode_emoji} <b>Post-processing...</b>\n\n🔧 Converting & optimizing\n\n📁 {display_title}"
                    await current_msg.edit_text(text, parse_mode="HTML", reply_markup=cancel_keyboard)

                elif parsed.get("source") == "ffmpeg":
                    elapsed = parsed.get("time", "")
                    speed = parsed.get("speed", "")

                    text = f"{mode_emoji} <b>Processing</b>\n\n"
                    if elapsed:
                        text += f"⏱️ {elapsed}"
                    if speed:
                        text += f"  ⚡ {speed}x"
                    text += f"\n\n📁 {display_title}"

                    await current_msg.edit_text(text, parse_mode="HTML", reply_markup=cancel_keyboard)

            except telegram.error.BadRequest:
                DOWNLOAD_TASKS[task_id]["status_msg"] = None

        # Run downloader
        print(f"[{task_id}] ⬇️  Phase 2: Downloading...")
        output = await run_downloader(url, settings, tmpdir, task_id=task_id, progress_callback=progress_callback)

        # Check for cancellation after download
        if DOWNLOAD_TASKS[task_id]["cancelled"]:
            raise DownloadCancelledError()

        # Phase 3: Find output file
        print(f"[{task_id}] 🔍 Phase 3: Finding output file...")
        current_msg = DOWNLOAD_TASKS[task_id].get("status_msg")
        if current_msg:
            try:
                await current_msg.edit_text(
                    f"{mode_emoji} <b>Finding output file...</b>",
                    parse_mode="HTML",
                    reply_markup=cancel_keyboard,
                )
            except (telegram.error.BadRequest):
                pass

        file_path = find_output_file(tmpdir, settings)
        if not file_path:
            raise RuntimeError("Download completed but output file not found.")

        file_size = os.path.getsize(file_path)
        file_size_mb = file_size / (1024 * 1024)
        file_name = Path(file_path).name
        print(f"[{task_id}] ✅ Found: {file_name} ({file_size_mb:.1f} MB)")

        # Phase 4: Embed metadata (for audio files)
        if mode == "audio" and video_info:
            print(f"[{task_id}] 🏷️  Phase 4: Embedding metadata...")
            current_msg = DOWNLOAD_TASKS[task_id].get("status_msg")
            if current_msg:
                try:
                    await current_msg.edit_text(
                        f"🏷️ <b>Embedding metadata...</b>\n📁 {file_name}",
                        parse_mode="HTML",
                        reply_markup=cancel_keyboard,
                    )
                except (telegram.error.BadRequest):
                    pass

            embed_audio_metadata(file_path, video_info, settings["audio_format"])

        # Phase 5: Upload to Telegram
        print(f"[{task_id}] 📤 Phase 5: Uploading to Telegram...")
        current_msg = DOWNLOAD_TASKS[task_id].get("status_msg")
        if current_msg:
            try:
                await current_msg.edit_text(
                    f"📤 <b>Uploading to Telegram...</b>\n"
                    f"📁 {file_name} ({format_file_size(file_size)})",
                    parse_mode="HTML",
                    reply_markup=cancel_keyboard,
                )
            except (telegram.error.BadRequest):
                pass

        # Send file
        with open(file_path, "rb") as f:
            if mode == "audio":
                send_title = video_info.get("title", "") if video_info else ""
                send_performer = video_info.get("uploader", "") if video_info else ""
                await context.bot.send_audio(
                    chat_id=chat_id,
                    audio=f,
                    filename=file_name,
                    title=send_title,
                    performer=send_performer,
                    timeout=600,
                )
            else:
                await context.bot.send_video(
                    chat_id=chat_id,
                    video=f,
                    filename=file_name,
                    supports_streaming=True,
                    timeout=600,
                )

        # Phase 6: Done
        print(f"[{task_id}] ✅ Done! Sent {file_name} ({file_size_mb:.1f} MB)")
        print(f"{'─' * 40}\n")

        if not silent and current_msg:
            try:
                await current_msg.edit_text(
                    f"✅ <b>Done!</b>\n\n"
                    f"📁 {file_name} ({format_file_size(file_size)})\n\n"
                    f"Send another link to download more.",
                    parse_mode="HTML",
                )
            except (telegram.error.BadRequest):
                pass

    except DownloadCancelledError:
        current_msg = DOWNLOAD_TASKS.get(task_id, {}).get("status_msg")
        if current_msg:
            try:
                await current_msg.edit_text(
                    "❌ Download cancelled.",
                    parse_mode="HTML",
                )
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
        # Cleanup temp directory
        if tmpdir and os.path.exists(tmpdir):
            shutil.rmtree(tmpdir, ignore_errors=True)

        # Remove task tracking
        if DOWNLOAD_TASKS.get(task_id):
            del DOWNLOAD_TASKS[task_id]

        # Clear downloading flag
        if IS_DOWNLOADING.get(user_id) == task_id:
            del IS_DOWNLOADING[user_id]


# ─── Queue drain ─────────────────────────────────────────────────────────────

async def drain_queue(user_id, context):
    """Process all items in the user's download queue."""
    queue = get_queue(user_id)
    if not queue:
        return

    chat_id = queue[0].get("chat_id")
    processed = 0
    failed = 0

    while queue:
        item = queue.popleft()
        url = item["url"]
        settings = item["settings"]

        try:
            # Extract video info for metadata embedding
            try:
                video_info = get_video_info(url)
            except Exception:
                video_info = None

            # Send notification for queue item
            notify_msg = await context.bot.send_message(
                chat_id=chat_id,
                text=f"📋 Processing queued item...",
            )

            # Download and send (silent mode)
            await download_and_send(
                context, user_id, chat_id, url, settings,
                video_info=video_info, status_msg=notify_msg, silent=True,
            )
            processed += 1

        except DownloadCancelledError:
            failed += 1
        except Exception as e:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"❌ Failed: <code>{url[:50]}</code>\n<code>{str(e)[:200]}</code>",
                    parse_mode="HTML",
                )
            except Exception:
                pass
            failed += 1
        finally:
            # Ensure IS_DOWNLOADING is cleared
            if user_id in IS_DOWNLOADING:
                del IS_DOWNLOADING[user_id]
            await asyncio.sleep(1)  # Rate limit delay between items

    # Notify completion
    if processed > 0:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"✅ <b>Queue complete!</b>\n"
                     f"📁 {processed} downloaded"
                     f"{', ❌ ' + str(failed) + ' failed' if failed else ''}.",
                parse_mode="HTML",
            )
        except Exception:
            pass