"""
daily_scan.py
-------------
AŞAMA 1: Günlük Tarama.

Süreç:
  1. 8 sorgu grubu ile tweet çek (her biri 20 sonuç)
  2. Her tweet için spam + konu filtresi uygula
  3. Geçenleri LLM ile puanla (4 eksen → ortalama → IQ)
  4. IQ < eşik altındakileri at (varsayılan: avg >= 7.0 = IQ 115)
  5. Yeterli tweet bulunamazsa ek sorgularla devam et
  6. Kabul edilenleri scan_results.json'a, reddedilenleri scan_rejected.json'a yaz

Çıktı:
    scan_results.json  — kabul edilen tweetler (IQ + detay puanlar dahil)
    scan_rejected.json — reddedilen tweetler (neden dahil)
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
SCAN_PATH     = os.path.join(os.path.dirname(__file__), "scan_results.json")
REJECTED_PATH = os.path.join(os.path.dirname(__file__), "scan_rejected.json")

# Minimum engagement eşiği (likes + retweets*3)
_MIN_ENGAGEMENT = 10
# Her sorgu için max sonuç
_MAX_RESULTS = 20
# Kaydetmek istediğimiz kabul edilen tweet sayısı
_SAVE_TOP = 30
# IQ eşiği (avg * 16.5) — bu altı kabul edilmez
_IQ_MIN = 115          # avg >= 7.0
_IQ_FALLBACK = 99      # avg >= 6.0 — fallback turunda
_MIN_ACCEPTED = 15     # bu kadar kabul edilemezse fallback tur başlar

# ── Ana sorgular: konuya göre 8 grup ──────────────────────────────────────────
_QUERIES = [
    # 1. WebXR / OpenXR — çekirdek teknik içerik
    "webxr -is:retweet -is:reply lang:en",
    # 2. Spatial computing / immersive web
    "(#SpatialComputing OR openxr OR #ImmersiveWeb OR #3DGS OR spatial-web) -is:retweet -is:reply lang:en",
    # 3. VR/XR — geliştirici/teknik bağlam (eğlence değil)
    "(#VirtualReality OR #MixedReality OR #XR) (developer OR SDK OR browser OR scene OR spatial OR research OR design) -is:retweet -is:reply lang:en",
    # 4. Virtual fashion / digital fashion / metaverse design
    "(#VirtualFashion OR #DigitalFashion OR #MetaverseDesign OR #VirtualArchitecture OR #DigitalCouture) -is:retweet -is:reply lang:en",
    # 5. Digital art / generative art / NFT art (scam değil)
    "(#DigitalArt OR #GenerativeArt OR #NFTArt OR #AIArt) -(mint OR whitelist OR presale OR giveaway OR airdrop) -is:retweet -is:reply lang:en",
    # 6. Smart cities / future cities / digital twin / avatar
    "(#SmartCities OR #FutureCities OR #DigitalTwin OR #AvatarDesign OR #VRM OR #Interoperability) -is:retweet -is:reply lang:en",
    # 7. Metaverse / virtual world — kripto shill hariç
    "(#Metaverse OR #VirtualWorld OR #SpatialWeb OR #3DWorldBuilding) -(XOOB OR Permaweb OR permacast OR 0G OR memecoin OR airdrop) -is:retweet -is:reply lang:en",
    # 8. WebXR + gaussian splat + volumetric
    "(#WebXR OR gaussian-splat OR volumetric OR #PointCloud OR haptic) -is:retweet -is:reply lang:en",
]

# Yeterli tweet bulunamazsa (fallback tur)
_EXTENDED_QUERIES = [
    "(#VirtualDesign OR #MetaverseArt OR #OnChainArt OR #GenerativeAI) -is:retweet -is:reply lang:en",
    "(spatial-computing OR immersive-web OR openxr OR webgl) developer -is:retweet -is:reply lang:en",
    "(#AIArt OR #DigitalIdentity OR #VRDesign OR #XRDesign) -is:retweet -is:reply lang:en",
]


def _engagement(tweet) -> int:
    m = getattr(tweet, "public_metrics", {}) or {}
    if hasattr(m, "get"):
        return m.get("like_count", 0) + m.get("retweet_count", 0) * 3
    return 0


def _score_detail(text: str) -> dict:
    """LLM ile detaylı puanlama. GROQ key yoksa pass-through döndür."""
    if not GROQ_API_KEY:
        return {"o": 9, "s": 9, "p": 9, "c": 9, "avg": 9.0, "iq": 148}
    try:
        from core.llm import score_tweet_detail
        return score_tweet_detail(text)
    except Exception as e:
        print(f"  score_detail error: {e}")
        return {"o": 0, "s": 0, "p": 0, "c": 0, "avg": 0.0, "iq": 0}


def _fetch_query(client, query: str, seen_ids: set) -> list:
    """Tek sorgu çalıştır, spam+konu filtrele, ham sonuçları döndür."""
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
            print(f"  No results: {query[:55]}...")
            return results

        users = {u.id: u.username for u in (resp.includes.get("users") or [])}

        spam_count = offtopic_count = eng_count = 0
        for tweet in resp.data:
            if tweet.id in seen_ids:
                continue
            eng = _engagement(tweet)
            if eng < _MIN_ENGAGEMENT:
                eng_count += 1
                continue
            if _is_scam(tweet.text):
                spam_count += 1
                continue
            if _is_off_topic(tweet.text):
                offtopic_count += 1
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

        print(
            f"  Query: {len(resp.data)} raw → "
            f"{spam_count} spam, {offtopic_count} off-topic, {eng_count} low-eng, "
            f"{len(results)} passed | {query[:45]}..."
        )
    except Exception as e:
        print(f"  Query failed: {e} — {query[:45]}...")
    return results


def _score_and_filter(tweets: list, iq_threshold: int, rejected_log: list) -> list:
    """Tweeleri puanla, IQ eşiği altındakileri rejected_log'a yaz."""
    if not GROQ_API_KEY:
        print("  GROQ_API_KEY not set — quality scoring skipped (all pass).")
        for t in tweets:
            t["scores"] = {"o": 9, "s": 9, "p": 9, "c": 9, "avg": 9.0, "iq": 148}
        return tweets

    passed = []
    for t in tweets:
        detail = _score_detail(t["text"])
        t["scores"] = detail
        if detail["iq"] >= iq_threshold:
            passed.append(t)
        else:
            rejected_log.append({
                "author": t["author"],
                "text": t["text"][:120],
                "reason": f"LOW_IQ:{detail['iq']} (avg:{detail['avg']} O:{detail['o']} S:{detail['s']} P:{detail['p']} C:{detail['c']})",
            })
            print(
                f"  REJECT IQ={detail['iq']} avg={detail['avg']} "
                f"@{t['author']}: {t['text'][:45]}..."
            )
    return passed


