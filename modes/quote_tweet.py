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
from modes.viral_mix import fetch_targets_for_reply
from utils.logger import get_logger

logger = get_logger(__name__)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

# BUG FIX #15: 9.0 çok sıkı — score_tweet_quality() 4 eksenin MIN'ini döner.
# GROQ API fail → 0.0 → tüm adaylar rejected → sıfır quote tweet atılıyor.
# 6.0'a indirildi: tüm eksenler en az 6/10 olsun yeterli.
_QUOTE_QUALITY_MIN = 6.0


def post_quote_tweet():
    """Target hesapların tweet'lerini kriptik commentary ile quote et.

    fetch_targets_for_reply() kullanır — targets.json'daki curated hesaplar.
    scan_results.json kullanmaz: orası yabancı hesaplar, Twitter quote izni vermez (403).
    """
    client, _ = get_twitter_clients()

    logger.info("Fetching target tweets for quote-tweet...")
    candidates = fetch_targets_for_reply(n_targets=10)
    if not candidates:
        logger.warning("No candidates for quote-tweet, skipping.")
        return

    # Daha önce quote edilmemiş olanları önce dene
    fresh = [
        c for c in candidates
        if not tweet_archive.is_posted_recently(
            "qt_" + hashlib.md5(c["text"].encode()).hexdigest()[:12], days=14
        )
    ]
    queue = fresh if fresh else candidates

    for selected in queue:
        commentary = generate_quote_commentary(selected["text"])
        quality = score_tweet_quality(commentary)
        if quality < _QUOTE_QUALITY_MIN:
            logger.warning(
                f"Quote commentary quality {quality}/10 < {_QUOTE_QUALITY_MIN}, "
                f"skipping @{selected['author']}."
            )
            continue

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
            return  # Başarılı — döngüyü sonlandır
        except Exception as e:
            err = str(e)
            if "403" in err and "not allowed" in err.lower():
                logger.warning(
                    f"Quote not allowed for @{selected['author']} tweet — "
                    "trying next candidate..."
                )
                continue  # Bu tweet quote edilemiyor, bir sonrakini dene
            logger.error(f"Quote-tweet failed: {e}")
            return

    logger.warning("Quote-tweet: no suitable candidate found after all attempts.")
