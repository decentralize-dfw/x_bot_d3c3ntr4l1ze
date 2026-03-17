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
import re
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


# ── Scam / spam sinyal listesi ────────────────────────────────────────────────
_SCAM_PATTERNS = re.compile(
    r"""
    # NFT mint / drop spam
    \b(mint|minting|presale|whitelist|wl\s+spot|free\s+mint|claim\s+your)\b
    # Pump sinyalleri
    | \b(100x|1000x|mooning|to\s+the\s+moon|next\s+gem|hidden\s+gem|ape\s+in|dyor)\b
    # Airdrop / giveaway
    | \b(airdrop|giveaway|free\s+tokens?|win\s+\d|drop\s+your\s+wallet|enter\s+to\s+win)\b
    # Follower farming
    | \b(follow\s+(&|and)\s+(rt|retweet)|rt\s+to\s+(win|enter)|like\s+and\s+follow)\b
    # Finansal spam
    | \b(buy\s+now|last\s+chance|limited\s+supply|floor\s+price|bullish|bearish|ath\b)\b
    # Proje isim kalıpları (8bit X, baby X, ape X, pepe X, doge X vb.)
    | \b(8bit|babydoge|babyape|pepe\s+coin|shib\b|degens?\b|based\s+dev|doxed\s+team)\b
    # Boot sequence / hype intro kalıbı
    | boot\s+sequence|gathering[\s,]+ready
    # Wallet/contract spam
    | \b(ca:|contract\s+address|0x[0-9a-f]{6,})\b
    # Token ticker ($XOOB, $KVAI vb.) — proje shill klasiği
    | \$[A-Z]{2,8}\b
    # Yield farming / token farming
    | \b(farming|yield\s+farm|liquidity\s+pool|staking\s+reward)\b
    # Wallet bağlantı spamı (Kvants tipi)
    | \b(link\s+(your\s+)?(evm\s+)?wallet|connect\s+(your\s+)?wallet|evm\s+wallet)\b
    # VIP / upgrade shill
    | \b(vip\s+access|unlock\s+vip|upgrade\s+(your|to)\s+(vip|access))\b
    # Çok sayıda 🚀 veya 💎 (3+) — scam klasiği
    | (?:🚀){3,}|(?:💎){3,}|(?:🔥){4,}
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Maksimum izin verilen hashtag sayısı — 5+ olan tweet'ler genellikle spam
_MAX_HASHTAGS = 5
# Minimum kelime sayısı — çok kısa tweet'ler anlamsız
_MIN_WORDS = 8


def _is_scam(text: str) -> bool:
    """Scam / spam sinyali taşıyan tweet'leri elek."""
    if _SCAM_PATTERNS.search(text):
        return True
    # Hashtag bombardımanı
    if text.count("#") > _MAX_HASHTAGS:
        return True
    # Çok kısa içerik
    if len(text.split()) < _MIN_WORDS:
        return True
    # URL olmadan mention spam (3+ @mention = muhtemelen reply farming)
    if len(re.findall(r"@\w+", text)) >= 3:
        return True
    return False


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
                tweet_fields=["public_metrics", "text", "author_id", "reply_settings"],
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
                if _is_scam(tweet.text):
                    print(f"  SCAM filtered: {tweet.text[:60]}...")
                    continue
                reply_settings = getattr(tweet, "reply_settings", "everyone") or "everyone"
                all_tweets.append({
                    "tweet_id": str(tweet.id),
                    "text": tweet.text,
                    "author": users.get(tweet.author_id, "unknown"),
                    "engagement_score": eng,
                    "reply_settings": reply_settings,
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
