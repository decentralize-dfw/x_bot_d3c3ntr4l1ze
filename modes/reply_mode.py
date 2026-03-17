"""
modes/reply_mode.py
-------------------
Quote tweet engage modu — scan + targets kaynaklı, engagement gerektirmez.

Direct reply (in_reply_to_tweet_id) Twitter tarafından cold accounts için engelleniyor
(403: "not mentioned or engaged"). Quote tweet aynı algoritmik değeri taşıyor ve
herhangi bir hesabın tweet'ini quote edebilirsin.

Günlük hedef: 5 quote tweet/run × 2 run = 10/gün.

BUG FIX #21: post_reply_tweet() → post_quote_engage() olarak yeniden adlandırıldı.
  Bu mod direct reply DEĞIL, quote tweet atar — eski isim bakımcıları yanıltıyordu.
  bot.py routing güncellendi. Geriye uyumluluk alias'ı dosyanın sonunda.
"""
import hashlib
import os

import tweet_archive
from core.llm import generate_quote_commentary, score_tweet_quality
from core.twitter import get_twitter_clients
from core.voice import get_this_weeks_theme
from modes.viral_mix import fetch_target_tweets_with_ids
from utils.logger import get_logger

logger = get_logger(__name__)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

_QUOTE_COOLDOWN_DAYS = 3
_MAX_QUOTES_PER_RUN = 5


def post_quote_engage():
    """Target tweet'leri quote tweet olarak engage et.

    Direct reply yerine quote tweet — Twitter cold-reply'ı engelliyor
    (403: author hasn't engaged with you). Quote tweet kısıtlama yok.
    Scan_results.json → targets.json fallback zinciri.
    """
    client, _ = get_twitter_clients()

    logger.info("Fetching candidates for quote-tweet engagement...")
    candidates = fetch_target_tweets_with_ids(n_targets=_MAX_QUOTES_PER_RUN + 5)
    if not candidates:
        logger.warning("No candidates for quote engagement, skipping.")
        return

    quoted = 0
    for c in candidates:
        if quoted >= _MAX_QUOTES_PER_RUN:
            break

        archive_id = "qtreply_" + hashlib.md5(c["text"].encode()).hexdigest()[:12]
        if tweet_archive.is_posted_recently(archive_id, days=_QUOTE_COOLDOWN_DAYS):
            logger.info(f"@{c['author']}: recently quoted, skipping.")
            continue

        commentary = generate_quote_commentary(c["text"])
        quality = score_tweet_quality(commentary)
        if quality < 5.0:
            logger.info(f"@{c['author']}: quote quality {quality:.1f}/10 too low, skipping.")
            continue

        logger.info(f"Quote-tweeting @{c['author']}: {commentary[:80]}...")
        try:
            resp = client.create_tweet(
                text=commentary,
                quote_tweet_id=c["id"],
            )
            tweet_archive.record_post(
                archive_id, content_type="quote_engage",
                tweet_text=commentary, tweet_id=resp.data["id"],
                weekly_theme=get_this_weeks_theme(),
            )
            logger.info(f"Quote tweet posted: {resp.data['id']}")
            quoted += 1
        except Exception as e:
            logger.error(f"Quote tweet for @{c['author']} failed: {e}")

    logger.info(f"Quote engagement done: {quoted}/{_MAX_QUOTES_PER_RUN} posted.")
    if quoted == 0:
        logger.warning("No quote tweets sent this run.")


# Geriye uyumluluk alias — eski çağrılar çalışmaya devam eder
post_reply_tweet = post_quote_engage
