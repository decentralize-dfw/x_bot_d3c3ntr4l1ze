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

BEARER_TOKEN = os.environ.get("BEARERTOKEN")
SCAN_PATH = os.path.join(os.path.dirname(__file__), "scan_results.json")

# Minimum engagement eşiği (likes + retweets*3)
_MIN_ENGAGEMENT = 10
# Her sorgu için max sonuç
_MAX_RESULTS = 20
# Kaydetmek istediğimiz toplam tweet sayısı
_SAVE_TOP = 30

_QUERIES = [
    "(metaverse OR webxr OR #SpatialComputing) -is:retweet -is:reply lang:en",
    "(#VirtualReality OR #VR OR #XR OR #ImmersiveWeb) -is:retweet -is:reply lang:en",
    "(web3 OR #OnChain OR #DigitalArt OR #NFT) -is:retweet -is:reply lang:en",
    "(#SpatialComputing OR #AI #Metaverse OR #WebXR) -is:retweet -is:reply lang:en",
]


def _engagement(tweet) -> int:
    m = getattr(tweet, "public_metrics", {}) or {}
    if hasattr(m, "get"):
        return m.get("like_count", 0) + m.get("retweet_count", 0) * 3
    return 0


def run_daily_scan() -> list:
    if not BEARER_TOKEN:
        print("ERROR: BEARERTOKEN not set — skipping daily scan.")
        sys.exit(1)

    client = tweepy.Client(bearer_token=BEARER_TOKEN, wait_on_rate_limit=True)
    all_tweets = []
    seen_ids = set()

    for query in _QUERIES:
        try:
            resp = client.search_recent_tweets(
                query=query,
                max_results=_MAX_RESULTS,
                tweet_fields=["public_metrics", "text", "author_id"],
                expansions=["author_id"],
                user_fields=["username"],
                sort_order="relevancy",
            )
            if not resp.data:
                print(f"Query returned no results: {query[:60]}...")
                continue

            # username map
            users = {u.id: u.username for u in (resp.includes.get("users") or [])}

            for tweet in resp.data:
                if tweet.id in seen_ids:
                    continue
                eng = _engagement(tweet)
                if eng < _MIN_ENGAGEMENT:
                    continue
                all_tweets.append({
                    "tweet_id": str(tweet.id),
                    "text": tweet.text,
                    "author": users.get(tweet.author_id, "unknown"),
                    "engagement_score": eng,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                })
                seen_ids.add(tweet.id)
            print(f"Query OK: {len(resp.data)} results — {query[:50]}...")
        except Exception as e:
            print(f"Query failed: {e} — {query[:50]}...")
        time.sleep(2)

    # Engagement'a göre sırala, top N'i sakla
    all_tweets.sort(key=lambda t: t["engagement_score"], reverse=True)
    top = all_tweets[:_SAVE_TOP]

    with open(SCAN_PATH, "w", encoding="utf-8") as f:
        json.dump(top, f, indent=2, ensure_ascii=False)

    print(f"Daily scan complete: {len(top)} tweets saved to scan_results.json")
    return top


if __name__ == "__main__":
    results = run_daily_scan()
    print(f"Top tweet: {results[0]['text'][:80] if results else 'none'}...")
