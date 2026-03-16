"""
analytics.py
------------
Tweet arşivindeki lokal veriden analiz üretir — Twitter API gerektirmez.
Free tier ile tam uyumlu: tweet_archive.json okunur, yapısal analiz yapılır.

Kullanım:
  python analytics.py          → arşivi sync et + rapor yaz
  python analytics.py report   → sadece mevcut kaydı yazdır
  python analytics.py sync     → archive → analytics.json sync et
"""

import os
import sys
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

ANALYTICS_PATH = os.path.join(os.path.dirname(__file__), "tweet_analytics.json")
ARCHIVE_PATH   = os.path.join(os.path.dirname(__file__), "tweet_archive.json")


# ── I/O ───────────────────────────────────────────────────────────────────────

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


# ── Sync: archive → analytics ──────────────────────────────────────────────

def sync_from_archive() -> list[dict]:
    """
    tweet_archive.json → tweet_analytics.json

    Twitter API kullanılmaz. Engagement metrikleri (likes/retweets/replies)
    API olmadan bilinemez, 0 olarak kaydedilir. Yapısal analiz (içerik tipi,
    tema, saat, metin) tam olarak çalışır.
    """
    archive = load_archive()
    existing = {r["tweet_id"]: r for r in load_analytics() if r.get("tweet_id")}

    added = 0
    for entry in archive:
        tid = entry.get("tweet_id")
        if not tid or tid in existing:
            continue

        posted_at = entry.get("posted_at", "")
        try:
            posted_dt = datetime.fromisoformat(posted_at)
            # Naive timestamp ise UTC kabul et (eski arşiv kayıtları için)
            if posted_dt.tzinfo is None:
                posted_dt = posted_dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError) as e:
            print(f"Skipping record {tid}: bad posted_at '{posted_at}' — {e}")
            posted_dt = datetime.now(timezone.utc)

        record = {
            "tweet_id":     tid,
            "content_id":   entry.get("content_id", ""),
            "content_type": entry.get("content_type", "unknown"),
            "tweet_text":   entry.get("tweet_text", ""),
            "theme":        entry.get("theme", ""),
            "posted_at":    posted_at,
            "posted_hour":  posted_dt.hour,
            "posted_day":   posted_dt.weekday(),  # 0=Pazartesi
            # Engagement: API olmadan bilinmiyor; 0 bırakıyoruz
            "likes":        0,
            "retweets":     0,
            "replies":      0,
            "impressions":  0,
        }
        existing[tid] = record
        added += 1

    records = list(existing.values())
    save_analytics(records)
    print(f"Sync tamamlandı: +{added} yeni kayıt, toplam {len(records)}.")
    return records


# ── Analiz fonksiyonları ──────────────────────────────────────────────────────

def engagement_score(record: dict) -> float:
    """likes + retweets*3 + replies*2"""
    return (
        record.get("likes", 0)
        + record.get("retweets", 0) * 3
        + record.get("replies", 0) * 2
    )


def get_content_type_distribution(records: list[dict]) -> dict:
    """Her content_type kaç kez kullanılmış."""
    counts: dict = defaultdict(int)
    for r in records:
        counts[r.get("content_type", "unknown")] += 1
    return dict(counts)


def get_posting_hour_distribution(records: list[dict]) -> dict:
    """Saate göre tweet sayısı."""
    counts: dict = defaultdict(int)
    for r in records:
        counts[r.get("posted_hour", 0)] += 1
    return dict(counts)


def get_posting_day_distribution(records: list[dict]) -> dict:
    """Güne göre tweet sayısı (0=Pazartesi)."""
    days = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]
    counts: dict = defaultdict(int)
    for r in records:
        counts[r.get("posted_day", 0)] += 1
    return {days[k]: v for k, v in sorted(counts.items())}


def get_theme_distribution(records: list[dict]) -> dict:
    """Tema bazında tweet sayısı."""
    counts: dict = defaultdict(int)
    for r in records:
        theme = r.get("theme")
        if theme:
            counts[theme] += 1
    return dict(counts)


def get_best_performing_content_type(records: list[dict]) -> str:
    """Ortalama engagement'a göre en iyi content_type (API verisi yoksa count'a göre)."""
    totals: dict = defaultdict(list)
    for r in records:
        totals[r.get("content_type", "unknown")].append(engagement_score(r))
    if not totals:
        return "unknown"
    # Eğer tüm engagement 0 ise (API yok), en çok kullanılan tipi döndür
    all_zero = all(sum(v) == 0 for v in totals.values())
    if all_zero:
        return max(totals, key=lambda k: len(totals[k]))
    averages = {ct: sum(v) / len(v) for ct, v in totals.items()}
    return max(averages, key=averages.get)


def get_top_tweets(records: list[dict], n: int = 5) -> list[dict]:
    """En çok engagement alan N tweet (API verisi varsa)."""
    has_engagement = [r for r in records if engagement_score(r) > 0]
    if has_engagement:
        return sorted(has_engagement, key=engagement_score, reverse=True)[:n]
    # API verisi yoksa en son N tweet
    sorted_by_date = sorted(
        records,
        key=lambda r: r.get("posted_at", ""),
        reverse=True,
    )
    return sorted_by_date[:n]


