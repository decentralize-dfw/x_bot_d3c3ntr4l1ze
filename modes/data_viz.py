"""
modes/data_viz.py
-----------------
Haftalık niche keyword frekans bar chart tweet'i.
Parallel RSS fetching kullanır (Faz 3.5).
"""
import os
import tempfile
from datetime import datetime, timezone

import tweet_archive
from core.llm import _call_llm
from core.rss import fetch_all_feeds
from core.twitter import get_twitter_clients
from core.voice import TONE_BLOCK, get_this_weeks_theme
from utils.logger import get_logger
from utils.text import format_tweet

logger = get_logger(__name__)

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

_KEYWORD_MAP = {
    "WebXR":             ["webxr", "web xr"],
    "Metaverse":         ["metaverse"],
    "Spatial Computing": ["spatial computing", "spatial"],
    "Virtual Reality":   ["virtual reality", "vr "],
    "Decentralized":     ["decentrali", "on-chain", "web3"],
    "AI + 3D":           ["generative 3d", "ai design", "ai 3d", "3d ai"],
}


def post_data_viz_tweet():
    """Haftada 1: niche konuların RSS frekansını bar chart ile tweet'le."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    client, api = get_twitter_clients()

    viz_id = "dataviz_" + datetime.now(timezone.utc).strftime("%Y_W%V")
    if tweet_archive.is_posted_recently(viz_id, days=6):
        logger.info("Data viz already posted this week, skipping.")
        return

    # Parallel RSS fetch (Faz 3.5)
    all_articles = fetch_all_feeds(_RSS_FEEDS, max_items=10)
    counts = {k: 0 for k in _KEYWORD_MAP}

    for source, articles in all_articles.items():
        for a in articles:
            combined = (a["title"] + " " + a.get("summary", "")).lower()
            for label, kws in _KEYWORD_MAP.items():
                if any(kw in combined for kw in kws):
                    counts[label] += 1

    if sum(counts.values()) == 0:
        logger.warning("All zero counts, skipping data viz.")
        return

    sorted_items = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    labels = [k for k, v in sorted_items if v > 0]
    values = [v for k, v in sorted_items if v > 0]
    if not labels:
        logger.warning("No positive counts, skipping data viz.")
        return

    # Dark-theme bar chart
    fig, ax = plt.subplots(figsize=(9, 4.5))
    fig.patch.set_facecolor("#0d1117")
    ax.set_facecolor("#0d1117")
    bars = ax.barh(labels, values, color="#FF3B6F", height=0.55)
    ax.set_xlabel("mentions in tech media this week", color="#8b949e", fontsize=10)
    ax.tick_params(colors="#e6edf3", labelsize=10)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    for spine in ["bottom", "left"]:
        ax.spines[spine].set_color("#30363d")
    ax.xaxis.label.set_color("#8b949e")
    for bar, val in zip(bars, values):
        ax.text(bar.get_width() + 0.08, bar.get_y() + bar.get_height() / 2,
                str(val), va="center", color="#e6edf3", fontsize=9)
    ax.set_title("what tech media is covering this week", color="#e6edf3", fontsize=12, pad=10)
    plt.tight_layout()

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        plt.savefig(tmp_path, dpi=150, bbox_inches="tight", facecolor="#0d1117")
        plt.close()

        top_topic = labels[0]
        caption_prompt = (
            f"You are @decentralize___.\n{TONE_BLOCK}"
            f"Write a 1-sentence tweet for a data chart showing '{top_topic}' dominates tech media coverage this week. "
            f"Be provocative — not just descriptive. What does this signal? Max 150 chars."
        )
        try:
            caption = _call_llm(caption_prompt, max_tokens=50, temperature=0.9).strip('"\'')
        except Exception:
            caption = f"{top_topic} is dominating the conversation. the question is whether the industry is building or just talking."

        tweet_text = format_tweet(caption)
        if len(tweet_text) > 280:
            tweet_text = caption[:274] + "... ꩜"

        media = api.media_upload(tmp_path)
        resp = client.create_tweet(text=tweet_text, media_ids=[media.media_id_string])
        tweet_archive.record_post(viz_id, content_type="data_viz",
                                  tweet_text=tweet_text, tweet_id=resp.data["id"],
                                  weekly_theme=get_this_weeks_theme())
        logger.info(f"Data viz tweet posted: {resp.data['id']} — {tweet_text[:60]}...")
    except Exception as e:
        logger.error(f"Data viz error: {e}")
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
