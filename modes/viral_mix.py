"""
modes/viral_mix.py
------------------
Viral mix: target tweet'leri + manifesto karışımı.
post_with_retry ortak döngüsünü kullanır.
"""
import hashlib
import json
import os
import random
import re
import time
import xml.etree.ElementTree as ET

import tweepy
from bs4 import BeautifulSoup

import tweet_archive
from core.llm import (
    generate_viral_mix_tweet,
    generate_viral_tweet,
    generate_quote_commentary,
    score_tweet_quality,
)
from core.quality import post_with_retry
from core.rss import _parse_rss_all
from core.twitter import get_twitter_clients, get_twitter_client_with_bearer
from core.voice import NICHE_KEYWORDS, get_this_weeks_theme
from utils.http import get_session
from utils.logger import get_logger
from utils.text import format_tweet, trim_for_format

logger = get_logger(__name__)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

_RSS_SOURCES = [
    ("The Verge",  "https://www.theverge.com/rss/index.xml"),
    ("TechCrunch", "https://techcrunch.com/feed/"),
    ("Decrypt",    "https://decrypt.co/feed"),
    ("Road to VR", "https://www.roadtovr.com/feed/"),
]

_NITTER_INSTANCES = [
    "nitter.privacydev.net",
    "nitter.poast.org",
    "nitter.cz",
    "nitter.1d4.us",
]


