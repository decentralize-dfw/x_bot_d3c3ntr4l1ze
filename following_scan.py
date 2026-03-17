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
import time
from datetime import datetime, timedelta, timezone

import tweepy

from core.twitter import get_twitter_clients, get_twitter_client_with_bearer
from utils.logger import get_logger

logger = get_logger(__name__)

ARCHIVE_PATH = os.path.join(os.path.dirname(__file__), "following_archive.json")
BACKUP_PATH  = os.path.join(os.path.dirname(__file__), "following_backup.json")

# Takip edilen hesap sayısı üst sınırı (API rate limit koruması)
_MAX_FOLLOWING = 500
# Kullanıcı başına max tweet sayısı
_TWEETS_PER_USER = 20
# Sorgular arası bekleme (sn) — rate limit
_SLEEP_BETWEEN = 0.5


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_following_v1(api: tweepy.API) -> list:
    """v1.1 friends/list — mevcut API planında genellikle 403 döner, graceful fail."""
    following = []
    try:
        for page in tweepy.Cursor(
            api.get_friends,
            count=200,
            skip_status=True,
            include_user_entities=False,
        ).pages():
            for user in page:
                following.append(user)
                if len(following) >= _MAX_FOLLOWING:
                    return following
    except Exception as e:
        logger.error(f"get_friends (v1.1) error: {e}")
    logger.info(f"Following list (v1.1): {len(following)} accounts")
    return following


def _load_backup_following(bearer_client: tweepy.Client) -> list:
    """following_backup.json'daki usernames → v2 get_users() ile objeler al.

    v1.1 planı yoksa bu yola düşeriz. Bearer token ile GET /2/users/by
    çalışır (basic v2 erişiminde mevcut).
    Döndürülen objelerin .id ve .username attribute'ları var (v2 User nesnesi).
    """
    try:
        with open(BACKUP_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        usernames = data.get("usernames", [])
        if not usernames:
            return []
        users = []
        # v2 get_users max 100 per call
        for i in range(0, len(usernames), 100):
            batch = usernames[i:i + 100]
            try:
                resp = bearer_client.get_users(usernames=batch, user_fields=["username"])
                if resp.data:
                    users.extend(resp.data)
            except Exception as e:
                logger.warning(f"get_users (v2 backup) batch error: {e}")
        logger.info(f"Backup following loaded (v2): {len(users)} accounts")
        return users
    except Exception as e:
        logger.error(f"_load_backup_following error: {e}")
        return []


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
    client, api = get_twitter_clients()
    bearer_client = get_twitter_client_with_bearer()

    # Kendi kimliğimizi al
    try:
        me = client.get_me()
        if not me.data:
            raise ValueError("Empty response from get_me()")
    except Exception as e:
        logger.error(f"get_me() failed: {e}")
        return {}

    logger.info(f"Authenticated as: @{me.data.username} (ID: {me.data.id})")

    # v1.1 API ile following listesini al (OAuth 1.0a)
    following = _get_following_v1(api)
    if not following:
        logger.warning("v1.1 following list empty — falling back to v2 backup")
        following = _load_backup_following(bearer_client)
    if not following:
        logger.warning("No following accounts found — archive not updated.")
        return {}

    since = _utcnow() - timedelta(hours=24)
    all_tweets = []
    fetched_at = _utcnow().isoformat()

    for user in following:
        # v1.1: .id_str / .screen_name  |  v2: .id / .username
        uid   = str(getattr(user, "id_str", None) or getattr(user, "id", ""))
        uname = getattr(user, "screen_name", None) or getattr(user, "username", "unknown")
        # BUG FIX #8: uid boşsa API'ye gönderme — 400 hatası alınır
        if not uid:
            logger.warning(f"Skipping user with empty id: @{uname}")
            continue
        tweets = _get_user_tweets(bearer_client, uid, since)
        for tweet in tweets:
            all_tweets.append({
                "tweet_id":  str(tweet.id),
                "author":    uname,
                "author_id": uid,
                "text":      tweet.text,
                "fetched_at": fetched_at,
            })
        if tweets:
            logger.info(f"  @{uname}: {len(tweets)} tweet(s)")
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
