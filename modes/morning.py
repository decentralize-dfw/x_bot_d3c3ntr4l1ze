"""
modes/morning.py
----------------
Sabah modu: Multimedia archive tweet (medya arşivinden rastgele içerik).
"""
import json
import os
import random
import re

import tweepy

import tweet_archive
from core.llm import generate_media_caption, score_tweet_quality
from core.twitter import get_twitter_clients, download_media
from core.voice import get_this_weeks_theme
from utils.logger import get_logger
from utils.text import format_tweet, trim_for_format

logger = get_logger(__name__)

TYPE_LABELS = {
    "glb":     "[3D Asset]",
    "vrm":     "[Avatar/VRM]",
    "video":   "[Video Archive]",
    "image":   "[Visual Archive]",
    "html":    "[Web Experience]",
    "website": "[Web Experience]",
    "text":    "[Manifesto/Text]",
    "pdf":     "[Document]",
}
MEDIA_TYPES = {"image", "video"}

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")


def _load_db() -> list:
    try:
        with open("database.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning("database.json not found, returning empty list.")
        return []
    except json.JSONDecodeError as e:
        logger.error(f"database.json parse error: {e}")
        return []


def _is_youtube(url: str) -> bool:
    return "youtube.com" in url or "youtu.be" in url


def post_morning_tweet():
    client, api = get_twitter_clients()
    db = _load_db()

    tweet_archive.cleanup_old_entries()

    media_items = [
        item
        for category, items in db.items()
        if isinstance(items, list)
        for item in items
        if item.get("type") in MEDIA_TYPES
    ]

    fresh_items = [i for i in media_items if not tweet_archive.is_posted_recently(i["id"])]
    if not fresh_items:
        logger.info("Archive: all media items posted recently, picking random anyway.")
        fresh_items = media_items

    selected = random.choice(fresh_items)
    name       = selected.get("name", "ARCHIVE_ITEM")
    item_type  = selected.get("type", "image")
    type_label = TYPE_LABELS.get(item_type, "[Archive]")
    desc       = selected.get("description", "")

    raw_url = selected.get("url", "https://decentralize.design")
    url = raw_url.replace("digitalforgerywork.shop", "decentralize.design")

    # YouTube: URL tweete eklenir — Twitter kart önizlemesi gösterir
    if _is_youtube(url):
        display_text = desc[:117] + "..." if len(desc) > 120 else desc
        tweet_text = f"{type_label} {name}\n\n{display_text}\n\n{url}"
        if len(tweet_text) > 280:
            tweet_text = tweet_text[:277] + "..."
        client.create_tweet(text=tweet_text)
        logger.info(f"Morning broadcast (YouTube card): {name}")
        return

    media_ids = []
    local_file = download_media(url)
    if local_file:
        try:
            if local_file.endswith(".mp4"):
                logger.info("Uploading Video (Chunked)...")
                media = api.media_upload(local_file, media_category="tweet_video", chunked=True)
            else:
                logger.info("Uploading Image...")
                media = api.media_upload(local_file)
            media_ids.append(media.media_id)
            os.remove(local_file)
        except Exception as e:
            logger.error(f"Upload failed ({type(e).__name__}): {e}")
            if os.path.exists(local_file):
                os.remove(local_file)

    display_text = ""
    if GROQ_API_KEY:
        try:
            display_text = generate_media_caption(name, desc, type_label)
        except Exception as e:
            logger.error(f"Caption generation error: {e}")
    if not display_text:
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", desc) if 30 < len(s.strip()) <= 137]
        display_text = sentences[0] if sentences else (desc[:134] + "..." if len(desc) > 137 else desc)

    inner = trim_for_format(f"{type_label} {name}\n\n{display_text}")
    tweet_text = format_tweet(inner)

    logger.info(f"Attempting morning tweet ({len(tweet_text)} chars): {tweet_text[:80]}...")
    try:
        if media_ids:
            resp = client.create_tweet(text=tweet_text, media_ids=media_ids)
        else:
            resp = client.create_tweet(text=tweet_text)
        tweet_archive.record_post(
            selected["id"], content_type="morning_media",
            tweet_text=tweet_text, tweet_id=resp.data["id"],
            weekly_theme=get_this_weeks_theme(),
            media_url=url if media_ids else None,
        )
        logger.info(f"Morning broadcast complete: {name}")

    except tweepy.errors.Forbidden as e:
        api_codes = getattr(e, "api_codes", [])
        logger.error(f"Twitter 403 — codes: {api_codes}")
        if 187 in api_codes:
            logger.info("Duplicate detected, retrying with a different item...")
            selected2 = random.choice([i for i in media_items if i != selected])
            name2      = selected2.get("name", "ARCHIVE_ITEM")
            desc2      = selected2.get("description", "")
            type_label2 = TYPE_LABELS.get(selected2.get("type", "image"), "[Archive]")
            caption2 = ""
            if GROQ_API_KEY:
                try:
                    caption2 = generate_media_caption(name2, desc2, type_label2)
                except Exception:
                    pass
            if not caption2:
                caption2 = desc2[:134] + "..." if len(desc2) > 137 else desc2
            retry_text = format_tweet(trim_for_format(f"{type_label2} {name2}\n\n{caption2}"))
            try:
                resp2 = client.create_tweet(text=retry_text)
                tweet_archive.record_post(
                    selected2["id"], content_type="morning_media",
                    tweet_text=retry_text, tweet_id=resp2.data["id"],
                    weekly_theme=get_this_weeks_theme(),
                )
                logger.info(f"Morning broadcast complete (retry): {name2}")
            except Exception as _re:
                tweet_archive.record_failed(
                    selected2["id"], "morning_media",
                    tweet_text=retry_text, error_msg=str(_re),
                    weekly_theme=get_this_weeks_theme(),
                )
                logger.error("Morning retry tweet failed — logged to failed_tweets.json.")

        elif "2 minutes" in str(e) or "longer than 2" in str(e).lower():
            logger.warning(f"Video >2min upload rejected — falling back to link tweet: {name}")
            prefix = format_tweet(trim_for_format(f"[Video >2min] {name}\n\n{display_text}"))
            if len(prefix) > 255:
                prefix = prefix[:252] + "..."
            link_text = f"{prefix}\n\n{url}"
            try:
                resp_link = client.create_tweet(text=link_text)
                tweet_archive.record_post(
                    selected["id"], content_type="morning_media_long_video",
                    tweet_text=link_text, tweet_id=resp_link.data["id"],
                    weekly_theme=get_this_weeks_theme(), media_url=url,
                )
                logger.info(f"Morning broadcast complete (link fallback >2min): {name}")
            except Exception as _le:
                tweet_archive.record_failed(
                    selected["id"], "morning_media_long_video",
                    tweet_text=link_text, error_msg=str(_le),
                    media_url=url, weekly_theme=get_this_weeks_theme(),
                )
                logger.error("Morning link tweet failed — logged.")
        else:
            tweet_archive.record_failed(
                selected["id"], "morning_media",
                tweet_text=tweet_text, error_msg=str(e),
                media_url=url, weekly_theme=get_this_weeks_theme(),
            )
            logger.error("Morning tweet failed (unhandled 403) — logged.")
