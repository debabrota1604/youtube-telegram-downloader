"""
Bot entry point: dependency check, application build, handler registration, polling.
"""

import sys

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from bot.config import BOT_TOKEN, ALLOWED_USERS, DOWNLOADER_SCRIPT, LOG_DIR
from bot.logger import setup_logging, logging
from bot.commands import COMMAND_HANDLERS
from bot.handlers import handle_message, handle_callback


# ─── Dependencies check ─────────────────────────────────────────────────────

def check_dependencies():
    """Check that all required packages are installed."""
    import subprocess

    missing = []

    try:
        import telegram  # noqa: F401
    except ImportError:
        missing.append("python-telegram-bot")

    try:
        import dotenv  # noqa: F401
    except ImportError:
        missing.append("python-dotenv")

    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        missing.append("yt-dlp")

    # Check ffmpeg
    result = subprocess.run(["which", "ffmpeg"], capture_output=True)
    if result.returncode != 0:
        missing.append("ffmpeg (install: brew install ffmpeg)")

    # Check downloader script exists
    if not DOWNLOADER_SCRIPT.exists():
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

    # Check mutagen (optional, for metadata embedding)
    try:
        import mutagen  # noqa: F401
        print("[INFO] mutagen available - metadata embedding enabled")
    except ImportError:
        print("[WARN] mutagen not installed - metadata embedding disabled")
        print("  Install with: pip install mutagen")


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    """Bot entry point."""
    # Setup file-based logging
    setup_logging()
    logging.info("=" * 50)
    logging.info("YouTube Telegram Bot starting...")
    logging.info("=" * 50)

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
    print(f"[BOT] Logs: {LOG_DIR}/")
    print("\n[BOT] Starting bot... Press Ctrl+C to stop.\n")

    logging.info(f"Bot token configured, restricted={bool(ALLOWED_USERS)}")

    # Build and run bot
    app = Application.builder().token(BOT_TOKEN).build()

    # Register command handlers
    for command_name, handler_func in COMMAND_HANDLERS:
        app.add_handler(CommandHandler(command_name, handler_func))

    # Inline buttons
    app.add_handler(CallbackQueryHandler(handle_callback))

    # YouTube links (text messages, not commands)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Run bot
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()