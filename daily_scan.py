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
  QUOTE_RT  — orijinal analiz/içgörü içeren tweet (O>=7, IQ3>=110, len>=80)
              Amacımız: kendi sesimizden yorum ekleyerek profilde görünmek
  RT        — haber/demo/yüksek engagement içerik (eng>=200 VEYA URL+kısa+S>=7)
              Amacımız: alanla ilgili haberi sessizce amplify etmek
  REPLY     — geliştirici/uygulayıcı paylaşımı (1. kişi + reply açık + IQ3>=99)
              Amacımız: direkt konuşma başlatmak, toplulukta görünmek
  LIKE      — kalan kaliteli içerik (fallback)

Çıktı:
    scan_results.json  — kabul edilen tweetler (IQ3 + detay puanlar + kategori)
    scan_rejected.json — reddedilen tweetler (neden dahil)
"""
# BUG FIX #3: import re dosyanın başında olmalı, fonksiyon tanımlamaları arasında değil
import json
import os
import re as _re
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

# BUG FIX #3: regex module-level sabit olarak tanımlanmalı (fonksiyon içinde değil)
# 1. şahıs kelime sınırlı regex — \b ile "your" içindeki "our"u yakalamaz
_FIRST_PERSON_RE = _re.compile(
    r"\bi\b|\bi'm\b|\bi've\b|\bi'll\b|\bi'd\b"
    r"|\bmy\b|\bmine\b"
    r"|\bwe\b|\bwe're\b|\bwe've\b"
    r"|\bour\b",
    _re.IGNORECASE,
)


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

    Kural hiyerarşisi (sıralı — ilk eşleşen kazanır):
    1. QUOTE_RT: Orijinal analiz/içgörü içeren → kendi sesimizle yorum ekleyeceğiz
       - O >= 7 ve IQ3 >= 110 (özgün, yüksek kalite)
       - Metin uzunluğu >= 80 karakter (gerçek içerik var)
       → URL olsa da olur — iyi tweet URL içerebilir
    2. RT: Haber/demo/yüksek engagement → sessizce amplify
       - eng >= 200 (zaten viral)
       - VEYA metin kısa (<140 kar) + URL var + S >= 7 (link paylaşımı)
    3. REPLY: Geliştirici/uygulayıcı paylaşımı → konuşma başlat
       - reply_settings == "everyone"
       - Metin 1. şahıs içeriyor (kelime sınırlı regex — "your" hariç)
       - IQ3 >= 99
    4. LIKE: Kalan kaliteli içerik (fallback)
    """
    scores = t.get("scores", {})
    o    = scores.get("o",   0)
    s    = scores.get("s",   0)
    iq3  = scores.get("iq3", 0)
    text = t.get("text", "")
    eng  = t.get("engagement_score", 0)
    has_url  = "http" in text
    text_len = len(text)
    # BUG FIX #4 (eski): \b ile kelime sınırlı — "your" içindeki "our"u yakalamaz
    is_first_person = bool(_FIRST_PERSON_RE.search(text))
    reply_open = t.get("reply_settings", "everyone") == "everyone"

    # 1. QUOTE_RT — özgün analiz → kendi yorumumuzla paylaş
    if o >= 7 and iq3 >= 110 and text_len >= 80:
        return "quote_rt"

    # 2. RT — viral veya link paylaşımı → amplify et
    if eng >= 200:
        return "rt"
    if has_url and text_len < 140 and s >= 7:
        return "rt"

    # 3. REPLY — kişisel/geliştirici paylaşımı → konuşma başlat
    if reply_open and is_first_person and iq3 >= 99:
        return "reply"

    # 4. LIKE (fallback)
    return "like"


def _fetch_query(client, query: str, seen_ids: set,
                 sort_order: str = "relevancy",
                 min_engagement: int = None) -> list:
    """Tek sorgu çalıştır, spam+konu filtrele, ham sonuçları döndür.

    sort_order: "relevancy" (popüler/kanıtlanmış) veya "recency" (taze/yeni)
    min_engagement: None → _MIN_ENGAGEMENT kullan; recency için daha düşük değer verilebilir
    """
    effective_min = min_engagement if min_engagement is not None else _MIN_ENGAGEMENT
    results = []
    try:
        resp = client.search_recent_tweets(
            query=query,
            max_results=_MAX_RESULTS,
            tweet_fields=["public_metrics", "text", "author_id", "reply_settings"],
            expansions=["author_id"],
            user_fields=["username"],
            sort_order=sort_order,
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
            if eng < effective_min:
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
            f"  [{sort_order[:3]}] Query: {len(resp.data)} raw → "
            f"{spam_count} spam, {offtopic_count} off-topic, {eng_count} low-eng, "
            f"{len(results)} passed | {query[:45]}..."
        )
    except Exception as e:
        print(f"  Query failed: {e} — {query[:45]}...")
    return results


