"""
analytics.py
------------
Tweet metriklerini çeker, en iyi içerik tipini ve posting saatini hesaplar.
GitHub Actions analytics.yml tarafından haftada bir çalıştırılır.

Kullanım:
  python analytics.py          → metrikleri güncelle + rapor yaz
  python analytics.py report   → sadece mevcut raporu yazdır
"""

import os
import sys
import json
from datetime import datetime, timedelta, timezone

import tweepy

ANALYTICS_PATH = os.path.join(os.path.dirname(__file__), "tweet_analytics.json")
ARCHIVE_PATH   = os.path.join(os.path.dirname(__file__), "tweet_archive.json")

TWITTER_API_KEY             = os.environ.get("TWITTER_API_KEY")
TWITTER_API_SECRET          = os.environ.get("TWITTER_API_SECRET")
TWITTER_ACCESS_TOKEN        = os.environ.get("TWITTER_ACCESS_TOKEN")
TWITTER_ACCESS_TOKEN_SECRET = os.environ.get("TWITTER_ACCESS_TOKEN_SECRET")


# ── Yardımcılar ───────────────────────────────────────────────────────────────

def get_client():
    return tweepy.Client(
        consumer_key=TWITTER_API_KEY,
        consumer_secret=TWITTER_API_SECRET,
        access_token=TWITTER_ACCESS_TOKEN,
        access_token_secret=TWITTER_ACCESS_TOKEN_SECRET,
    )


