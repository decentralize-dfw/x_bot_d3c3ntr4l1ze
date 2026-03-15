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
import sys
import json
import time
import tweepy
from datetime import datetime, timezone

TWITTER_BEARER_TOKEN        = os.environ.get("BEARERTOKEN")
TWITTER_API_KEY             = os.environ.get("TWITTER_API_KEY")
TWITTER_API_SECRET          = os.environ.get("TWITTER_API_SECRET")
TWITTER_ACCESS_TOKEN        = os.environ.get("TWITTER_ACCESS_TOKEN")
TWITTER_ACCESS_TOKEN_SECRET = os.environ.get("TWITTER_ACCESS_TOKEN_SECRET")

MAX_TARGETS   = 60
MIN_FOLLOWERS = 500   # Minimum niche relevance için yükseltildi
MAX_FOLLOWERS = 600_000  # Mega-account'ları filtrele (niche değil)

# min_faves kaldırıldı — basic/free tier'da desteklenmiyor
# max_results 10 yapıldı — basic tier limiti
NICHE_QUERIES = [
    '(metaverse OR "virtual world" OR "spatial web") lang:en -is:retweet',
    '("three.js" OR "threejs" OR "WebGL" OR "3D web") lang:en -is:retweet',
    '("digital art" OR "generative art" OR "new media art") lang:en -is:retweet',
    '("on-chain" OR "crypto art" OR "NFT art" OR "digital collectible") lang:en -is:retweet',
    '("spatial computing" OR "extended reality" OR "XR design") lang:en -is:retweet',
    '("virtual architecture" OR "metaverse design" OR "web3 design") lang:en -is:retweet',
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
    # Başlangıç tanılaması
    print(f"Bearer token set: {'YES' if TWITTER_BEARER_TOKEN else 'NO — will fail!'}")
    print(f"API key set:      {'YES' if TWITTER_API_KEY else 'NO'}")

    client = get_client()
    author_data = {}
    query_errors = 0

    for i, query in enumerate(NICHE_QUERIES):
        print(f"\n[{i+1}/{len(NICHE_QUERIES)}] {query[:70]}")
        try:
            resp = client.search_recent_tweets(
                query=query,
                max_results=10,
                tweet_fields=["author_id", "public_metrics"],
                expansions=["author_id"],
                user_fields=["username", "name", "public_metrics", "description"],
            )

            if not resp.data:
                print("  → No results.")
                time.sleep(2)
                continue

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

            print(f"  → {len(resp.data)} tweets, {len(users_by_id)} authors.")

        except tweepy.errors.TooManyRequests as e:
            print(f"  → Rate limit hit: {e}. Waiting 60s...")
            time.sleep(60)
            query_errors += 1
        except tweepy.errors.Forbidden as e:
            print(f"  → 403 Forbidden: {e}")
            print("     (API tier may not support search — check your Twitter Developer Portal)")
            query_errors += 1
        except tweepy.errors.Unauthorized as e:
            print(f"  → 401 Unauthorized: {e}")
            print("     (Check BEARERTOKEN secret)")
            query_errors += 1
        except Exception as e:
            print(f"  → Unexpected error [{type(e).__name__}]: {e}")
            query_errors += 1

        time.sleep(3)

    print(f"\n--- Summary: {len(author_data)} unique authors found, {query_errors}/{len(NICHE_QUERIES)} queries errored ---")

    if not author_data:
        print("ERROR: 0 authors collected. Aborting without touching targets.json.")
        sys.exit(1)  # Job fail olsun, sessiz geçmesin

    # Filter + rank
    candidates = []
    for uname, data in author_data.items():
        user = data["user"]
        pm = user.public_metrics or {}
        followers = pm.get("followers_count", pm.get("follower_count", 0))
        print(f"  candidate @{uname}: {followers} followers")
        if followers < MIN_FOLLOWERS or followers > MAX_FOLLOWERS:
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

    # Engagement rate (orana dayalı, mutlak değere değil) bazlı sıralama
    # engagement_rate = tweet başına ortalama etkileşim / takipçi sayısı
    for c in candidates:
        followers = max(c["followers"], 1)
        per_tweet = c["engagement_score"] / max(c["tweet_samples"], 1)
        c["engagement_rate"] = per_tweet / followers  # normalize edilmiş oran
        # Niche relevance: bio'da niche keyword varsa bonus
        niche_terms = ["xr", "webxr", "metaverse", "spatial", "3d", "virtual", "web3",
                       "nft", "on-chain", "onchain", "digital art", "generative", "glb"]
        bio_lower = c["bio"].lower()
        c["niche_score"] = sum(1 for t in niche_terms if t in bio_lower) / len(niche_terms)

    candidates.sort(
        key=lambda x: (x["engagement_rate"] * 0.6 + x["niche_score"] * 0.4),
        reverse=True
    )
    top_new = candidates[:MAX_TARGETS]
    print(f"{len(top_new)} candidates passed filters (MIN={MIN_FOLLOWERS}, MAX={MAX_FOLLOWERS} followers).")

    # Merge with existing targets.json
    existing = []
    try:
        with open("targets.json", "r", encoding="utf-8") as f:
            existing = json.load(f)
        print(f"Existing targets loaded: {len(existing)}")
    except FileNotFoundError:
        pass

    existing_map = {t["username"]: t for t in existing}
    for t in top_new:
        if t["username"] not in existing_map:
            existing_map[t["username"]] = t
        else:
            existing_map[t["username"]]["engagement_score"] = t["engagement_score"]
            existing_map[t["username"]]["followers"] = t["followers"]

    merged = sorted(
        existing_map.values(),
        key=lambda x: (x.get("engagement_rate", 0) * 0.6 + x.get("niche_score", 0) * 0.4),
        reverse=True
    )[:MAX_TARGETS]

    with open("targets.json", "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)

    print(f"\nSaved {len(merged)} targets to targets.json:")
    for t in merged[:15]:
        print(f"  @{t['username']:<25} {t['followers']:>7,} followers   score {t['engagement_score']:>5}")


if __name__ == "__main__":
    discover()