def get_recent_tweets(records: list[dict], days: int = 30) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = []
    for r in records:
        try:
            dt = datetime.fromisoformat(r["posted_at"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= cutoff:
                result.append(r)
        except (ValueError, KeyError):
            pass
    return result


# ── Rapor ─────────────────────────────────────────────────────────────────────

def print_report(records: list[dict]):
    if not records:
        print("Henüz analitik veri yok. Önce 'python analytics.py sync' çalıştır.")
        return

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    recent  = get_recent_tweets(records, days=30)

    print("\n" + "═" * 60)
    print("  @decentralize___ — ANALİTİK RAPORU")
    print(f"  {now_str}")
    print("═" * 60)

    print(f"\n📊 Toplam kayıt: {len(records)}  |  Son 30 gün: {len(recent)}")
    print("ℹ️  Not: Free tier — engagement metrikleri API'den alınamıyor.")
    print("   Analizler tweet arşivindeki yapısal veriye dayanıyor.\n")

    # Content type dağılımı
    ct_dist = get_content_type_distribution(records)
    print("📋 Content type dağılımı:")
    for ct, count in sorted(ct_dist.items(), key=lambda x: -x[1]):
        bar = "█" * min(count, 40)
        print(f"  {ct:<28} {bar} {count}")

    # Saatlik dağılım
    hour_dist = get_posting_hour_distribution(records)
    print("\n⏰ Saatlik dağılım (UTC):")
    for hour in sorted(hour_dist):
        count = hour_dist[hour]
        bar = "█" * min(count, 30)
        print(f"  {hour:02d}:00  {bar} {count}")

    # Günlük dağılım
    day_dist = get_posting_day_distribution(records)
    print("\n📅 Günlük dağılım:")
    for day, count in day_dist.items():
        bar = "█" * min(count, 30)
        print(f"  {day}  {bar} {count}")

    # Tema dağılımı
    theme_dist = get_theme_distribution(records)
    if theme_dist:
        print("\n🎯 Tema dağılımı:")
        for theme, count in sorted(theme_dist.items(), key=lambda x: -x[1]):
            bar = "█" * min(count, 30)
            print(f"  {theme:<22} {bar} {count}")

    # Son tweetler
    print(f"\n🕐 Son 5 tweet:")
    for r in get_top_tweets(records, n=5):
        text = (r.get("tweet_text") or "")[:70]
        ts   = r.get("posted_at", "")[:10]
        ct   = r.get("content_type", "?")
        print(f"  [{ts}] [{ct:20}] {text}...")

    print("\n" + "═" * 60)


# ── Failed tweets raporu (Faz 3.4 — rapor2.txt §3.4) ─────────────────────────

FAILED_PATH = os.path.join(os.path.dirname(__file__), "failed_tweets.json")
SCAN_PATH   = os.path.join(os.path.dirname(__file__), "scan_results.json")


def report_failures(days: int = 7) -> None:
    """Son N günün başarısız tweet'lerini tip bazında raporla."""
    try:
        with open(FAILED_PATH, "r", encoding="utf-8") as f:
            failures = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        print("No failed_tweets.json found or file is empty.")
        return

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    recent = []
    for fl in failures:
        try:
            failed_at = fl.get("failed_at", "")
            dt = datetime.fromisoformat(failed_at.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt > cutoff:
                recent.append(fl)
        except (ValueError, KeyError):
            pass

    if not recent:
        print(f"No failures in last {days} days.")
        return

    by_type: dict = defaultdict(int)
    for fl in recent:
        ct = fl.get("content_type", "unknown")
        by_type[ct] += 1

    print(f"\n=== FAILURES (last {days} days) — {len(recent)} total ===")
    for ct, count in sorted(by_type.items(), key=lambda x: -x[1]):
        bar = "█" * min(count, 20)
        print(f"  {ct:<28} {bar} {count}")
    print()


# ── AŞAMA 2: Pattern Extraction ───────────────────────────────────────────────

def analyze_scan_patterns(top_n: int = 10) -> str:
    """scan_results.json'dan yüksek engagement tweet pattern'larını çıkar (AŞAMA 2).

    Döndürür: LLM prompt'una inject edilecek kısa bağlam metni.
    """
    try:
        with open(SCAN_PATH, "r", encoding="utf-8") as f:
            results = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return ""

    if not results:
        return ""

    # En yüksek engagement tweet'leri al
    top = sorted(results, key=lambda r: r.get("engagement_score", 0), reverse=True)[:top_n]

    # Kelime frekansı ile baskın temaları çıkar
    from collections import Counter
    import re as _re
    stop = {"the","a","an","is","are","was","were","be","been","have","has","do","does",
            "will","would","could","should","not","but","and","or","for","in","on","at",
            "to","of","with","that","this","it","by","we","you","they","just","so","if",
            "when","what","how","who","there","very","too","even","now","all","some",
            "more","no","i","my","your","our","its","their","from","into","as","than",
            "can","may","might","must","rt","via"}
    words: Counter = Counter()
    for r in top:
        tokens = _re.findall(r"[a-z]+", r["text"].lower())
        for t in tokens:
            if t not in stop and len(t) > 3:
                words[t] += 1

    top_words = [w for w, _ in words.most_common(12)]
    top_texts = [r["text"][:120] for r in top[:5]]

    lines = ["High-engagement tweets in your niche right now (AŞAMA 2 — pattern context):"]
    for txt in top_texts:
        lines.append(f"  • {txt}")
    if top_words:
        lines.append(f"Dominant themes: {', '.join(top_words)}")

    return "\n".join(lines)


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "sync"

    if mode == "report":
        print_report(load_analytics())
    elif mode == "failures":
        report_failures(days=7)
    else:  # "sync" veya default
        records = sync_from_archive()
        print_report(records)
        report_failures(days=7)
