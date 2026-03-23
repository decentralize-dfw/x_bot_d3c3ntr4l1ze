"""
utils/text.py
-------------
Tweet text formatting helpers.
"""
import re

# Tweet sonundaki dekoratif emoji ve eski brand sembolü (꩜) sil
_TRAILING_JUNK = re.compile(
    r'[\s\U0001F300-\U0001FFFF\U00002600-\U000027BF\u2300-\u23FF'
    r'\uFE0F\u200D꩜]+$'
)


def trim_for_format(text: str, limit: int = 260) -> str:
    """Word-boundary trim before format_tweet() — keeps total tweet ≤ 280 chars.
    format_tweet() appends ' 💿💿💿' (9 chars) → 260 + 9 = 269 ≤ 280.
    """
    if len(text) <= limit:
        return text
    trimmed = text[:limit]
    last_space = trimmed.rfind(" ")
    return trimmed[:last_space] if last_space > (limit * 3 // 4) else trimmed


def format_tweet(text: str) -> str:
    """Strip surrounding quotes + trailing emojis, add 💿💿💿 brand suffix."""
    text = text.strip().strip("\"'")
    text = _TRAILING_JUNK.sub('', text).strip()
    return f"{text} 💿💿💿"
