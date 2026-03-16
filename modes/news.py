"""
modes/news.py
-------------
Haber modları: Decrypt.co + VentureBeat RSS tweet thread'leri.
"""
import hashlib
import os

from bs4 import BeautifulSoup

import tweet_archive
from core.llm import generate_news_headline, generate_news_tweet
from core.rss import _parse_rss
from core.twitter import get_twitter_clients
from core.voice import get_this_weeks_theme
from utils.http import get_session
from utils.logger import get_logger

logger = get_logger(__name__)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

_RSS_FEEDS = {
    "decrypt.co":      "https://decrypt.co/feed/",
    "venturebeat.com": "https://venturebeat.com/feed/",
    "roadtovr.com":    "https://www.roadtovr.com/feed/",
    "awwwards.com":    "https://www.awwwards.com/blog/rss",
    "webxr.news":      "https://webxr.news/rss",
    "sketchfab.com":   "https://sketchfab.com/blogs/community/feed",
}


def _scrape_article_body(article_url: str, source_name: str) -> str:
    session = get_session()
    try:
        resp = session.get(article_url, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"{source_name} article fetch HTTP {resp.status_code}: {article_url}")
            return ""
        soup = BeautifulSoup(resp.content, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        import re
        body_tag = (
            soup.find("article")
            or soup.find("div", class_=re.compile(r"(article|post|entry|content|story)[-_]?(body|text|content)?", re.I))
        )
        if body_tag:
            paragraphs = [p.get_text(separator=" ", strip=True) for p in body_tag.find_all("p") if len(p.get_text(strip=True)) > 50]
        else:
            paragraphs = [p.get_text(separator=" ", strip=True) for p in soup.find_all("p") if len(p.get_text(strip=True)) > 80]
        return " ".join(paragraphs)[:2500]
    except Exception as e:
        logger.error(f"{source_name} article scrape error: {e}")
        return ""


def _fetch_article_content(site_url: str, source_name: str):
    """RSS → article scrape → homepage fallback."""
    rss_url = _RSS_FEEDS.get(source_name)
    if rss_url:
        logger.info(f"{source_name}: parsing RSS feed {rss_url}")
        rss_result = _parse_rss(rss_url, source_name)
        if rss_result:
            title, article_url, body_text = rss_result
            if len(body_text) < 300 and article_url:
                logger.info(f"{source_name}: RSS body short ({len(body_text)} chars), scraping article...")
                scraped = _scrape_article_body(article_url, source_name)
                if scraped:
                    body_text = scraped
            if body_text:
                logger.info(f"{source_name} RSS article: '{title}' ({len(body_text)} chars)")
                return title, body_text[:2500]

    # Fallback: homepage HTML scraping
    logger.info(f"{source_name}: falling back to homepage HTML scraping...")
    session = get_session()
    try:
        resp = session.get(site_url, timeout=15)
        if resp.status_code != 200:
            logger.error(f"{source_name} homepage fetch failed: {resp.status_code}")
            return None
        soup = BeautifulSoup(resp.content, "html.parser")
        article_url = None
        base = site_url.rstrip("/")
        for tag in soup.find_all(["h1", "h2", "h3", "article"]):
            a = tag.find("a", href=True)
            if a:
                href = a["href"]
                if href.startswith("http"):
                    article_url = href
                elif href.startswith("/"):
                    article_url = base + href
                if article_url:
                    break
        if not article_url:
            logger.error(f"{source_name}: no article link found on homepage.")
            return None
        full_text = _scrape_article_body(article_url, source_name)
        title = ""
        try:
            art_resp = session.get(article_url, timeout=15)
            if art_resp.status_code == 200:
                art_soup = BeautifulSoup(art_resp.content, "html.parser")
                og = art_soup.find("meta", property="og:title")
                if og and og.get("content"):
                    title = og["content"].strip()
                elif art_soup.find("h1"):
                    title = art_soup.find("h1").get_text(strip=True)
        except Exception:
            pass
        if not full_text:
            logger.error(f"{source_name}: no article text found.")
            return None
        return title, full_text
    except Exception as e:
        logger.error(f"{source_name} fallback fetch error: {e}")
        return None


def _get_hashtags_for_source(source_name: str) -> str:
    if "decrypt" in source_name:
        return "#web3 #crypto"
    return "#metaverse #web3"


def _get_prior_opinions_for_topic(title: str, n: int = 3) -> list:
    topic_words = set(w.lower() for w in title.split() if len(w) > 4)
    recent = tweet_archive.get_recent_tweet_texts(days=30)
    matches = []
    for t in reversed(recent):
        tweet_words = set(w.lower() for w in t.split())
        if len(topic_words & tweet_words) >= 2:
            matches.append(t)
        if len(matches) >= n:
            break
    return matches


def _post_news_tweet(site_url: str, source_name: str):
    if not GROQ_API_KEY:
        logger.warning(f"GROQ_API_KEY not set, skipping {source_name} tweet.")
        return
    client, _ = get_twitter_clients()
    result = _fetch_article_content(site_url, source_name)
    if not result:
        logger.warning(f"No article content from {source_name}, skipping.")
        return
    title, article_text = result
    logger.info(f"{source_name} title: {title}")

    article_id = "news_" + hashlib.md5(title.encode()).hexdigest()[:12]
    if tweet_archive.is_posted_recently(article_id):
        logger.info(f"Archive: article '{title[:60]}' already posted in last 60 days, skipping.")
        return

    try:
        headline = generate_news_headline(title, article_text, source_name)
    except Exception as e:
        logger.error(f"Headline generation error: {e}")
        headline = title[:115] if title else "breaking news in the space"

    tweet1 = f"NEWS: {headline}"
    if len(tweet1) > 140:
        tweet1 = tweet1[:137].rsplit(" ", 1)[0] + "..."

    logger.info(f"Posting {source_name} tweet 1 ({len(tweet1)} chars): {tweet1}")
    try:
        resp = client.create_tweet(text=tweet1)
        tweet1_id = resp.data["id"]
        tweet_archive.record_post(article_id, content_type="news",
                                  tweet_text=tweet1, tweet_id=tweet1_id,
                                  weekly_theme=get_this_weeks_theme())
    except Exception as e:
        logger.error(f"{source_name} tweet 1 post error: {e}")
        return

    prior_opinions = _get_prior_opinions_for_topic(title)
    if prior_opinions:
        logger.info(f"  Opinion evolution: {len(prior_opinions)} prior opinion(s) found.")
    try:
        commentary = generate_news_tweet(title, article_text, source_name, prior_opinions=prior_opinions)
    except Exception as e:
        logger.error(f"Commentary generation error: {e}")
        commentary = "nobody saw this coming"

    hashtags = _get_hashtags_for_source(source_name)
    tweet2 = f"{commentary} {hashtags}".strip()
    if len(tweet2) > 140:
        tweet2 = commentary[:140]

    logger.info(f"Posting {source_name} tweet 2 ({len(tweet2)} chars): {tweet2}")
    try:
        resp2 = client.create_tweet(text=tweet2, in_reply_to_tweet_id=tweet1_id)
        tweet_archive.record_post(article_id + "_reply", content_type="news_reply",
                                  tweet_text=tweet2, tweet_id=resp2.data["id"],
                                  weekly_theme=get_this_weeks_theme())
        logger.info(f"{source_name} thread posted.")
    except Exception as e:
        logger.error(f"{source_name} tweet 2 post error: {e}")


def post_decrypt_tweet():
    _post_news_tweet("https://decrypt.co", "decrypt.co")


def post_venturebeat_tweet():
    _post_news_tweet("https://venturebeat.com", "venturebeat.com")
