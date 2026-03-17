"""
daily_scan.py
-------------
AŞAMA 1: Günlük Tarama.

Süreç:
  1. 8 sorgu grubu ile tweet çek (her biri 20 sonuç)
  2. Her tweet için spam + konu filtresi uygula
  3. Geçenleri LLM ile puanla (3-eksen IQ3 = (O+S+C)/3 * 16.5)
     Not: P (Provocation) başkasının tweeti için adil değil → IQ3 kullanılır
  4. IQ3 < eşik altındakileri at, kategori sınırları dolana kadar sorgula
  5. _classify() ile her tweet QRT / RT / REPLY / LIKE olarak etiketle
  6. Kabul edilenleri scan_results.json'a, reddedilenleri scan_rejected.json'a yaz

Kategori Mantığı:
  QUOTE_RT  — orijinal analiz/içgörü içeren tweet (O>=7, uzun metin, IQ3>=110)
              Amacımız: kendi sesimizden yorum ekleyerek profilde görünmek
  RT        — haber/demo/yüksek engagement içerik (eng>=200 VEYA URL+kısa+S>=7)
              Amacımız: alanla ilgili haberi sessizce amplify etmek
  REPLY     — geliştirici/uygulayıcı paylaşımı (1. kişi + URL + reply açık)
              Amacımız: direkt konuşma başlatmak, toplulukta görünmek
  LIKE      — kalan kaliteli içerik (IQ3>=82, fallback)

Çıktı:
    scan_results.json  — kabul edilen tweetler (IQ3 + detay puanlar + kategori)
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
# Kaydetmek istediğimiz kabul edilen tweet sayısı (toplam)
_SAVE_TOP = 30

# Kategori hedefleri — hepsi dolarsa tarama biter
_QUOTA = {
    "quote_rt": 5,
    "rt":       5,
    "reply":   20,
}
# LIKE için quota yok, kalan kaliteli tweetler oraya gider

# IQ3 eşikleri (her tur için azalan)
_IQ3_R1 = 115   # Tur 1: IQ3 >= 115 (avg O+S+C >= 7.0)
_IQ3_R2 = 99    # Tur 2: IQ3 >= 99  (avg >= 6.0) — ek sorgular
_IQ3_R3 = 99    # Tur 3: IQ3 >= 99  — farklı sorgular
_IQ3_R4 = 82    # Tur 4: IQ3 >= 82  (avg >= 5.0) — ret havuzundan kurtarma
_MIN_ACCEPTED = 15  # Bu kadar kabul edilemezse 2. tur başlar

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

# Tur 2 ek sorgular (daha geniş)
_EXTENDED_QUERIES = [
    "(#VirtualDesign OR #MetaverseArt OR #OnChainArt OR #GenerativeAI) -is:retweet -is:reply lang:en",
    "(spatial-computing OR immersive-web OR openxr OR webgl) developer -is:retweet -is:reply lang:en",
    "(#AIArt OR #DigitalIdentity OR #VRDesign OR #XRDesign) -is:retweet -is:reply lang:en",
]

# Tur 3 — daha geniş niş tarama
_BROAD_QUERIES = [
    "(virtual OR spatial OR immersive) (architecture OR design OR art OR fashion) -is:retweet -is:reply lang:en",
    "(metaverse OR WebXR OR #XR) (research OR paper OR study OR demo OR launch) -is:retweet -is:reply lang:en",
    "(avatar OR VRM OR digital-twin OR interoperability) -is:retweet -is:reply lang:en",
]


def _engagement(tweet) -> int:
    m = getattr(tweet, "public_metrics", {}) or {}
    if hasattr(m, "get"):
        return m.get("like_count", 0) + m.get("retweet_count", 0) * 3
    return 0


def _score_detail(text: str) -> dict:
    """LLM ile detaylı puanlama. GROQ key yoksa pass-through döndür."""
    if not GROQ_API_KEY:
        return {"o": 9, "s": 9, "p": 9, "c": 9, "avg": 9.0, "iq": 148, "iq3": 148}
    try:
        from core.llm import score_tweet_detail
        return score_tweet_detail(text)
    except Exception as e:
        print(f"  score_detail error: {e}")
        return {"o": 0, "s": 0, "p": 0, "c": 0, "avg": 0.0, "iq": 0, "iq3": 0}


def _classify(t: dict) -> str:
    """Tweet'i kategorize et: quote_rt | rt | reply | like.

    Kural hiyerarşisi:
    1. QUOTE_RT: Orijinal analiz/içgörü içeren → kendi sesimizle yorum ekleyeceğiz
       - O >= 7 (özgün düşünce)
       - Metin uzunluğu >= 100 karakter
       - IQ3 >= 110
       - URL yok (link değil, düşünce paylaşıyor)
    2. RT: Haber/demo/yüksek engagement → sessizce amplify
       - eng >= 200 (zaten viral)
       - VEYA URL var + metin kısa (<120 kar) + S >= 7
    3. REPLY: Geliştirici/uygulayıcı paylaşımı → konuşma başlatmak
       - reply_settings == "everyone"
       - Metin 1. şahıs içeriyor ("I ", "my ", "we ", "our ")
       - IQ3 >= 99
    4. LIKE: Kalan kaliteli içerik (fallback)
    """
    scores = t.get("scores", {})
    o    = scores.get("o",   0)
    s    = scores.get("s",   0)
    iq3  = scores.get("iq3", 0)
    text = t.get("text", "")
    eng  = t.get("engagement_score", 0)
    has_url = "http" in text
    text_len = len(text)
    is_first_person = any(
        word in text.lower()
        for word in [" i ", " i'", "my ", "we ", "our ", " i'm", " i've", " i've", "i built", "i made"]
    )
    reply_open = t.get("reply_settings", "everyone") == "everyone"

    # 1. QUOTE_RT
    if o >= 7 and text_len >= 100 and iq3 >= 110 and not has_url:
        return "quote_rt"

    # 2. RT
    if eng >= 200:
        return "rt"
    if has_url and text_len < 120 and s >= 7:
        return "rt"

    # 3. REPLY
    if reply_open and is_first_person and iq3 >= 99:
        return "reply"

    # 4. LIKE (fallback)
    return "like"


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


def _score_and_filter(tweets: list, iq3_threshold: int, rejected_log: list) -> list:
    """Tweeleri puanla, IQ3 eşiği altındakileri rejected_log'a yaz."""
    if not GROQ_API_KEY:
        print("  GROQ_API_KEY not set — quality scoring skipped (all pass).")
        for t in tweets:
            t["scores"] = {"o": 9, "s": 9, "p": 9, "c": 9, "avg": 9.0, "iq": 148, "iq3": 148}
        return tweets

    passed = []
    for t in tweets:
        detail = _score_detail(t["text"])
        t["scores"] = detail
        if detail["iq3"] >= iq3_threshold:
            passed.append(t)
        else:
            rejected_log.append({
                "author": t["author"],
                "text": t["text"][:120],
                "reason": (
                    f"LOW_IQ3:{detail['iq3']} "
                    f"(O:{detail['o']} S:{detail['s']} C:{detail['c']} P:{detail['p']} "
                    f"avg:{detail['avg']})"
                ),
            })
            print(
                f"  REJECT IQ3={detail['iq3']} avg={detail['avg']} "
                f"@{t['author']}: {t['text'][:45]}..."
            )
    return passed


