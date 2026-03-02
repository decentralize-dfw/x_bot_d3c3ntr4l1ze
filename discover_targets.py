"""
discover_targets.py
-------------------
Niche'teki thought leader'ları bulur, engagement + follower'a göre sıralar,
targets.json'a kaydeder. Bot bu listeyi WATCH_ACCOUNTS olarak kullanır.

Çalıştırma:
  python discover_targets.py

GitHub Actions (discover.yml) haftada bir otomatik çalıştırır.
"""

import os
import json
import time
import tweepy
from datetime import datetime, timezone

TWITTER_BEARER_TOKEN        = os.environ.get("BEARERTOKEN")
TWITTER_API_KEY             = os.environ.get("TWITTER_API_KEY")
TWITTER_API_SECRET          = os.environ.get("TWITTER_API_SECRET")
TWITTER_ACCESS_TOKEN        = os.environ.get("TWITTER_ACCESS_TOKEN")
TWITTER_ACCESS_TOKEN_SECRET = os.environ.get("TWITTER_ACCESS_TOKEN_SECRET")

# Kaç kişilik liste tutulsun
MAX_TARGETS = 60

# Minimum follower eşiği (bot hesaplarını, çok küçükleri elemeye yarar)
MIN_FOLLOWERS = 500

# Niche sorgu seti — her biri ayrı çekilir, sonuçlar birleştirilir
NICHE_QUERIES = [
    '(metaverse OR "virtual world" OR "spatial web") lang:en -is:retweet min_faves:15',
    '("three.js" OR "threejs" OR "WebGL" OR "3D web") lang:en -is:retweet min_faves:10',
    '("digital art" OR "generative art" OR "new media art") lang:en -is:retweet min_faves:20',
    '("on-chain" OR "crypto art" OR "NFT art" OR "digital collectible") lang:en -is:retweet min_faves:15',
    '("spatial computing" OR "extended reality" OR "XR design") lang:en -is:retweet min_faves:10',
    '("virtual architecture" OR "metaverse design" OR "web3 design") lang:en -is:retweet min_faves:10',
]


def get_client():
    return tweepy.Client(
        bearer_token=TWITTER_BEARER_TOKEN,
        consumer_key=TWITTER_API_KEY,
        consumer_secret=TWITTER_API_SECRET,
        access_token=TWITTER_ACCESS_TOKEN,
        access_token_secret=TWITTER_ACCESS_TOKEN_SECRET,
    )


def discover():
    client = get_client()
    author_data = {}  # username -> {score, count, user_obj}

    for i, query in enumerate(NICHE_QUERIES):
        print(f"[{i+1}/{len(NICHE_QUERIES)}] Searching: {query[:60]}...")
        try:
            resp = client.search_recent_tweets(
                query=query,
                max_results=100,
                tweet_fields=["author_id", "public_metrics"],
                expansions=["author_id"],
                user_fields=["username", "name", "public_metrics", "description"],
            )
            if not resp.data:
                print("  → No results.")
                time.sleep(2)
                continue

            # Build id → user map from expansions
            users_by_id = {}
            if resp.includes and resp.includes.get("users"):
                for u in resp.includes["users"]:
                    users_by_id[u.id] = u

            for tweet in resp.data:
                user = users_by_id.get(tweet.author_id)
                if not user:
                    continue
                m = tweet.public_metrics or {}
                score = m.get("like_count", 0) + m.get("retweet_count", 0) * 3

                uname = user.username.lower()
                if uname not in author_data:
                    author_data[uname] = {"score": 0, "count": 0, "user": user}
                author_data[uname]["score"] += score
                author_data[uname]["count"] += 1

            print(f"  → {len(resp.data)} tweets, {len(users_by_id)} authors collected.")

        except tweepy.errors.TooManyRequests:
            print("  → Rate limit hit, waiting 60s...")
            time.sleep(60)
        except Exception as e:
            print(f"  → Error: {e}")

        time.sleep(3)  # rate limit koruması

    # Filter + rank
    candidates = []
    for uname, data in author_data.items():
        user = data["user"]
        pm = user.public_metrics or {}
        followers = pm.get("followers_count", pm.get("follower_count", 0))
        if followers < MIN_FOLLOWERS:
            continue
        candidates.append({
            "username": uname,
            "name": user.name,
            "followers": followers,
            "bio": (user.description or "")[:200],
            "engagement_score": data["score"],
            "tweet_samples": data["count"],
            "discovered": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        })

    candidates.sort(
        key=lambda x: (x["engagement_score"] * 0.7 + x["followers"] * 0.001),
        reverse=True
    )
    top_new = candidates[:MAX_TARGETS]

    # Merge with existing targets.json (preserve manually added entries)
    existing = []
    try:
        with open("targets.json", "r", encoding="utf-8") as f:
            existing = json.load(f)
        print(f"\nExisting targets: {len(existing)}")
    except FileNotFoundError:
        pass

    existing_map = {t["username"]: t for t in existing}
    for t in top_new:
        if t["username"] not in existing_map:
            existing_map[t["username"]] = t
        else:
            # Update engagement score and follower count, keep discovery date
            existing_map[t["username"]]["engagement_score"] = t["engagement_score"]
            existing_map[t["username"]]["followers"] = t["followers"]

    merged = sorted(
        existing_map.values(),
        key=lambda x: (x.get("engagement_score", 0) * 0.7 + x.get("followers", 0) * 0.001),
        reverse=True
    )[:MAX_TARGETS]

    with open("targets.json", "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)

    print(f"\nTop targets saved ({len(merged)} total):")
    for t in merged[:15]:
        print(f"  @{t['username']:<25} {t['followers']:>7,} followers   score {t['engagement_score']:>5}")


if __name__ == "__main__":
    discover()
