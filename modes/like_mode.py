"""
modes/like_mode.py
------------------
Like modu — YENİ. Premium API ile aktif. (Faz 3.3)

İki ayrı like stratejisi:
  post_like_tweets()      — target scan tweet'lerine like (niche karşılıklılık)
  like_following_tweets() — takip edilenlerin tüm son tweetlerini like (hesap olarak var olma)

Günlük hedef: 3 niche like (d3c3ntr4l1z3_strategy.docx §05) +
              following_archive'deki tüm tweetler.
"""
import json
import os
import time

import tweet_archive
from core.twitter import get_twitter_clients
from modes.viral_mix import fetch_target_tweets_with_ids
from utils.logger import get_logger
from utils.spam_filter import is_spam

logger = get_logger(__name__)

_LIKE_TARGET = 3
_LIKE_COOLDOWN_DAYS = 7  # Aynı tweet'i 7 gün içinde tekrar like etme

_FOLLOWING_ARCHIVE = os.path.join(os.path.dirname(__file__), "..", "following_archive.json")


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

        if is_spam(c["text"]):
            logger.info(f"@{c['author']}: spam/shill filtered, skipping like.")
            continue

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


_FOLLOWING_DAILY_CAP = 35  # Günde en fazla bu kadar following like atılır


def like_following_tweets(max_likes: int = _FOLLOWING_DAILY_CAP):
    """Takip edilen hesapların son 24h tweetlerini like at (retweet hariç).

    following_archive.json'u okur. Bu dosya following_scan modu tarafından
    günlük olarak güncellenir. Amaç: takip edilen herkesin timeline'ında
    hesap olarak görünür ve düzenli etkileşimde olmak.

    max_likes: günlük üst sınır (varsayılan 35).
    """
    client, _ = get_twitter_clients()

    try:
        with open(_FOLLOWING_ARCHIVE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        logger.warning("following_archive.json bulunamadı. Önce 'following_scan' modunu çalıştır.")
        return
    except json.JSONDecodeError as e:
        logger.error(f"following_archive.json parse error: {e}")
        return

    tweets = data.get("tweets", [])
    if not tweets:
        logger.warning("following_archive.json boş, like atılacak tweet yok.")
        return

    logger.info(f"like_following: {len(tweets)} tweet okundu, like döngüsü başlıyor...")

    liked = 0
    skipped = 0
    errors = 0

    for t in tweets:
        if liked >= max_likes:
            logger.info(f"like_following: daily cap {max_likes} reached, stopping.")
            break

        if is_spam(t["text"]):
            logger.info(f"@{t['author']}: spam/shill filtered, skipping like.")
            skipped += 1
            continue

        archive_id = "fllike_" + t["tweet_id"]
        if tweet_archive.is_posted_recently(archive_id, days=_LIKE_COOLDOWN_DAYS):
            skipped += 1
            continue

        try:
            client.like(t["tweet_id"])
            tweet_archive.record_post(
                archive_id, content_type="following_like",
                tweet_text=t["text"][:100], tweet_id=t["tweet_id"],
            )
            logger.info(f"Liked @{t['author']}: {t['text'][:60]}...")
            liked += 1
        except Exception as e:
            logger.warning(f"Like failed @{t['author']} ({t['tweet_id']}): {e}")
            errors += 1

        time.sleep(0.5)  # API rate limit

    logger.info(
        f"like_following done: {liked} liked, {skipped} already liked, {errors} errors."
    )
