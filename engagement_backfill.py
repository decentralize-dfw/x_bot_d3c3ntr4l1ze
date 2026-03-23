"""
engagement_backfill.py
-----------------------
AŞAMA 4: Ölçüm & Ayarlama (d3c3ntr4l1z3_strategy.docx §03).

Son 7 günün tweet'lerinin gerçek engagement metriklerini Twitter API'den çek,
tweet_archive.json'a yaz. analytics.py bunu okuyarak gerçek veri gösterir.

Kullanım:
    python engagement_backfill.py

Her Pazar 08:00 UTC çalışır (GitHub Actions).
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

import tweepy

# Kendi modüllerimiz
sys.path.insert(0, os.path.dirname(__file__))
import tweet_archive

TWITTER_API_KEY             = os.environ.get("TWITTER_API_KEY")
TWITTER_API_SECRET          = os.environ.get("TWITTER_API_SECRET")
TWITTER_ACCESS_TOKEN        = os.environ.get("TWITTER_ACCESS_TOKEN")
TWITTER_ACCESS_TOKEN_SECRET = os.environ.get("TWITTER_ACCESS_TOKEN_SECRET")
BEARER_TOKEN                = os.environ.get("BEARERTOKEN")


def run_backfill(days: int = 7) -> None:
    """Son N günün tweet'lerini bul, engagement metriklerini çek, archive'ı güncelle."""
    unscored = tweet_archive.get_unscored_tweets(days=days)
    if not unscored:
        print(f"No unscored tweets in last {days} days — nothing to backfill.")
        return

    print(f"Backfilling {len(unscored)} tweets...")

    client = tweepy.Client(
        bearer_token=BEARER_TOKEN,
        consumer_key=TWITTER_API_KEY,
        consumer_secret=TWITTER_API_SECRET,
        access_token=TWITTER_ACCESS_TOKEN,
        access_token_secret=TWITTER_ACCESS_TOKEN_SECRET,
        wait_on_rate_limit=True,
    )

    # tweet_id → archive entry map
    entries = tweet_archive.load_archive()
    id_to_entry = {e.get("tweet_id"): e for e in entries if e.get("tweet_id")}

    updated = 0
    for record in unscored:
        tweet_id = record.get("tweet_id")
        if not tweet_id:
            continue

        try:
            resp = client.get_tweet(
                tweet_id,
                tweet_fields=["public_metrics"],
            )
            if not resp.data:
                continue

            m = resp.data.public_metrics or {}
            likes     = m.get("like_count", 0)
            retweets  = m.get("retweet_count", 0)
            replies   = m.get("reply_count", 0)
            impressions = m.get("impression_count", 0)
            score = likes + retweets * 3 + replies * 2

            if tweet_id in id_to_entry:
                id_to_entry[tweet_id]["likes"]           = likes
                id_to_entry[tweet_id]["retweets"]        = retweets
                id_to_entry[tweet_id]["replies"]         = replies
                id_to_entry[tweet_id]["impressions"]     = impressions
                id_to_entry[tweet_id]["engagement_score"] = score
                updated += 1
                print(f"  tweet {tweet_id}: likes={likes} RT={retweets} replies={replies} score={score}")

        except Exception as e:
            print(f"  tweet {tweet_id}: fetch failed — {e}")

        time.sleep(1)  # rate limit koruması

    if updated > 0:
        tweet_archive.save_archive(list(id_to_entry.values()) +
                                   [e for e in entries if not e.get("tweet_id")])
        print(f"Backfill complete: {updated}/{len(unscored)} tweets updated.")
    else:
        print("Backfill: no tweets could be updated.")


def print_weekly_report() -> None:
    """Haftalık engagement özeti — analytics.py gerçek veriyle rapor eder.
    Sonuçları analytics_weekly.json'a da yazar (git'e commit edilir).
    """
    from collections import defaultdict
    from analytics import load_analytics

    records = load_analytics()
    if not records:
        print("No analytics data yet.")
        return

    scored = [r for r in records if r.get("engagement_score", 0) > 0]
    if not scored:
        print("No engagement data yet (API backfill needed).")
        return

    print("\n=== WEEKLY ENGAGEMENT REPORT ===")
    print(f"Records with real data: {len(scored)}/{len(records)}")

    # En iyi content type
    by_type: dict = defaultdict(list)
    for r in scored:
        by_type[r.get("content_type", "unknown")].append(r.get("engagement_score", 0))
    sorted_by_type = sorted(by_type.items(), key=lambda x: -sum(x[1]) / len(x[1]))
    print("\nEngagement by content type:")
    for ct, scores in sorted_by_type:
        avg = sum(scores) / len(scores)
        print(f"  {ct:<25} avg={avg:.1f}  n={len(scores)}")

    # En iyi 3 tweet
    top3 = sorted(scored, key=lambda r: r.get("engagement_score", 0), reverse=True)[:3]
    print("\nTop 3 tweets:")
    for r in top3:
        print(f"  [{r.get('engagement_score',0):3d}] {r.get('tweet_text','')[:70]}")
    print()

    # JSON dosyasına kaydet — analytics_weekly.json git'e commit edilir
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_records": len(records),
        "scored_records": len(scored),
        "by_type": {
            ct: {"avg": round(sum(s) / len(s), 1), "n": len(s)}
            for ct, s in sorted_by_type
        },
        "top3": [
            {
                "score": r.get("engagement_score", 0),
                "text": r.get("tweet_text", "")[:120],
                "tweet_id": r.get("tweet_id"),
                "content_type": r.get("content_type"),
            }
            for r in top3
        ],
    }
    with open("analytics_weekly.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print("Weekly report saved → analytics_weekly.json")


if __name__ == "__main__":
    run_backfill(days=7)
    print_weekly_report()
