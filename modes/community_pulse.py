"""
modes/community_pulse.py
------------------------
Community pulse: haftalık niche RSS özet thread'i.
Parallel RSS fetching kullanır (Faz 3.5).
"""
import os
from datetime import datetime, timezone

import tweet_archive
from core.llm import _call_llm
from core.rss import fetch_all_feeds
from core.twitter import get_twitter_clients
from core.voice import NICHE_KEYWORDS, TONE_BLOCK, get_this_weeks_theme
from utils.logger import get_logger
from utils.text import format_tweet

logger = get_logger(__name__)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

_RSS_FEEDS = {
    "decrypt.co":       "https://decrypt.co/feed/",
    "venturebeat.com":  "https://venturebeat.com/feed/",
    "roadtovr.com":     "https://www.roadtovr.com/feed/",
    "awwwards.com":     "https://www.awwwards.com/blog/rss",
    "webxr.news":       "https://webxr.news/rss",
    "sketchfab.com":    "https://sketchfab.com/blogs/community/feed",
    "techcrunch.com":   "https://techcrunch.com/feed/",
    "theverge.com":     "https://www.theverge.com/rss/index.xml",
    "a16z.com":         "https://a16z.com/feed/",
    "ieee_spectrum_vr": "https://spectrum.ieee.org/feeds/topic/virtual-reality.rss",
}

_NICHE_KW = [kw.lower() for kw in NICHE_KEYWORDS] + ["vr", "xr", "ar", "virtual", "immersive", "3d"]


def post_community_pulse_thread():
    """Her Pazartesi: niche RSS'ten haftalık pulse thread'i."""
    client, _ = get_twitter_clients()

    pulse_id = "pulse_" + datetime.now(timezone.utc).strftime("%Y_W%V")
    if tweet_archive.is_posted_recently(pulse_id, days=6):
        logger.info("Community pulse already posted this week, skipping.")
        return

    # Parallel RSS fetch (Faz 3.5)
    all_articles = fetch_all_feeds(_RSS_FEEDS, max_items=6)
    all_headlines = []
    for source, articles in all_articles.items():
        for a in articles:
            title_lower = a["title"].lower()
            if any(kw in title_lower for kw in _NICHE_KW):
                all_headlines.append(f"[{source}] {a['title']}")

    if not all_headlines:
        logger.warning("No niche headlines for community pulse, skipping.")
        return

    logger.info(f"Community pulse: {len(all_headlines)} niche headlines found.")
    headlines_block = "\n".join(all_headlines[:25])

    prompt = (
        "You are @decentralize___, thought leader in WebXR, virtual design, and spatial computing.\n"
        f"{TONE_BLOCK}"
        f"Here are this week's niche headlines:\n{headlines_block}\n\n"
        "Write EXACTLY 4 tweet-sized insights for a weekly pulse thread.\n"
        "Rules:\n"
        "- Each insight is a standalone synthesis, NOT a headline summary\n"
        "- Find the pattern nobody else is naming\n"
        "- Use first-person perspective\n"
        "- Max 200 chars each, end each with 2-3 relevant hashtags\n"
        "- Each on its own line\n"
        "Output: 4 lines only."
    )
    try:
        raw = _call_llm(prompt, max_tokens=400, temperature=0.88)
        insights = [l.strip() for l in raw.strip().split("\n") if l.strip()][:4]
    except Exception as e:
        logger.error(f"Pulse LLM error: {e}")
        return

    if not insights:
        logger.warning("No pulse insights generated, skipping.")
        return

    header = "this week in virtual design & spatial computing ꩜"
    logger.info(f"Posting community pulse thread ({len(insights)} insights)...")

    try:
        resp = client.create_tweet(text=header)
        parent_id = resp.data["id"]
        tweet_archive.record_post(pulse_id, content_type="community_pulse",
                                  tweet_text=header, tweet_id=parent_id,
                                  weekly_theme=get_this_weeks_theme())
    except Exception as e:
        logger.error(f"Pulse header error: {e}")
        return

    for i, insight in enumerate(insights):
        tweet_text = format_tweet(insight)
        if len(tweet_text) > 280:
            tweet_text = insight[:274] + "... ꩜"
        try:
            resp = client.create_tweet(text=tweet_text, in_reply_to_tweet_id=parent_id)
            parent_id = resp.data["id"]
            logger.info(f"  Insight {i+1}: {tweet_text[:70]}...")
        except Exception as e:
            logger.error(f"  Insight {i+1} error: {e}")

    logger.info("Community pulse thread done.")