def _quotas_full(buckets: dict) -> bool:
    """Tüm ana kategoriler (QRT, RT, REPLY) dolu mu?"""
    return all(len(buckets[cat]) >= _QUOTA[cat] for cat in _QUOTA)


def _fill_buckets(candidates: list, buckets: dict, seen_ids_in_buckets: set) -> None:
    """Adayları _classify() ile etiketle, boş kova varsa doldur."""
    for t in candidates:
        tid = t["tweet_id"]
        if tid in seen_ids_in_buckets:
            continue
        cat = _classify(t)
        t["category"] = cat
        if cat in _QUOTA and len(buckets[cat]) < _QUOTA[cat]:
            buckets[cat].append(t)
            seen_ids_in_buckets.add(tid)
        elif cat == "like":
            buckets["like"].append(t)
            seen_ids_in_buckets.add(tid)


def run_daily_scan() -> list:
    if not BEARER_TOKEN:
        print("ERROR: BEARERTOKEN not set — skipping daily scan.")
        sys.exit(1)

    client = tweepy.Client(bearer_token=BEARER_TOKEN, wait_on_rate_limit=True)
    seen_ids: set = set()           # tüm çekilen tweet ID'leri (tekrar önleme)
    rejected_log: list = []
    buckets = {"quote_rt": [], "rt": [], "reply": [], "like": []}
    seen_in_buckets: set = set()    # kovalara atılmış tweet ID'leri

    # ── TUR 1: Ana sorgular — IQ3 >= 115 ──────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"TUR 1 — Ana sorgular (IQ3>={_IQ3_R1})")
    print('='*60)
    raw1 = []
    for query in _QUERIES:
        raw1.extend(_fetch_query(client, query, seen_ids))
        time.sleep(2)

    raw1.sort(key=lambda t: t["engagement_score"], reverse=True)
    pool_size = min(len(raw1), _SAVE_TOP * 2)
    accepted1 = _score_and_filter(raw1[:pool_size], _IQ3_R1, rejected_log)
    _fill_buckets(accepted1, buckets, seen_in_buckets)

    total_accepted = sum(len(v) for v in buckets.values())
    print(f"\nTur 1 sonuç: kabul={total_accepted} | "
          f"QRT:{len(buckets['quote_rt'])}/{_QUOTA['quote_rt']} "
          f"RT:{len(buckets['rt'])}/{_QUOTA['rt']} "
          f"Reply:{len(buckets['reply'])}/{_QUOTA['reply']}")

    # ── TUR 2: Ek sorgular — yeterli değilse ──────────────────────────────────
    if not _quotas_full(buckets) and total_accepted < _MIN_ACCEPTED:
        print(f"\n{'='*60}")
        print(f"TUR 2 — Ek sorgular (IQ3>={_IQ3_R2})")
        print('='*60)
        raw2 = []
        for query in _EXTENDED_QUERIES:
            raw2.extend(_fetch_query(client, query, seen_ids))
            time.sleep(2)

        raw2.sort(key=lambda t: t["engagement_score"], reverse=True)
        accepted2 = _score_and_filter(raw2[:_SAVE_TOP], _IQ3_R2, rejected_log)
        _fill_buckets(accepted2, buckets, seen_in_buckets)

        total_accepted = sum(len(v) for v in buckets.values())
        print(f"\nTur 2 sonuç: kabul={total_accepted} | "
              f"QRT:{len(buckets['quote_rt'])}/{_QUOTA['quote_rt']} "
              f"RT:{len(buckets['rt'])}/{_QUOTA['rt']} "
              f"Reply:{len(buckets['reply'])}/{_QUOTA['reply']}")

    # ── TUR 3: Geniş sorgular — hâlâ yeterli değilse ─────────────────────────
    if not _quotas_full(buckets):
        print(f"\n{'='*60}")
        print(f"TUR 3 — Geniş sorgular (IQ3>={_IQ3_R3})")
        print('='*60)
        raw3 = []
        for query in _BROAD_QUERIES:
            raw3.extend(_fetch_query(client, query, seen_ids))
            time.sleep(2)

        raw3.sort(key=lambda t: t["engagement_score"], reverse=True)
        accepted3 = _score_and_filter(raw3[:_SAVE_TOP], _IQ3_R3, rejected_log)
        _fill_buckets(accepted3, buckets, seen_in_buckets)

        total_accepted = sum(len(v) for v in buckets.values())
        print(f"\nTur 3 sonuç: kabul={total_accepted} | "
              f"QRT:{len(buckets['quote_rt'])}/{_QUOTA['quote_rt']} "
              f"RT:{len(buckets['rt'])}/{_QUOTA['rt']} "
              f"Reply:{len(buckets['reply'])}/{_QUOTA['reply']}")

    # ── TUR 4: Ret havuzundan kurtarma — eşiği daha da düşür ─────────────────
    if not _quotas_full(buckets):
        print(f"\n{'='*60}")
        print(f"TUR 4 — Ret havuzundan kurtarma (IQ3>={_IQ3_R4})")
        print('='*60)
        # Daha önce reddedilenleri yeniden değerlendir (sadece daha düşük eşikle geçirme)
        rescue_candidates = []
        for entry in rejected_log:
            # rejected_log'da tam tweet objesi yok, sadece özet var; bu tur için
            # tüm raw havuzlarında IQ3 >= 82 olanları çekelim
            pass

        # Tur 1-3'te puanlanmış ama eşiği geçemeyen tweetleri kurtarmak için:
        # raw havuzunu yeniden tara (zaten scored)
        all_raw_scored = [
            t for t in (raw1[:pool_size] if 'raw1' in dir() else [])
            if t.get("scores", {}).get("iq3", 0) >= _IQ3_R4
            and t["tweet_id"] not in seen_in_buckets
        ]
        all_raw_scored.sort(key=lambda t: t["engagement_score"], reverse=True)
        _fill_buckets(all_raw_scored, buckets, seen_in_buckets)

        total_accepted = sum(len(v) for v in buckets.values())
        print(f"\nTur 4 sonuç: kabul={total_accepted} | "
              f"QRT:{len(buckets['quote_rt'])}/{_QUOTA['quote_rt']} "
              f"RT:{len(buckets['rt'])}/{_QUOTA['rt']} "
              f"Reply:{len(buckets['reply'])}/{_QUOTA['reply']}")

    # ── Sonuç: Tüm kategoryileri birleştir ───────────────────────────────────
    # Sıralama: QRT → RT → Reply → Like (raporda mantıklı görünmesi için)
    all_accepted = (
        buckets["quote_rt"]
        + buckets["rt"]
        + buckets["reply"]
        + buckets["like"]
    )
    # Her kategori içinde engagement'a göre sırala
    all_accepted.sort(key=lambda t: (
        ["quote_rt", "rt", "reply", "like"].index(t.get("category", "like")),
        -t["engagement_score"]
    ))
    top = all_accepted[:_SAVE_TOP]

    with open(SCAN_PATH, "w", encoding="utf-8") as f:
        json.dump(top, f, indent=2, ensure_ascii=False)

    with open(REJECTED_PATH, "w", encoding="utf-8") as f:
        json.dump(rejected_log, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"TARAMA TAMAMLANDI")
    print(f"  Kabul: {len(top)} tweet → scan_results.json")
    print(f"    QRT:   {len(buckets['quote_rt'])} / {_QUOTA['quote_rt']}")
    print(f"    RT:    {len(buckets['rt'])} / {_QUOTA['rt']}")
    print(f"    Reply: {len(buckets['reply'])} / {_QUOTA['reply']}")
    print(f"    Like:  {len(buckets['like'])} (kota yok)")
    print(f"  Ret:   {len(rejected_log)} tweet → scan_rejected.json")
    if top:
        best = top[0]
        s = best.get("scores", {})
        print(f"  En iyi: @{best['author']} IQ3={s.get('iq3','?')} IQ={s.get('iq','?')} eng={best['engagement_score']} cat={best.get('category','?')}")
    print('='*60)
    return top


if __name__ == "__main__":
    results = run_daily_scan()
    print(f"Top tweet: {results[0]['text'][:80] if results else 'none'}...")
