"""
modes/like_mode.py
------------------
Like modu — YENİ. Premium API ile aktif. (Faz 3.3)

Target hesapların tweet'lerine like at — karşılıklılık zinciri başlatır.
Günlük hedef: 3 like (d3c3ntr4l1z3_strategy.docx §05).
"""
import os

import tweet_archive
from core.twitter import get_twitter_clients
from modes.viral_mix import fetch_target_tweets_with_ids
from utils.logger import get_logger

logger = get_logger(__name__)

_LIKE_TARGET = 3
_LIKE_COOLDOWN_DAYS = 7  # Aynı tweet'i 7 gün içinde tekrar like etme


def post_like_tweets():
    """Target hesapların niche tweet'lerine like at.

    Karşılıklılık zinciri başlatır — mid-tier hesaplar geri like/follow atar.
    """
    client, _ = get_twitter_clients()

    logger.info("Fetching target tweets for like mode...")
    candidates = fetch_target_tweets_with_ids(n_targets=6)
    if not candidates:
        logger.warning("No candidates for like mode, skipping.")
        return

    liked = 0
    for c in candidates:
        if liked >= _LIKE_TARGET:
            break

        import hashlib
        archive_id = "like_" + hashlib.md5(c["text"].encode()).hexdigest()[:12]
        if tweet_archive.is_posted_recently(archive_id, days=_LIKE_COOLDOWN_DAYS):
            logger.info(f"@{c['author']}: tweet recently liked, skipping.")
            continue

        try:
            client.like(c["id"])
            tweet_archive.record_post(
                archive_id, content_type="like",
                tweet_text=c["text"][:100], tweet_id=c["id"],
            )
            logger.info(f"Liked tweet from @{c['author']}: {c['text'][:60]}...")
            liked += 1
        except Exception as e:
            logger.warning(f"Like tweet from @{c['author']} failed: {e}")

    logger.info(f"Like mode done: {liked}/{_LIKE_TARGET} likes sent.")
