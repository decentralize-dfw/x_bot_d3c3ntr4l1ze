"""
modes/quote_tweet.py
--------------------
Quote tweet modu — Premium hesap ile AKTIF (Faz 3.1).

Hesap Free Tier → Premium'a alındı.
search_recent_tweets + quote_tweet_id artık kullanılabilir.
Günlük hedef: 5 quote tweet (d3c3ntr4l1z3_strategy.docx §05).
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


def post_quote_tweet():
    """Target hesapların tweet'lerini kriptik commentary ile quote et.

    Mid-tier hesaplara (10k-50k follower) odaklanır — yanıt verme
    olasılıkları yüksek, algoritma quote'ları yayar.
    """
    client, _ = get_twitter_clients()

    logger.info("Fetching target tweets for quote-tweet...")
    candidates = fetch_target_tweets_with_ids(n_targets=5)
    if not candidates:
        logger.warning("No candidates for quote-tweet, skipping.")
        return

    # Arşivlenmemiş birini seç (son 14 gün içinde quote edilmemiş)
    selected = None
    for c in candidates:
        archive_id = "qt_" + hashlib.md5(c["text"].encode()).hexdigest()[:12]
        if not tweet_archive.is_posted_recently(archive_id, days=14):
            selected = c
            break
    if not selected:
        import random
        selected = random.choice(candidates)

    commentary = generate_quote_commentary(selected["text"])
    quality = score_tweet_quality(commentary)
    if quality < 5.5:
        logger.warning(f"Quote-tweet quality {quality:.1f}/10 too low, skipping.")
        return

    logger.info(f"Posting quote-tweet of @{selected['author']}: {commentary[:80]}...")
    try:
        resp = client.create_tweet(text=commentary, quote_tweet_id=selected["id"])
        archive_id = "qt_" + hashlib.md5(selected["text"].encode()).hexdigest()[:12]
        tweet_archive.record_post(
            archive_id, content_type="quote_tweet",
            tweet_text=commentary, tweet_id=resp.data["id"],
            weekly_theme=get_this_weeks_theme(),
        )
        logger.info(f"Quote-tweet posted: {resp.data['id']}")
    except Exception as e:
        logger.error(f"Quote-tweet failed: {e}")
