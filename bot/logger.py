"""
Logging setup: main bot log + per-user logs.
"""

import logging
from bot.config import LOG_DIR


def setup_logging():
    """Setup file-based logging: main bot log + per-user logs."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # --- Main bot logger (system events, errors, downloads) ---
    main_log = LOG_DIR / "bot.log"
    main_handler = logging.FileHandler(main_log, encoding="utf-8")
    main_handler.setLevel(logging.INFO)
    main_handler.setFormatter(logging.Formatter(
        "[%(asctime)s] %(levelname)-5s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    # --- Console handler ---
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(
        "[%(asctime)s] %(message)s",
        datefmt="%H:%M:%S",
    ))

    # --- Root logger ---
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(main_handler)
    root_logger.addHandler(console_handler)

    return root_logger


# ─── Per-user logger ────────────────────────────────────────────────────────

def get_user_logger(user_id) -> logging.Logger:
    """Get (or create) a logger that writes to a per-user log file."""
    logger_name = f"user_{user_id}"
    logger = logging.getLogger(logger_name)

    if not logger.handlers:
        user_log = LOG_DIR / f"user_{user_id}.log"
        handler = logging.FileHandler(user_log, encoding="utf-8")
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter(
            "[%(asctime)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        # Prevent propagation to root logger (avoid duplicate entries in bot.log)
        logger.propagate = False

    return logger


def log_user_activity(user_id, action, request_text="", response_text=""):
    """Log a user's request and the bot's response."""
    logger = get_user_logger(user_id)
    entry = f"ACTION: {action}"
    if request_text:
        req = request_text[:200]
        entry += f" | REQUEST: {req}"
    if response_text:
        resp = response_text[:300]
        entry += f" | RESPONSE: {resp}"
    logger.info(entry)

    # Also log to main bot log
    logging.info(f"[User @{user_id}] {action}: {request_text[:100]}")