def _load_db():
    try:
        with open("database.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"database.json load error: {e}")
        return {}


def fetch_target_tweets_nitter(n_targets: int = 8, tweets_per_user: int = 3) -> list:
    """Target hesapların Nitter RSS üzerinden son tweet'lerini çek."""
    try:
        with open("targets.json", "r") as f:
            targets = json.load(f)
    except Exception as e:
        logger.error(f"targets.json load error: {e}")
        return []

    top_targets = sorted(targets, key=lambda t: t.get("engagement_score", 0), reverse=True)[:n_targets]
    session = get_session()
    all_tweets = []

    for target in top_targets:
        username = target.get("username", "")
        if not username:
            continue
        fetched = False
        for instance in _NITTER_INSTANCES:
            try:
                rss_url = f"https://{instance}/{username}/rss"
                resp = session.get(rss_url, timeout=8)
                if resp.status_code != 200:
                    continue
                root = ET.fromstring(resp.content)
                count = 0
                for item in root.findall(".//item"):
                    if count >= tweets_per_user:
                        break
                    title = item.findtext("title", "").strip()
                    if "RT @" in title:
                        continue
                    if ": " in title:
                        title = title.split(": ", 1)[1]
                    if not title:
                        desc = item.findtext("description", "")
                        title = BeautifulSoup(desc, "html.parser").get_text(separator=" ", strip=True)
                    if len(title) > 30:
                        all_tweets.append(title)
                        count += 1
                if count > 0:
                    logger.info(f"Nitter ({instance}): {count} tweets from @{username}")
                    fetched = True
                    break
            except Exception as e:
                logger.warning(f"Nitter {instance} @{username}: {e}")
                continue
        if not fetched:
            logger.warning(f"Nitter: could not fetch @{username} from any instance")

    logger.info(f"Nitter total: {len(all_tweets)} tweets from {n_targets} target accounts")
    return all_tweets[:15]


def fetch_viral_context() -> list:
    """Niche RSS feed'lerinden trending başlıklar."""
    session = get_session()
    headlines = []
    for source_name, url in _RSS_SOURCES:
        try:
            resp = session.get(url, timeout=10)
            if resp.status_code != 200:
                logger.warning(f"RSS {source_name}: HTTP {resp.status_code}")
                continue
            root = ET.fromstring(resp.content)
            matched = 0
            for item in root.findall(".//item"):
                title_el = item.find("title")
                if title_el is None or not title_el.text:
                    continue
                title = title_el.text.strip()
                if len(title) > 15 and any(kw in title.lower() for kw in NICHE_KEYWORDS):
                    headlines.append(f"[{source_name}] {title}")
                    matched += 1
                    if matched >= 5:
                        break
            logger.info(f"RSS {source_name}: {matched} relevant headlines")
        except Exception as e:
            logger.warning(f"RSS {source_name} error: {e}")
    logger.info(f"Viral context total: {len(headlines)} headlines")
    return headlines[:15]


def fetch_target_tweets(n_targets: int = 10, max_results: int = 20) -> list:
    """Twitter API → Nitter RSS → RSS fallback."""
    try:
        with open("targets.json", "r") as f:
            targets = json.load(f)
        top_targets = sorted(targets, key=lambda t: t.get("engagement_score", 0), reverse=True)[:n_targets]
        usernames = [t["username"] for t in top_targets if t.get("username")]
        if usernames:
            client = get_twitter_client_with_bearer()  # Bearer token for search
            from_clause = " OR ".join(f"from:{u}" for u in usernames)
            resp = client.search_recent_tweets(
                query=f"({from_clause}) -is:retweet -is:reply lang:en",
                max_results=max_results,
                tweet_fields=["public_metrics", "text"],
                sort_order="relevancy",
            )
            if resp.data:
                # BUG FIX #14: public_metrics None guard — API bazen None döner
                tweets = sorted(
                    resp.data,
                    key=lambda t: (
                        (t.public_metrics or {}).get("like_count", 0)
                        + (t.public_metrics or {}).get("retweet_count", 0) * 3
                    ),
                    reverse=True,
                )
                results = [t.text for t in tweets[:5]]
                logger.info(f"Twitter API: {len(results)} tweets fetched.")
                return results
    except Exception as e:
        logger.warning(f"Twitter API error ({type(e).__name__}): {e}")

    nitter_results = fetch_target_tweets_nitter(n_targets)
    if nitter_results:
        return nitter_results

    logger.info("Nitter failed, fetching viral context from tech news RSS...")
    return fetch_viral_context()


def _load_scan_results(max_age_hours: int = 12) -> list:
    """scan_results.json'dan taze tarama sonuçlarını yükle (AŞAMA 1)."""
    scan_path = os.path.join(os.path.dirname(__file__), "..", "scan_results.json")
    try:
        with open(scan_path, "r", encoding="utf-8") as f:
            results = json.load(f)
        if not results:
            return []
        # Tazelik kontrolü: ilk kaydın fetched_at'ına bak
        from datetime import datetime, timezone, timedelta
        fetched_at = datetime.fromisoformat(results[0]["fetched_at"])
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - fetched_at > timedelta(hours=max_age_hours):
            logger.info("scan_results.json too old, will use live API search.")
            return []
        logger.info(f"Using scan_results.json: {len(results)} tweets (AŞAMA 1).")
        return results
    except Exception:
        return []


def fetch_target_tweets_with_ids(n_targets: int = 3, category: str = None) -> list:
    """Target tweet'leri id + text ile döndür (quote/RT için).

    Önce günlük tarama sonuçlarını (scan_results.json) dener (AŞAMA 1).
    Taze değilse Twitter API ile canlı arama yapar.

    category: scan_results'tan belirli bir kategoriden seç ("reply", "quote_rt", "rt", "like").
              None ise tüm kategorilerden rastgele seç.
    NOT: Reply modu için fetch_targets_for_reply() kullan — scan sonuçları yabancı hesaplar.
    """
    # AŞAMA 1: Önce günlük tarama sonuçlarını dene
    scan = _load_scan_results(max_age_hours=12)
    if scan:
        if category:
            pool = [r for r in scan if r.get("category") == category]
            if not pool:
                logger.info(f"No scan results for category='{category}', using full pool.")
                pool = scan
        else:
            pool = scan
        sample = random.sample(pool, min(n_targets, len(pool)))
        return [{"id": r["tweet_id"], "text": r["text"], "author": r["author"]} for r in sample]

    # Fallback: canlı API araması
    client = get_twitter_client_with_bearer()
    try:
        with open("targets.json", "r", encoding="utf-8") as f:
            targets = json.load(f)
    except Exception:
        return []

    tier1 = [t for t in targets if t.get("followers", 0) < 50_000]
    sample = random.sample(tier1, min(n_targets, len(tier1))) if tier1 else random.sample(targets, min(n_targets, len(targets)))

    results = []
    for target in sample:
        try:
            resp = client.search_recent_tweets(
                query=f"from:{target['username']} -is:retweet -is:reply lang:en",
                max_results=5,
                tweet_fields=["public_metrics", "text"],
                sort_order="relevancy",
            )
            if resp.data:
                # BUG FIX #14: public_metrics None guard
                best = max(
                    resp.data,
                    key=lambda t: (t.public_metrics or {}).get("like_count", 0) + (t.public_metrics or {}).get("retweet_count", 0) * 2,
                )
                results.append({"id": str(best.id), "text": best.text, "author": target["username"]})
        except Exception as e:
            logger.warning(f"fetch_target_tweets_with_ids error for @{target['username']}: {e}")
        time.sleep(1)

    return results


def fetch_targets_for_reply(n_targets: int = 10) -> list:
    """Reply modu için SADECE targets.json'dan tweet çek.

    Scan sonuçları yabancı hesaplardır — Twitter, follow etmediğin/etkileşim kurmadığın
    hesaplara cold-reply atmana izin vermez (403). Targets.json curated listedir.
    """
    client = get_twitter_client_with_bearer()
    try:
        with open("targets.json", "r", encoding="utf-8") as f:
            targets = json.load(f)
    except Exception as e:
        logger.error(f"targets.json load error: {e}")
        return []

    # Önce mid-tier (10k-50k), sonra genel — yanıt verme olasılığı yüksek
    tier1 = [t for t in targets if 1_000 <= t.get("followers", 0) <= 50_000]
    pool = tier1 if tier1 else targets
    sample = random.sample(pool, min(n_targets, len(pool)))

    results = []
    for target in sample:
        username = target.get("username", "")
        if not username:
            continue
        try:
            resp = client.search_recent_tweets(
                query=f"from:{username} -is:retweet -is:reply lang:en",
                max_results=5,
                tweet_fields=["public_metrics", "text", "reply_settings"],
                sort_order="relevancy",
            )
            if resp.data:
                # reply_settings=everyone olan tweet'leri filtrele
                open_tweets = [
                    t for t in resp.data
                    if getattr(t, "reply_settings", "everyone") == "everyone"
                ]
                candidates = open_tweets if open_tweets else resp.data
                # BUG FIX #14: public_metrics None guard
                best = max(
                    candidates,
                    key=lambda t: (t.public_metrics or {}).get("like_count", 0) + (t.public_metrics or {}).get("retweet_count", 0) * 2,
                )
                results.append({"id": str(best.id), "text": best.text, "author": username})
        except Exception as e:
            logger.warning(f"fetch_targets_for_reply error for @{username}: {e}")
        time.sleep(1)

    logger.info(f"fetch_targets_for_reply: {len(results)} candidates from targets.json")
    return results


def _post_thread_reply_safe(client, tweet_id: str, main_tweet_text: str) -> None:
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
        logger.warning("Viral mix thread reply failed — logged.")


def post_viral_mix_tweet():
    """Fetch top target tweets, mix with manifesto chunk, post one viral tweet."""
    client, _ = get_twitter_clients()
    db = _load_db()

    text_items = [
        item
        for category, items in db.items()
        if isinstance(items, list)
        for item in items
        if item.get("type") == "text" and len(item.get("content", "")) > 500
    ]
    if not text_items:
        logger.error("No text items in database, skipping viral mix.")
        return

    fresh_texts = [i for i in text_items if not tweet_archive.is_posted_recently(i["id"] + "_viral")]
    if not fresh_texts:
        logger.info("Archive: all text items used for viral mix recently, picking random anyway.")
        fresh_texts = text_items

    manifesto_item = random.choice(fresh_texts)
    content = manifesto_item.get("content", "")
    name    = manifesto_item.get("name", "")

    logger.info("Fetching top target tweets for viral mix...")
    target_tweets = fetch_target_tweets()
    if not target_tweets:
        logger.info("No target tweets available, generating from manifesto only...")

    # AŞAMA 2: Pattern extraction context
    try:
        from analytics import analyze_scan_patterns
        pattern_ctx = analyze_scan_patterns()
    except Exception:
        pattern_ctx = ""

    def _gen():
        words = content.split()
        chunk = content
        if len(words) > 100:
            start = random.randint(0, len(words) - 100)
            chunk = " ".join(words[start : start + 100])
        cand = generate_viral_mix_tweet(target_tweets, chunk, name, pattern_context=pattern_ctx)
        if len(cand) < 50:
            raise ValueError(f"tweet too short: {len(cand)} chars")
        return cand

    tweet_text = post_with_retry(_gen) if GROQ_API_KEY else None

    if not tweet_text:
        logger.warning("post_with_retry failed after all attempts — skipping viral_mix tweet to avoid context-less post.")
        return

    tweet_text = format_tweet(trim_for_format(tweet_text))
    logger.info(f"Posting viral mix tweet ({len(tweet_text)} chars): {tweet_text[:80]}...")

    archive_id = "viral_" + hashlib.md5(tweet_text.encode()).hexdigest()[:12]
    try:
        resp = client.create_tweet(text=tweet_text)
        tweet_id = resp.data["id"]
        tweet_archive.record_post(archive_id, content_type="viral_mix",
                                  tweet_text=tweet_text, tweet_id=tweet_id,
                                  weekly_theme=get_this_weeks_theme())
        tweet_archive.record_post(manifesto_item["id"] + "_viral", content_type="viral_mix_source",
                                  weekly_theme=get_this_weeks_theme())
        _post_thread_reply_safe(client, tweet_id, tweet_text)
        logger.info(f"Viral mix tweet posted: {tweet_text[:60]}...")
    except tweepy.errors.Forbidden as e:
        api_codes = getattr(e, "api_codes", [])
        if 187 in api_codes:
            logger.warning("Duplicate viral mix tweet, skipping.")
        else:
            tweet_archive.record_failed(archive_id, "viral_mix",
                                        tweet_text=tweet_text, error_msg=str(e),
                                        weekly_theme=get_this_weeks_theme())
            logger.error("Viral mix tweet failed (unhandled 403) — logged.")
