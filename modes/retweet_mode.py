"""
modes/retweet_mode.py
---------------------
Retweet modu — Premium API ile aktif.

Target hesapların niche tweet'lerini retweet et.
Günlük hedef: 7-8 RT (d3c3ntr4l1z3_strategy.docx §05 — %30 etkileşim payı).
2x/gün çalışır → 3/run × 2 = ~6 RT/gün + Pazar kapalı = haftalık ~36 RT.
"""
import hashlib

import tweet_archive
from core.twitter import get_twitter_clients
from modes.viral_mix import fetch_target_tweets_with_ids
from utils.logger import get_logger

logger = get_logger(__name__)

_RT_TARGET = 3          # Her run'da maksimum RT sayısı
_RT_COOLDOWN_DAYS = 7   # Aynı tweet 7 gün içinde tekrar RT edilmez


def post_retweet():
    """Target hesapların yüksek kaliteli tweet'lerini retweet et.

    Mid-tier hesaplara (10k-50k follower) odaklanır.
    Aynı tweet 7 gün içinde tekrar RT edilmez.
    """
    client, _ = get_twitter_clients()

    try:
        me = client.get_me()
        my_id = me.data.id
    except Exception as e:
        logger.error(f"get_me() failed: {e}")
        return

    logger.info("Fetching target tweets for retweet mode...")
    candidates = fetch_target_tweets_with_ids(n_targets=8)
    if not candidates:
        logger.warning("No candidates for retweet mode, skipping.")
        return

    retweeted = 0
    for c in candidates:
        if retweeted >= _RT_TARGET:
            break

        archive_id = "rt_" + hashlib.md5(c["text"].encode()).hexdigest()[:12]
        if tweet_archive.is_posted_recently(archive_id, days=_RT_COOLDOWN_DAYS):
            logger.info(f"@{c['author']}: tweet recently retweeted, skipping.")
            continue

        try:
            client.retweet(my_id, c["id"])
            tweet_archive.record_post(
                archive_id, content_type="retweet",
                tweet_text=c["text"][:100], tweet_id=c["id"],
            )
            logger.info(f"Retweeted @{c['author']}: {c['text'][:60]}...")
            retweeted += 1
        except Exception as e:
            logger.warning(f"Retweet @{c['author']} failed: {e}")

    logger.info(f"Retweet mode done: {retweeted}/{_RT_TARGET} RTs sent.")
