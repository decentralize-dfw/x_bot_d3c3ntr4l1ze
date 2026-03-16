"""
tests/test_archive.py
---------------------
tweet_archive.py için unit testler: atomic write, cleanup, record_post.
"""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import tweet_archive


def _reset_archive(tmp_path):
    """Test için temiz bir archive path."""
    import tweet_archive as ta
    ta.ARCHIVE_PATH = str(tmp_path / "tweet_archive.json")
    ta._ARCHIVE_CACHE = None


def test_atomic_save_no_corruption(tmp_path):
    _reset_archive(tmp_path)
    entries = [
        {"content_id": "x1", "content_type": "test",
         "posted_at": "2025-01-01T10:00:00+00:00"}
    ]
    tweet_archive.save_archive(entries)
    # tmp file should not exist after atomic rename
    assert not os.path.exists(tweet_archive.ARCHIVE_PATH + ".tmp")
    # file should be valid JSON
    with open(tweet_archive.ARCHIVE_PATH, "r") as f:
        loaded = json.load(f)
    assert loaded == entries


def test_cleanup_removes_old_entries(tmp_path):
    _reset_archive(tmp_path)
    # Record a post with very old timestamp
    entries = [
        {"content_id": "old_1", "content_type": "test",
         "posted_at": "2020-01-01T00:00:00+00:00"},
        {"content_id": "new_1", "content_type": "test",
         "posted_at": tweet_archive._utcnow().isoformat()},
    ]
    tweet_archive.save_archive(entries)
    tweet_archive._ARCHIVE_CACHE = None  # force reload
    fresh = tweet_archive.cleanup_old_entries(days=60)
    assert len(fresh) == 1
    assert fresh[0]["content_id"] == "new_1"


def test_record_post_and_retrieve(tmp_path):
    _reset_archive(tmp_path)
    tweet_archive.record_post(
        "cid_test_42",
        content_type="morning",
        tweet_text="virtual worlds are not games, they are infrastructure",
        tweet_id="123456789",
    )
    entries = tweet_archive.load_archive()
    assert len(entries) == 1
    e = entries[0]
    assert e["content_id"] == "cid_test_42"
    assert e["content_type"] == "morning"
    assert e["tweet_id"] == "123456789"
    assert "posted_at" in e


def test_record_post_deduplicates(tmp_path):
    _reset_archive(tmp_path)
    tweet_archive.record_post("dup_id", content_type="morning", tweet_text="first version")
    tweet_archive.record_post("dup_id", content_type="morning", tweet_text="second version")
    entries = tweet_archive.load_archive()
    # Should only have one entry with the latest text
    assert len(entries) == 1
    assert entries[0]["tweet_text"] == "second version"


def test_is_posted_recently_true(tmp_path):
    _reset_archive(tmp_path)
    tweet_archive.record_post("recent_id", content_type="test")
    assert tweet_archive.is_posted_recently("recent_id", days=60) is True


def test_is_posted_recently_false_old(tmp_path):
    _reset_archive(tmp_path)
    entries = [
        {"content_id": "stale_id", "content_type": "test",
         "posted_at": "2020-01-01T00:00:00+00:00"},
    ]
    tweet_archive.save_archive(entries)
    tweet_archive._ARCHIVE_CACHE = None
    assert tweet_archive.is_posted_recently("stale_id", days=60) is False


def test_get_unscored_tweets(tmp_path):
    _reset_archive(tmp_path)
    tweet_archive.record_post(
        "scored_cid", content_type="morning", tweet_id="111"
    )
    # Add one with engagement_score set (simulate already scored)
    entries = tweet_archive.load_archive()
    entries[0]["engagement_score"] = 5
    tweet_archive.save_archive(entries)
    tweet_archive._ARCHIVE_CACHE = None
    tweet_archive.record_post("unscored_cid", content_type="evening", tweet_id="222")

    unscored = tweet_archive.get_unscored_tweets(days=7)
    ids = [e["tweet_id"] for e in unscored]
    assert "222" in ids
    assert "111" not in ids
