"""
tests/test_quality.py
---------------------
Quality gate + duplicate detection testleri.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import tweet_archive


def _reset_archive(tmp_path):
    """Test için temiz bir archive path."""
    import tweet_archive as ta
    ta.ARCHIVE_PATH = str(tmp_path / "tweet_archive.json")
    ta._ARCHIVE_CACHE = None


def test_is_too_similar_detects_duplicates(tmp_path):
    _reset_archive(tmp_path)
    tweet_archive.record_post("id_1", "test", tweet_text="virtual worlds need inhabitants to survive")
    assert tweet_archive.is_too_similar("virtual worlds need inhabitants to survive") is True


def test_is_too_similar_passes_unique(tmp_path):
    _reset_archive(tmp_path)
    tweet_archive.record_post("id_1", "test", tweet_text="virtual worlds need inhabitants")
    assert tweet_archive.is_too_similar("blockchain regulation in Australia confirmed") is False


def test_is_too_similar_empty_archive(tmp_path):
    _reset_archive(tmp_path)
    assert tweet_archive.is_too_similar("any tweet text here") is False


def test_theme_in_cooldown_blocks_same_theme(tmp_path):
    _reset_archive(tmp_path)
    tweet_archive.record_post("id_1", "test", tweet_text="webxr will replace native apps")
    assert tweet_archive.is_theme_in_cooldown("webxr browser adoption is accelerating") is True


def test_theme_in_cooldown_different_theme(tmp_path):
    _reset_archive(tmp_path)
    tweet_archive.record_post("id_1", "test", tweet_text="webxr will replace native apps")
    assert tweet_archive.is_theme_in_cooldown("blockchain ownership matters") is False