def run_daily_scan() -> list:
    if not BEARER_TOKEN:
        print("ERROR: BEARERTOKEN not set — skipping daily scan.")
        sys.exit(1)

    client = tweepy.Client(bearer_token=BEARER_TOKEN, wait_on_rate_limit=True)
    all_candidates = []
    seen_ids: set = set()
    rejected_log: list = []

    # ── Tur 1: Ana sorgular ────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("TUR 1 — Ana sorgular")
    print('='*60)
    for query in _QUERIES:
        all_candidates.extend(_fetch_query(client, query, seen_ids))
        time.sleep(2)

    # Engagement sırası → kalite için adayları hazırla
    all_candidates.sort(key=lambda t: t["engagement_score"], reverse=True)
    to_score = all_candidates[:_SAVE_TOP * 2]

    print(f"\nPuanlama (IQ eşiği={_IQ_MIN}, {len(to_score)} aday)...")
    accepted = _score_and_filter(to_score, _IQ_MIN, rejected_log)

    # ── Tur 2: Yeterli değilse ek sorgular (fallback eşiği) ────────────────────
    if len(accepted) < _MIN_ACCEPTED:
        print(f"\n{'='*60}")
        print(f"TUR 2 — Fallback (yalnızca {len(accepted)} kabul edildi, hedef={_MIN_ACCEPTED})")
        print('='*60)
        extra = []
        for query in _EXTENDED_QUERIES:
            extra.extend(_fetch_query(client, query, seen_ids))
            time.sleep(2)

        accepted_ids = {t["tweet_id"] for t in accepted}
        new_pool = [t for t in extra if t["tweet_id"] not in accepted_ids]
        new_pool.sort(key=lambda t: t["engagement_score"], reverse=True)

        print(f"\nFallback puanlama (IQ eşiği={_IQ_FALLBACK}, {len(new_pool)} yeni aday)...")
        fallback_accepted = _score_and_filter(new_pool[:_SAVE_TOP], _IQ_FALLBACK, rejected_log)
        accepted.extend(fallback_accepted)

    # ── Sonuç ──────────────────────────────────────────────────────────────────
    accepted.sort(key=lambda t: t["engagement_score"], reverse=True)
    top = accepted[:_SAVE_TOP]

    with open(SCAN_PATH, "w", encoding="utf-8") as f:
        json.dump(top, f, indent=2, ensure_ascii=False)

    with open(REJECTED_PATH, "w", encoding="utf-8") as f:
        json.dump(rejected_log, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"TARAMA TAMAMLANDI")
    print(f"  Kabul: {len(top)} tweet → scan_results.json")
    print(f"  Ret:   {len(rejected_log)} tweet → scan_rejected.json")
    if top:
        best = top[0]
        s = best.get("scores", {})
        print(f"  En iyi: @{best['author']} IQ={s.get('iq','?')} eng={best['engagement_score']}")
    print('='*60)
    return top


if __name__ == "__main__":
    results = run_daily_scan()
    print(f"Top tweet: {results[0]['text'][:80] if results else 'none'}...")
