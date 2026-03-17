"""
daily_scan.py
-------------
AŞAMA 1: Günlük Tarama (d3c3ntr4l1z3_strategy.docx §03).

Niche sorgularla Twitter'ı tara, yüksek engagement tweet'leri bul,
scan_results.json'a kaydet. reply/quote/like/retweet modları bu sonuçları kullanır.

Kullanım:
    python daily_scan.py

Çıktı:
    scan_results.json — {tweet_id, text, author, engagement_score, fetched_at}
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

import tweepy

from utils.spam_filter import is_spam as _is_scam, is_off_topic as _is_off_topic

BEARER_TOKEN = os.environ.get("BEARERTOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
SCAN_PATH = os.path.join(os.path.dirname(__file__), "scan_results.json")

# Minimum engagement eşiği (likes + retweets*3)
_MIN_ENGAGEMENT = 10
# Her sorgu için max sonuç
_MAX_RESULTS = 20
# Kaydetmek istediğimiz toplam tweet sayısı
_SAVE_TOP = 30
# Kalite eşiği — min bu kadar tweet bulunmazsa fallback eşiği devreye girer
_QUALITY_MIN = 7.0
_QUALITY_FALLBACK = 5.0
_MIN_QUALITY_COUNT = 15  # bu kadar tweet bulunamazsa fallback devreye girer

_QUERIES = [
    # Ultra-spesifik: WebXR / OpenXR teknik içerik
    "webxr -is:retweet -is:reply lang:en",
    # Spatial computing / immersive web teknik terimler
    "(#SpatialComputing OR openxr OR #ImmersiveWeb OR #3DGS) -is:retweet -is:reply lang:en",
    # VR/XR — sadece geliştirici/teknik bağlamda (eğlence/spor değil)
    "(#VirtualReality OR #MixedReality OR #XR) (developer OR SDK OR browser OR scene OR spatial OR research) -is:retweet -is:reply lang:en",
    # WebXR + metaverse teknik — kripto/NFT hariç
    "(#WebXR OR immersive-web OR gaussian-splat) -(NFT OR memecoin OR airdrop OR token OR coin) -is:retweet -is:reply lang:en",
]

# Yeterli kaliteli tweet bulunamazsa bu ek sorgular denenir
_EXTENDED_QUERIES = [
    "(volumetric OR haptic OR holographic OR openxr) -is:retweet -is:reply lang:en",
    "(spatial-audio OR spatial-computing OR webgl) developer -is:retweet -is:reply lang:en",
]


# Scam/spam filtresi utils/spam_filter.py'da merkezi olarak tanımlıdır.
# _is_scam = utils.spam_filter.is_spam (yukarıda import edildi)


def _engagement(tweet) -> int:
    m = getattr(tweet, "public_metrics", {}) or {}
    if hasattr(m, "get"):
        return m.get("like_count", 0) + m.get("retweet_count", 0) * 3
    return 0


def _score_quality(text: str) -> float:
    """Tweet kalitesini LLM ile puanla (0-10). GROQ key yoksa geç (9.0 döner)."""
    if not GROQ_API_KEY:
        return 9.0
    try:
        from core.llm import score_tweet_quality
        return score_tweet_quality(text)
    except Exception as e:
        print(f"  quality score error: {e}")
        return 5.0


def _fetch_query(client, query: str, seen_ids: set) -> list:
    """Tek sorgu çalıştır, spam filtrele, sonuçları döndür."""
    results = []
    try:
        resp = client.search_recent_tweets(
            query=query,
            max_results=_MAX_RESULTS,
            tweet_fields=["public_metrics", "text", "author_id", "reply_settings"],
            expansions=["author_id"],
            user_fields=["username"],
            sort_order="relevancy",
        )
        if not resp.data:
            print(f"Query returned no results: {query[:60]}...")
            return results

        users = {u.id: u.username for u in (resp.includes.get("users") or [])}

        for tweet in resp.data:
            if tweet.id in seen_ids:
                continue
            eng = _engagement(tweet)
            if eng < _MIN_ENGAGEMENT:
                continue
            if _is_scam(tweet.text):
                print(f"  SCAM filtered: {tweet.text[:60]}...")
                continue
            if _is_off_topic(tweet.text):
                print(f"  OFF-TOPIC filtered: {tweet.text[:60]}...")
                continue
            reply_settings = getattr(tweet, "reply_settings", "everyone") or "everyone"
            results.append({
                "tweet_id": str(tweet.id),
                "text": tweet.text,
                "author": users.get(tweet.author_id, "unknown"),
                "engagement_score": eng,
                "reply_settings": reply_settings,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            })
            seen_ids.add(tweet.id)
        print(f"Query OK: {len(resp.data)} results ({len(results)} passed spam) — {query[:50]}...")
    except Exception as e:
        print(f"Query failed: {e} — {query[:50]}...")
    return results


def _apply_quality_filter(tweets: list, threshold: float) -> list:
    """Kalite skoru threshold üstündekileri döndür."""
    if not GROQ_API_KEY:
        print("GROQ_API_KEY not set — quality filter skipped, spam filter only.")
        return tweets
    passed = []
    for t in tweets:
        score = _score_quality(t["text"])
        if score >= threshold:
            t["quality_score"] = round(score, 1)
            passed.append(t)
        else:
            print(f"  QUALITY {score:.1f}/10 filtered: @{t['author']} {t['text'][:50]}...")
    return passed


def run_daily_scan() -> list:
    if not BEARER_TOKEN:
        print("ERROR: BEARERTOKEN not set — skipping daily scan.")
        sys.exit(1)

    client = tweepy.Client(bearer_token=BEARER_TOKEN, wait_on_rate_limit=True)
    all_tweets = []
    seen_ids = set()

    # Ana sorgular
    for query in _QUERIES:
        all_tweets.extend(_fetch_query(client, query, seen_ids))
        time.sleep(2)

    # Engagement'a göre sırala, kalite skoru için top N'i tut
    all_tweets.sort(key=lambda t: t["engagement_score"], reverse=True)
    candidates = all_tweets[:_SAVE_TOP * 2]  # Fazlasını skora sok, elenenler telafi edilir

    # Kalite filtresi (IQ >= 9 beklentisi → min_score >= 7.0 ile karşılanır)
    print(f"\nScoring quality for {len(candidates)} candidates (threshold={_QUALITY_MIN})...")
    quality_passed = _apply_quality_filter(candidates, _QUALITY_MIN)

    # Yeterli tweet bulunamadıysa ek sorgular dene
    if len(quality_passed) < _MIN_QUALITY_COUNT and _EXTENDED_QUERIES:
        print(f"\nOnly {len(quality_passed)} tweets passed quality — running extended queries...")
        for query in _EXTENDED_QUERIES:
            all_tweets.extend(_fetch_query(client, query, seen_ids))
            time.sleep(2)
        # Yeni gelenleri fallback eşiğiyle ekle
        new_candidates = [t for t in all_tweets if t["tweet_id"] not in {x["tweet_id"] for x in quality_passed}]
        new_candidates.sort(key=lambda t: t["engagement_score"], reverse=True)
        print(f"Fallback quality scoring (threshold={_QUALITY_FALLBACK}) for {len(new_candidates)} new candidates...")
        fallback_passed = _apply_quality_filter(new_candidates[:_SAVE_TOP], _QUALITY_FALLBACK)
        quality_passed.extend(fallback_passed)

    # Son sıralama ve kayıt
    quality_passed.sort(key=lambda t: t["engagement_score"], reverse=True)
    top = quality_passed[:_SAVE_TOP]

    with open(SCAN_PATH, "w", encoding="utf-8") as f:
        json.dump(top, f, indent=2, ensure_ascii=False)

    print(f"\nDaily scan complete: {len(top)} tweets saved to scan_results.json")
    return top


if __name__ == "__main__":
    results = run_daily_scan()
    print(f"Top tweet: {results[0]['text'][:80] if results else 'none'}...")
