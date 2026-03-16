"""
tests/test_voice.py
-------------------
core/voice.py ve utils/text.py için unit testler.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.voice import get_this_weeks_theme, WEEKLY_THEMES
from utils.text import format_tweet, trim_for_format


def test_get_this_weeks_theme_is_deterministic():
    """Aynı hafta içinde iki çağrı aynı temayı döndürmeli."""
    theme1 = get_this_weeks_theme()
    theme2 = get_this_weeks_theme()
    assert theme1 == theme2
    assert theme1 in WEEKLY_THEMES


def test_get_this_weeks_theme_is_valid_string():
    theme = get_this_weeks_theme()
    assert isinstance(theme, str)
    assert len(theme) > 10


def test_format_tweet_appends_suffix():
    result = format_tweet("virtual worlds need inhabitants")
    assert result.endswith(" ꩜")
    assert "virtual worlds need inhabitants" in result


def test_format_tweet_strips_quotes():
    result = format_tweet('"quoted tweet text"')
    assert not result.startswith('"')
    assert not result.startswith("'")
    assert result.endswith(" ꩜")


def test_trim_for_format_short_passthrough():
    text = "short text"
    assert trim_for_format(text, limit=135) == text


def test_trim_for_format_word_boundary():
    # Text longer than limit should trim at word boundary
    text = "a " * 70  # 140 chars of "a "
    result = trim_for_format(text, limit=135)
    assert len(result) <= 135
    # Should not end with half a word (trimmed at space)
    assert not result.endswith("a")  # "a " trimmed at space leaves trailing space or shorter


def test_trim_for_format_exact_limit():
    text = "x" * 135
    assert trim_for_format(text, limit=135) == text


def test_trim_for_format_over_limit_no_space():
    # If no space found in a sensible position, still trims
    text = "x" * 200
    result = trim_for_format(text, limit=135)
    assert len(result) <= 135
