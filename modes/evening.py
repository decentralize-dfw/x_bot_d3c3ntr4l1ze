"""
modes/evening.py
----------------
Akşam modları: viral manifesto + controversial manifesto.
Her ikisi de post_with_retry ortak döngüsünü kullanır (Faz 2.2).
"""
import json
import os
import random
import re

import tweepy

import tweet_archive
from core.llm import (
    _call_llm,
    distill_to_tweet,
    generate_controversial_tweet,
    generate_viral_tweet,
    score_tweet_quality,
)
from core.quality import post_with_retry
from core.twitter import get_twitter_clients
from core.voice import FOLLOW_UP_QUESTIONS, get_this_weeks_theme
from utils.logger import get_logger
from utils.text import format_tweet, trim_for_format

logger = get_logger(__name__)

GROQ_API_KEY     = os.environ.get("GROQ_API_KEY")
SEARCH_KEYWORDS  = (
    '(metaverse OR "virtual world" OR "AI agent" OR "on-chain" OR '
    '"spatial computing" OR "web3" OR "3D NFT" OR "WebXR")'
)


def _load_db():
    try:
        with open("database.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"database.json load error: {e}")
        return {}


def _pick_text_item(db: dict) -> dict | None:
    text_items = [
        item
        for category, items in db.items()
        if isinstance(items, list)
        for item in items
        if item.get("type") == "text" and len(item.get("content", "")) > 500
    ]
    fresh = [i for i in text_items if not tweet_archive.is_posted_recently(i["id"])]
    if not fresh:
        logger.info("Archive: all text items posted recently, picking random anyway.")
        fresh = text_items
    return random.choice(fresh) if fresh else None


def _random_chunk(content: str, max_words: int = 150) -> str:
    words = content.split()
    if len(words) > max_words:
        start = random.randint(0, len(words) - max_words)
        return " ".join(words[start : start + max_words])
    return content


def _fetch_context_tweets(query: str, max_results: int = 10) -> list:
    client, _ = get_twitter_clients()
    try:
        resp = client.search_recent_tweets(
            query=f"{query} -is:retweet -is:reply lang:en",
            max_results=max_results,
            tweet_fields=["public_metrics", "text"],
            sort_order="relevancy",
        )
        if not resp.data:
            return []
        tweets = sorted(
            resp.data,
            key=lambda t: (
                t.public_metrics.get("like_count", 0)
                + t.public_metrics.get("retweet_count", 0) * 2
            ),
            reverse=True,
        )
        return [t.text for t in tweets[:3]]
    except Exception as e:
        logger.warning(f"Context fetch error: {e}")
        return []


def _post_thread_reply_safe(client, tweet_id: str, main_tweet_text: str) -> None:
    """Thread reply + quality gate (Faz 1.4)."""
    from core.llm import generate_thread_reply
    reply = generate_thread_reply(main_tweet_text)
    if not reply:
        return
    if score_tweet_quality(reply) < 5.0:
        retry = generate_thread_reply(main_tweet_text)
        if retry:
            reply = retry
    try:
        client.create_tweet(text=reply, in_reply_to_tweet_id=tweet_id)
    except Exception as _te:
        tweet_archive.record_failed(
            str(tweet_id) + "_thread", "thread_reply",
            tweet_text=reply, error_msg=str(_te),
        )
        logger.warning("Thread reply failed — logged.")


def post_evening_tweet():
    """Akşam viral manifesto tweet."""
    client, _ = get_twitter_clients()
    db = _load_db()
    selected = _pick_text_item(db)
    if not selected:
        logger.error("No text items in database, skipping evening tweet.")
        return

    content = selected.get("content", "")
    name    = selected.get("name", "")

    context_tweets = []
    if GROQ_API_KEY:
        try:
            context_tweets = _fetch_context_tweets(SEARCH_KEYWORDS)
        except Exception as e:
            logger.warning(f"Context fetch error: {e}")

    def _gen():
        chunk = _random_chunk(content)
        return generate_viral_tweet(chunk, name, context_tweets)

    tweet_text = post_with_retry(_gen) if GROQ_API_KEY else None

    if not tweet_text:
        sentences = [
            s.strip() for s in re.split(r"(?<=[.!?])\s+", content)
            if 70 < len(s.strip()) < 240
            and not s.strip().isupper()
            and "\n" not in s.strip()[:30]
        ]
        tweet_text = random.choice(sentences) if sentences else content[:240]

    tweet_text = format_tweet(trim_for_format(tweet_text))

    logger.info(f"Attempting evening tweet ({len(tweet_text)} chars): {tweet_text[:80]}...")
    try:
        resp = client.create_tweet(text=tweet_text)
        tweet_id = resp.data["id"]
        tweet_archive.record_post(
            selected["id"], content_type="evening_text",
            tweet_text=tweet_text, tweet_id=tweet_id,
            weekly_theme=get_this_weeks_theme(),
        )
        _post_thread_reply_safe(client, tweet_id, tweet_text)
        logger.info(f"Evening broadcast complete: {tweet_text[:60]}...")
    except tweepy.errors.Forbidden as e:
        api_codes = getattr(e, "api_codes", [])
        logger.error(f"Twitter 403 — codes: {api_codes}")
        if 187 in api_codes and GROQ_API_KEY:
            logger.info("Duplicate detected, retrying with a different chunk...")
            new_chunk  = _random_chunk(content)
            tweet_text = format_tweet(trim_for_format(distill_to_tweet(new_chunk, name)))
            try:
                resp = client.create_tweet(text=tweet_text)
                tweet_id = resp.data["id"]
                _post_thread_reply_safe(client, tweet_id, tweet_text)
                logger.info(f"Evening broadcast complete (retry): {tweet_text[:60]}...")
            except Exception as _re:
                tweet_archive.record_failed(
                    selected["id"], "evening_text",
                    tweet_text=tweet_text, error_msg=str(_re),
                    weekly_theme=get_this_weeks_theme(),
                )
                logger.error("Evening retry tweet failed — logged.")
        else:
            tweet_archive.record_failed(
                selected["id"], "evening_text",
                tweet_text=tweet_text, error_msg=str(e),
                weekly_theme=get_this_weeks_theme(),
            )
            logger.error("Evening tweet failed (unhandled 403) — logged.")


def post_controversial_evening_tweet():
    """Akşam contrarian manifesto tweet."""
    client, _ = get_twitter_clients()
    db = _load_db()
    selected = _pick_text_item(db)
    if not selected:
        logger.error("No text items in database, skipping controversial tweet.")
        return

    content = selected.get("content", "")
    name    = selected.get("name", "")

    context_tweets = []
    if GROQ_API_KEY:
        try:
            context_tweets = _fetch_context_tweets(SEARCH_KEYWORDS)
        except Exception as e:
            logger.warning(f"Context fetch error: {e}")

    def _gen():
        chunk = _random_chunk(content)
        return generate_controversial_tweet(chunk, name, context_tweets)

    tweet_text = post_with_retry(_gen) if GROQ_API_KEY else None

    if not tweet_text:
        sentences = [
            s.strip() for s in re.split(r"(?<=[.!?])\s+", content)
            if 70 < len(s.strip()) < 240
            and not s.strip().isupper()
            and "\n" not in s.strip()[:30]
        ]
        tweet_text = random.choice(sentences) if sentences else content[:240]

    tweet_text = format_tweet(trim_for_format(tweet_text))

    logger.info(f"Attempting controversial tweet ({len(tweet_text)} chars): {tweet_text[:80]}...")
    try:
        resp = client.create_tweet(text=tweet_text)
        tweet_id = resp.data["id"]
        tweet_archive.record_post(
            selected["id"], content_type="evening_controversial",
            tweet_text=tweet_text, tweet_id=tweet_id,
            weekly_theme=get_this_weeks_theme(),
        )
        _post_thread_reply_safe(client, tweet_id, tweet_text)
        logger.info(f"Controversial broadcast complete: {tweet_text[:60]}...")
    except tweepy.errors.Forbidden as e:
        api_codes = getattr(e, "api_codes", [])
        logger.error(f"Twitter 403 — codes: {api_codes}")
        if 187 in api_codes and GROQ_API_KEY:
            logger.info("Duplicate detected, retrying with different chunk...")
            new_chunk  = _random_chunk(content)
            tweet_text = format_tweet(trim_for_format(distill_to_tweet(new_chunk, name)))
            try:
                resp = client.create_tweet(text=tweet_text)
                tweet_id = resp.data["id"]
                _post_thread_reply_safe(client, tweet_id, tweet_text)
                logger.info(f"Controversial complete (retry): {tweet_text[:60]}...")
            except Exception as _re:
                tweet_archive.record_failed(
                    selected["id"], "evening_controversial",
                    tweet_text=tweet_text, error_msg=str(_re),
                    weekly_theme=get_this_weeks_theme(),
                )
                logger.error("Controversial retry tweet failed — logged.")
        else:
            tweet_archive.record_failed(
                selected["id"], "evening_controversial",
                tweet_text=tweet_text, error_msg=str(e),
                weekly_theme=get_this_weeks_theme(),
            )
            logger.error("Controversial tweet failed (unhandled 403) — logged.")