def _fetch_home_timeline(seen_ids: set) -> list:
    """For You feed — Twitter algoritmasının önerdiği niche tweetler.

    Zaten niş+following karışımını döndürüyor. Üstüne spam + off-topic +
    NICHE_KEYWORDS filtresi uygula. OAuth user-auth gerektirir.
    """
    from core.twitter import get_twitter_client_with_bearer
    from core.voice import NICHE_KEYWORDS

    try:
        client = get_twitter_client_with_bearer()
        me = client.get_me()
        if not me.data:
            print("  Home timeline: could not get authenticated user")
            return []
        resp = client.get_home_timeline(
            id=me.data.id,
            max_results=100,
            tweet_fields=["public_metrics", "text", "author_id", "reply_settings"],
            expansions=["author_id"],
            user_fields=["username"],
            exclude=["retweets", "replies"],
        )
        if not resp.data:
            print("  Home timeline: no data returned")
            return []

        users = {u.id: u.username for u in (resp.includes.get("users") or [])}
        results = []
        skip_niche = skip_spam = skip_offtopic = skip_dup = 0
        for tweet in resp.data:
            if tweet.id in seen_ids:
                skip_dup += 1
                continue
            text_lower = tweet.text.lower()
            if not any(kw in text_lower for kw in NICHE_KEYWORDS):
                skip_niche += 1
                continue
            if _is_scam(tweet.text):
                skip_spam += 1
                continue
            if _is_off_topic(tweet.text):
                skip_offtopic += 1
                continue
            eng = _engagement(tweet)
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
            f"  Home timeline: {len(resp.data)} raw → "
            f"{skip_dup} dup, {skip_niche} off-niche, {skip_spam} spam, "
            f"{skip_offtopic} off-topic, {len(results)} passed"
        )
        return results
    except Exception as e:
        print(f"  Home timeline fetch failed ({type(e).__name__}): {e}")
        return []


