"""
following_scan.py
-----------------
Takip edilen hesapların son 24 saatlik orijinal tweetlerini çeker.
Retweet ve reply'lar hariç. Sonuçlar following_archive.json'a kaydedilir.

Bu arşiv iki amaca hizmet eder:
  1. like_following modu — takip edilenlerin tüm tweetlerine like at
  2. İleride: reply/quote hedefi olarak kullanılabilir

Kullanım:
    python following_scan.py

Çıktı:
    following_archive.json — {fetched_at, tweets: [{tweet_id, author, author_id, text, fetched_at}]}
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import tweepy

from core.twitter import get_twitter_clients, get_twitter_client_with_bearer
from utils.logger import get_logger

logger = get_logger(__name__)

ARCHIVE_PATH = os.path.join(os.path.dirname(__file__), "following_archive.json")

# Takip edilen hesap sayısı üst sınırı (API rate limit koruması)
_MAX_FOLLOWING = 500
# Kullanıcı başına max tweet sayısı
_TWEETS_PER_USER = 20
# Sorgular arası bekleme (sn) — rate limit
_SLEEP_BETWEEN = 0.5


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_following(client: tweepy.Client, user_id: str) -> list:
    """Takip edilen hesapları çek. Paginated, max _MAX_FOLLOWING."""
    following = []
    try:
        for user in tweepy.Paginator(
            client.get_users_following,
            id=user_id,
            max_results=1000,
            user_fields=["username", "public_metrics"],
        ).flatten(limit=_MAX_FOLLOWING):
            following.append(user)
    except Exception as e:
        logger.error(f"get_following error: {e}")
    logger.info(f"Following list: {len(following)} accounts")
    return following


def _get_user_tweets(client: tweepy.Client, user_id: str, start_time: datetime) -> list:
    """Kullanıcının son 24h orijinal tweetlerini çek (retweet ve reply hariç)."""
    try:
        resp = client.get_users_tweets(
            id=user_id,
            max_results=_TWEETS_PER_USER,
            exclude=["retweets", "replies"],
            start_time=start_time,
            tweet_fields=["text", "created_at", "public_metrics"],
        )
        return resp.data or []
    except tweepy.errors.TooManyRequests:
        logger.warning(f"Rate limit hit for user {user_id}, sleeping 60s...")
        time.sleep(60)
        return []
    except Exception as e:
        logger.warning(f"get_users_tweets error for {user_id}: {e}")
        return []


def run_following_scan() -> dict:
    """Ana tarama: takip edilenlerin son 24h tweetlerini topla → following_archive.json."""
    client, _ = get_twitter_clients()
    bearer_client = get_twitter_client_with_bearer()

    # Kendi kimliğimizi al
    try:
        me = client.get_me()
        if not me.data:
            raise ValueError("Empty response from get_me()")
    except Exception as e:
        logger.error(f"get_me() failed: {e}")
        sys.exit(1)

    user_id = str(me.data.id)
    logger.info(f"Authenticated as: @{me.data.username} (ID: {user_id})")

    following = _get_following(client, user_id)
    if not following:
        logger.error("No following accounts found, exiting.")
        sys.exit(1)

    since = _utcnow() - timedelta(hours=24)
    all_tweets = []
    fetched_at = _utcnow().isoformat()

    for user in following:
        tweets = _get_user_tweets(bearer_client, str(user.id), since)
        for tweet in tweets:
            all_tweets.append({
                "tweet_id":  str(tweet.id),
                "author":    user.username,
                "author_id": str(user.id),
                "text":      tweet.text,
                "fetched_at": fetched_at,
            })
        if tweets:
            logger.info(f"  @{user.username}: {len(tweets)} tweet(s)")
        time.sleep(_SLEEP_BETWEEN)

    result = {
        "fetched_at": fetched_at,
        "following_count": len(following),
        "tweet_count": len(all_tweets),
        "tweets": all_tweets,
    }

    tmp = ARCHIVE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    os.replace(tmp, ARCHIVE_PATH)

    logger.info(
        f"Following scan complete: {len(all_tweets)} tweets from "
        f"{len(following)} accounts → following_archive.json"
    )
    return result


if __name__ == "__main__":
    run_following_scan()
