"""
modes/reply_mode.py
-------------------
Reply modu — Premium API ile aktif. (Faz 3.2)

Target hesapların son tweet'lerine kriptik, thoughtful yanıtlar ver.
Günlük hedef: 10 reply (d3c3ntr4l1z3_strategy.docx §05).
Reply = algoritmanın en değerli sinyali (150x like değeri).
2x/gün çalışır → 5/run × 2 = 10 reply/gün.
"""
import hashlib
import os

import tweet_archive
from core.llm import generate_reply_comment, score_tweet_quality
from core.twitter import get_twitter_clients
from core.voice import get_this_weeks_theme
from modes.viral_mix import fetch_targets_for_reply
from utils.logger import get_logger

logger = get_logger(__name__)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

# Mid-tier hesaplar için cooldown: 3 gün (aynı kişiye spam yapma)
_REPLY_COOLDOWN_DAYS = 3
# Her run'da maksimum reply sayısı (2x/gün → ~10/gün)
_MAX_REPLIES_PER_RUN = 5


def post_reply_tweet():
    """Target hesapların son tweet'lerine kriptik yanıt gönder.

    Mid-tier odak (10k-50k follower): yanıt verme olasılığı %60+.
    Son 3 günde yanıt verilen hesapları atla.
    Run başına max 5 reply — günde 2 kez çalışarak ~10/gün hedefine ulaşır.
    """
    client, _ = get_twitter_clients()

    logger.info("Fetching target tweets for reply mode (targets.json only)...")
    candidates = fetch_targets_for_reply(n_targets=10)
    if not candidates:
        logger.warning("No candidates for reply mode, skipping.")
        return

    replied = 0
    for c in candidates:
        if replied >= _MAX_REPLIES_PER_RUN:
            break

        archive_id = "reply_" + hashlib.md5(c["text"].encode()).hexdigest()[:12]
        if tweet_archive.is_posted_recently(archive_id, days=_REPLY_COOLDOWN_DAYS):
            logger.info(f"@{c['author']}: recently replied, skipping.")
            continue

        commentary = generate_reply_comment(c["text"])
        quality = score_tweet_quality(commentary)
        if quality < 5.0:
            logger.info(f"@{c['author']}: reply quality {quality:.1f}/10 too low, skipping.")
            continue

        logger.info(f"Replying to @{c['author']}: {commentary[:80]}...")
        try:
            resp = client.create_tweet(
                text=commentary,
                in_reply_to_tweet_id=c["id"],
            )
            tweet_archive.record_post(
                archive_id, content_type="reply",
                tweet_text=commentary, tweet_id=resp.data["id"],
                weekly_theme=get_this_weeks_theme(),
            )
            logger.info(f"Reply posted: {resp.data['id']}")
            replied += 1
        except Exception as e:
            logger.error(f"Reply to @{c['author']} failed: {e}")

    logger.info(f"Reply mode done: {replied}/{_MAX_REPLIES_PER_RUN} replies sent.")
    if replied == 0:
        logger.warning("No replies sent this run.")
