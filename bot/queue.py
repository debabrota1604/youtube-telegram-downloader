"""
Download queue management.
"""

from collections import deque

from bot.config import DOWNLOAD_QUEUE, MAX_QUEUE_SIZE


def get_queue(user_id) -> deque:
    """Get or create the download queue for a user."""
    if user_id not in DOWNLOAD_QUEUE:
        DOWNLOAD_QUEUE[user_id] = deque()
    return DOWNLOAD_QUEUE[user_id]


def add_to_queue(user_id, url: str, settings: dict, chat_id) -> tuple[bool, str]:
    """Add a URL to the user's download queue.

    Returns (success: bool, message: str).
    """
    queue = get_queue(user_id)
    if len(queue) >= MAX_QUEUE_SIZE:
        return False, (
            f"❌ Queue is full (max {MAX_QUEUE_SIZE} items).\n"
            f"Use <code>/queue-clear</code> to free space."
        )

    queue.append({
        "url": url,
        "settings": settings.copy(),
        "chat_id": chat_id,
    })

    return True, f"⏳ Download in progress. Added to queue (position <code>{len(queue)}</code>)."


def clear_queue(user_id) -> int:
    """Clear the user's download queue. Returns the number of items removed."""
    queue = DOWNLOAD_QUEUE.get(user_id, deque())
    count = len(queue)
    DOWNLOAD_QUEUE[user_id] = deque()
    return count