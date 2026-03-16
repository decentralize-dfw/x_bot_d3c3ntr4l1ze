"""
utils/text.py
-------------
Tweet text formatting helpers.
"""


def trim_for_format(text: str, limit: int = 135) -> str:
    """Word-boundary trim before format_tweet() — keeps total tweet ≤ 140 chars."""
    if len(text) <= limit:
        return text
    trimmed = text[:limit]
    last_space = trimmed.rfind(" ")
    return trimmed[:last_space] if last_space > (limit * 3 // 4) else trimmed


def format_tweet(text: str) -> str:
    """Strip surrounding quotes and add ꩜ brand suffix."""
    text = text.strip().strip("\"'")
    return f"{text} ꩜"
