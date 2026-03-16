"""
modes/artwork.py
----------------
Artwork drop modu: artworks.json'dan rastgele eser paylaşımı.
"""
import json
import os
import random

import tweet_archive
from core.llm import generate_artwork_tweet
from core.twitter import get_twitter_clients, download_media
from core.voice import get_this_weeks_theme
from utils.logger import get_logger
from utils.text import format_tweet

logger = get_logger(__name__)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")


def post_artwork_tweet():
    """Pick a random artwork, post image + metadata, then reply with site link."""
    client, api = get_twitter_clients()

    try:
        with open("artworks.json", "r", encoding="utf-8") as f:
            artworks = json.load(f)
    except FileNotFoundError:
        logger.error("artworks.json not found, skipping artwork tweet.")
        return
    except json.JSONDecodeError as e:
        logger.error(f"artworks.json parse error: {e}")
        return

    if not artworks:
        logger.warning("No artworks found in artworks.json.")
        return

    fresh_artworks = [a for a in artworks if not tweet_archive.is_posted_recently(a["id"])]
    if not fresh_artworks:
        logger.info("Archive: all artworks posted recently, picking random anyway.")
        fresh_artworks = artworks

    artwork     = random.choice(fresh_artworks)
    name        = artwork.get("name", "")
    description = artwork.get("description", "")
    cats        = artwork.get("categories", {})

    tweet_text = None
    if GROQ_API_KEY:
        try:
            tweet_text = generate_artwork_tweet(name, description, cats)
        except Exception as e:
            logger.error(f"Artwork LLM error: {e}")

    if not tweet_text:
        base = f"{name} — {description}".strip()
        words = base.split()
        tweet_text = ""
        for w in words:
            candidate = (tweet_text + " " + w).strip()
            if len(candidate) > 130:
                break
            tweet_text = candidate

    media_ids = []
    img_url   = None
    media_list = artwork.get("media", [])
    if media_list:
        img_url = media_list[0].get("src")
        if img_url:
            logger.info(f"Downloading artwork image: {img_url}")
            local_file = download_media(img_url)
            if local_file:
                try:
                    logger.info("Uploading artwork image...")
                    media = api.media_upload(local_file)
                    media_ids.append(media.media_id)
                    os.remove(local_file)
                except Exception as e:
                    logger.error(f"Upload failed: {e}")
                    if os.path.exists(local_file):
                        os.remove(local_file)

    tweet_text = format_tweet(tweet_text)
    logger.info(f"Posting artwork tweet: {tweet_text[:80]}...")
    try:
        if media_ids:
            resp = client.create_tweet(text=tweet_text, media_ids=media_ids)
        else:
            resp = client.create_tweet(text=tweet_text)
        tweet_id = resp.data["id"]
        tweet_archive.record_post(artwork["id"], content_type="artwork",
                                  tweet_text=tweet_text, tweet_id=tweet_id,
                                  weekly_theme=get_this_weeks_theme(),
                                  media_url=img_url)
        try:
            client.create_tweet(
                text="explore the collection: de-centralize.com #digitalart #metaverse",
                in_reply_to_tweet_id=tweet_id,
            )
        except Exception as _te:
            tweet_archive.record_failed(
                artwork["id"] + "_thread", "thread_reply",
                tweet_text="explore the collection: de-centralize.com #digitalart #metaverse",
                error_msg=str(_te),
            )
            logger.warning("Artwork thread reply failed — logged.")
        logger.info(f"Artwork thread posted: {name}")
    except Exception as e:
        tweet_archive.record_failed(
            artwork["id"], "artwork",
            tweet_text=tweet_text, error_msg=str(e),
            media_url=img_url, weekly_theme=get_this_weeks_theme(),
        )
        logger.error("Artwork tweet failed — logged.")