def load_analytics() -> list[dict]:
    if not os.path.exists(ANALYTICS_PATH):
        return []
    with open(ANALYTICS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_analytics(records: list[dict]):
    with open(ANALYTICS_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)


def load_archive() -> list[dict]:
    if not os.path.exists(ARCHIVE_PATH):
        return []
    with open(ARCHIVE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ── Metrik çekme ──────────────────────────────────────────────────────────────

def fetch_metrics_for_tweet(client, tweet_id: str) -> dict | None:
    """Tek bir tweet'in engagement metriklerini Twitter API'dan çek."""
    try:
        resp = client.get_tweet(
            tweet_id,
            tweet_fields=["public_metrics", "created_at"],
        )
        if not resp.data:
            return None
        m = resp.data.public_metrics or {}
        return {
            "likes":       m.get("like_count", 0),
            "retweets":    m.get("retweet_count", 0),
            "replies":     m.get("reply_count", 0),
            "impressions": m.get("impression_count", 0),
            "fetched_at":  datetime.now(timezone.utc).isoformat(),
        }
    except tweepy.errors.NotFound:
        print(f"  Tweet {tweet_id} not found (deleted?).")
        return None
    except Exception as e:
        print(f"  Error fetching {tweet_id}: {e}")
        return None


def update_analytics():
    """Archive'daki tweet_id'leri çek, metriklerini güncelle, kaydet."""
    client  = get_client()
    archive = load_archive()
    records = {r["tweet_id"]: r for r in load_analytics() if "tweet_id" in r}

    # Son 30 gün içinde atılmış ve tweet_id'si olan entry'ler
    cutoff = datetime.utcnow() - timedelta(days=30)
    to_fetch = [
        e for e in archive
        if e.get("tweet_id")
        and datetime.fromisoformat(e["posted_at"]) > cutoff
    ]

    print(f"Fetching metrics for {len(to_fetch)} tweets...")

    for entry in to_fetch:
        tid = entry["tweet_id"]
        metrics = fetch_metrics_for_tweet(client, tid)
        if metrics is None:
            continue

        posted_dt = datetime.fromisoformat(entry["posted_at"])
        record = {
            "tweet_id":     tid,
            "content_id":   entry.get("content_id", ""),
            "content_type": entry.get("content_type", "unknown"),
            "tweet_text":   entry.get("tweet_text", ""),
            "theme":        entry.get("theme", ""),
            "posted_at":    entry["posted_at"],
            "posted_hour":  posted_dt.hour,
            "posted_day":   posted_dt.weekday(),   # 0=Pazartesi
            **metrics,
        }
        records[tid] = record
        print(f"  [{entry.get('content_type','?'):25}] ❤️ {metrics['likes']:>4}  🔁 {metrics['retweets']:>3}  💬 {metrics['replies']:>3}")

    save_analytics(list(records.values()))
    print(f"\nAnalytics saved: {len(records)} total records.")
    return list(records.values())


# ── Analiz fonksiyonları ──────────────────────────────────────────────────────

def engagement_score(record: dict) -> float:
    """likes + retweets*3 + replies*2 — ağırlıklı etkileşim skoru."""
    return (
        record.get("likes", 0)
        + record.get("retweets", 0) * 3
        + record.get("replies", 0) * 2
    )


def get_best_performing_content_type(records: list[dict]) -> str:
    """Ortalama engagement'a göre en iyi content_type'ı döndür."""
    from collections import defaultdict
    totals = defaultdict(list)
    for r in records:
        ct = r.get("content_type", "unknown")
        totals[ct].append(engagement_score(r))
    if not totals:
        return "unknown"
    averages = {ct: sum(v) / len(v) for ct, v in totals.items()}
    return max(averages, key=averages.get)


def get_optimal_posting_hour(records: list[dict], content_type: str = None) -> int:
    """Engagement'a göre en iyi UTC saatini döndür."""
    from collections import defaultdict
    filtered = records
    if content_type:
        filtered = [r for r in records if r.get("content_type") == content_type]
    if not filtered:
        return 15  # default UTC

    hourly = defaultdict(list)
    for r in filtered:
        hourly[r.get("posted_hour", 15)].append(engagement_score(r))

    averages = {h: sum(v) / len(v) for h, v in hourly.items()}
    return max(averages, key=averages.get)


def get_top_tweets(records: list[dict], n: int = 5) -> list[dict]:
    return sorted(records, key=engagement_score, reverse=True)[:n]


def get_worst_tweets(records: list[dict], n: int = 5) -> list[dict]:
    return sorted(records, key=engagement_score)[:n]


def get_theme_performance(records: list[dict]) -> dict:
    """Tema bazında ortalama engagement."""
    from collections import defaultdict
    theme_scores = defaultdict(list)
    for r in records:
        theme = r.get("theme")
        if theme:
            theme_scores[theme].append(engagement_score(r))
    return {t: round(sum(v) / len(v), 1) for t, v in theme_scores.items()}


# ── Rapor ────────────────────────────────────────────────────────────────────

def print_report(records: list[dict]):
    if not records:
        print("No analytics data yet.")
        return

    print("\n" + "═" * 60)
    print("  @decentralize___ — ANALYTICS RAPORU")
    print(f"  {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print("═" * 60)

    print(f"\n📊 Toplam kayıt: {len(records)}")

    best_ct = get_best_performing_content_type(records)
    print(f"🏆 En iyi content type: {best_ct}")

    best_hour = get_optimal_posting_hour(records)
    print(f"⏰ En iyi posting saati (UTC): {best_hour:02d}:00")

    print("\n🔥 En iyi 5 tweet:")
    for r in get_top_tweets(records):
        score = engagement_score(r)
        text  = (r.get("tweet_text") or "")[:60]
        print(f"  [{score:>5.0f}] {text}...")

    print("\n❄️  En kötü 5 tweet:")
    for r in get_worst_tweets(records):
        score = engagement_score(r)
        text  = (r.get("tweet_text") or "")[:60]
        print(f"  [{score:>5.0f}] {text}...")

    theme_perf = get_theme_performance(records)
    if theme_perf:
        print("\n🎯 Tema performansı:")
        for theme, avg in sorted(theme_perf.items(), key=lambda x: -x[1]):
            print(f"  {theme:<22} → avg {avg:.1f}")

    # Content type dağılımı
    from collections import defaultdict
    ct_avg = defaultdict(list)
    for r in records:
        ct_avg[r.get("content_type", "?")].append(engagement_score(r))
    print("\n📋 Content type ortalamaları:")
    for ct, scores in sorted(ct_avg.items(), key=lambda x: -sum(x[1]) / len(x[1])):
        avg = sum(scores) / len(scores)
        print(f"  {ct:<28} → avg {avg:.1f}  (n={len(scores)})")

    print("\n" + "═" * 60)


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "update"

    if mode == "report":
        print_report(load_analytics())
    else:
        records = update_analytics()
        print_report(records)