def _score_and_filter(tweets: list, iq3_threshold: int, rejected_log: list) -> list:
    """Tweetleri puanla, IQ3 eşiği altındakileri rejected_log'a yaz."""
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
    seen_ids: set = set()            # tüm çekilen tweet ID'leri (tekrar önleme)
    rejected_log: list = []
    buckets = {"quote_rt": [], "rt": [], "reply": [], "like": []}
    seen_in_buckets: set = set()     # kovalara atılmış tweet ID'leri

    # BUG FIX #1 (Tour 4): Tüm turlardan gelen puanlı tweetleri biriktir
    # Tour 4'te bu havuzdan IQ3>=82 olanları kurtaracağız — dir() kullanmak gerek yok
    all_raw_pool: list = []

    # ── TUR 1: Ana sorgular — IQ3 >= 115 ──────────────────────────────────────
    # İlk 4 sorgu: relevancy (kanıtlanmış popüler içerik)
    # Son 4 sorgu: recency (taze içerik, düşük engagement eşiği)
    print(f"\n{'='*60}")
    print(f"TUR 1 — Ana sorgular (IQ3>={_IQ3_R1})")
    print(f"  [rel=relevancy, rec=recency — karışık tarama]")
    print('='*60)
    raw1 = []
    for i, query in enumerate(_QUERIES):
        if i < 4:
            raw1.extend(_fetch_query(client, query, seen_ids, sort_order="relevancy"))
        else:
            # Recency: yeni tweetler engagement biriktirmedi → eşiği 3'e düşür
            raw1.extend(_fetch_query(client, query, seen_ids, sort_order="recency", min_engagement=3))
        time.sleep(2)

    raw1.sort(key=lambda t: t["engagement_score"], reverse=True)
    pool_size = min(len(raw1), _SAVE_TOP * 2)
    accepted1 = _score_and_filter(raw1[:pool_size], _IQ3_R1, rejected_log)
    # Puanlama yapıldı, tüm havuza ekle (scores artık kayıtlı)
    all_raw_pool.extend(raw1[:pool_size])
    _fill_buckets(accepted1, buckets, seen_in_buckets)

    total_accepted = sum(len(v) for v in buckets.values())
    print(f"\nTur 1 sonuç: kabul={total_accepted} | "
          f"QRT:{len(buckets['quote_rt'])}/{_QUOTA['quote_rt']} "
          f"RT:{len(buckets['rt'])}/{_QUOTA['rt']} "
          f"Reply:{len(buckets['reply'])}/{_QUOTA['reply']}")

    # ── TUR 1.5: Home Timeline (For You feed) ─────────────────────────────────
    # Twitter algoritmasının zaten niş filtreli önerilerini çek — kullanıcının
    # ana sayfasındaki taze tweetler buradan gelir.
    if not _quotas_full(buckets):
        print(f"\n{'='*60}")
        print(f"TUR 1.5 — Home Timeline (For You feed, NICHE filtreli)")
        print('='*60)
        home_raw = _fetch_home_timeline(seen_ids)
        if home_raw:
            home_raw.sort(key=lambda t: t["engagement_score"], reverse=True)
            accepted_home = _score_and_filter(home_raw, _IQ3_R1, rejected_log)
            all_raw_pool.extend(home_raw)
            _fill_buckets(accepted_home, buckets, seen_in_buckets)

            total_accepted = sum(len(v) for v in buckets.values())
            print(f"\nTur 1.5 sonuç: kabul={total_accepted} | "
                  f"QRT:{len(buckets['quote_rt'])}/{_QUOTA['quote_rt']} "
                  f"RT:{len(buckets['rt'])}/{_QUOTA['rt']} "
                  f"Reply:{len(buckets['reply'])}/{_QUOTA['reply']}")

    # ── TUR 2: Ek sorgular — BUG FIX #2: AND → kotalar dolmamışsa çalış ──────
    # Eski: `and total_accepted < _MIN_ACCEPTED` koşulu Tur 2'yi gereksiz kısıtlıyordu.
    # 16 tweet kabul edilip reply kotası 8/20'de kalsa bile Tur 2 çalışmıyordu.
    if not _quotas_full(buckets):
        print(f"\n{'='*60}")
        print(f"TUR 2 — Ek sorgular (IQ3>={_IQ3_R2})")
        print('='*60)
        raw2 = []
        for query in _EXTENDED_QUERIES:
            # Tur 2: recency — Tur 1 relevancy ile popüler içerik alındı, şimdi taze içerik
            raw2.extend(_fetch_query(client, query, seen_ids, sort_order="recency", min_engagement=3))
            time.sleep(2)

        raw2.sort(key=lambda t: t["engagement_score"], reverse=True)
        accepted2 = _score_and_filter(raw2[:_SAVE_TOP], _IQ3_R2, rejected_log)
        all_raw_pool.extend(raw2[:_SAVE_TOP])
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
            # Tur 3: recency — geniş ama taze içerik
            raw3.extend(_fetch_query(client, query, seen_ids, sort_order="recency", min_engagement=3))
            time.sleep(2)

        raw3.sort(key=lambda t: t["engagement_score"], reverse=True)
        accepted3 = _score_and_filter(raw3[:_SAVE_TOP], _IQ3_R3, rejected_log)
        all_raw_pool.extend(raw3[:_SAVE_TOP])
        _fill_buckets(accepted3, buckets, seen_in_buckets)

        total_accepted = sum(len(v) for v in buckets.values())
        print(f"\nTur 3 sonuç: kabul={total_accepted} | "
              f"QRT:{len(buckets['quote_rt'])}/{_QUOTA['quote_rt']} "
              f"RT:{len(buckets['rt'])}/{_QUOTA['rt']} "
              f"Reply:{len(buckets['reply'])}/{_QUOTA['reply']}")

    # ── TUR 4: Kurtarma — eşiği daha da düşür ────────────────────────────────
    # BUG FIX #1: dir() kullanan kırık rescue mantığı düzeltildi.
    # Artık all_raw_pool (tüm turlardan biriktirilmiş, zaten puanlı) kullanılıyor.
    # Boş döngü (for entry in rejected_log: pass) silindi.
    if not _quotas_full(buckets):
        print(f"\n{'='*60}")
        print(f"TUR 4 — Kurtarma: tüm havuzdan IQ3>={_IQ3_R4} eşiğiyle")
        print('='*60)
        rescue_candidates = [
            t for t in all_raw_pool
            if t.get("scores", {}).get("iq3", 0) >= _IQ3_R4
            and t["tweet_id"] not in seen_in_buckets
        ]
        rescue_candidates.sort(key=lambda t: t["engagement_score"], reverse=True)
        print(f"  Kurtarma havuzu: {len(rescue_candidates)} tweet (IQ3>={_IQ3_R4})")
        _fill_buckets(rescue_candidates, buckets, seen_in_buckets)

        total_accepted = sum(len(v) for v in buckets.values())
        print(f"\nTur 4 sonuç: kabul={total_accepted} | "
              f"QRT:{len(buckets['quote_rt'])}/{_QUOTA['quote_rt']} "
              f"RT:{len(buckets['rt'])}/{_QUOTA['rt']} "
              f"Reply:{len(buckets['reply'])}/{_QUOTA['reply']}")

    # ── Sonuç: Tüm kategorileri birleştir ────────────────────────────────────
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
        print(f"  En iyi: @{best['author']} IQ3={s.get('iq3','?')} IQ={s.get('iq','?')} "
              f"eng={best['engagement_score']} cat={best.get('category','?')}")
    print('='*60)
    return top


if __name__ == "__main__":
    results = run_daily_scan()
    print(f"Top tweet: {results[0]['text'][:80] if results else 'none'}...")
