"""
brain_wip.py
------------
Günlük scan + following scan sonrası kaliteli / niche tweetleri arşivler.

Filtre kriterleri:
  - Niche keyword eşleşmesi (geniş konu listesi)
  - Min. 60 karakter (anlamlı içerik)
  - Scam pattern yok (scan_results zaten filtreli; following'den gelen kontrol edilir)
  - Duplicate yok (tweet_id bazlı)

Kaynak:
  - scan_results.json   → niche_scan
  - following_archive.json → following

Çıktı: brain_wip.json
"""
import json
import os
import re
from datetime import datetime, timezone

SCAN_PATH      = os.path.join(os.path.dirname(__file__), "scan_results.json")
FOLLOWING_PATH = os.path.join(os.path.dirname(__file__), "following_archive.json")
BRAIN_PATH     = os.path.join(os.path.dirname(__file__), "brain_wip.json")

# Geniş niche keyword listesi — spatial, web3, fashion/culture, AI, design dahil
_NICHE_KEYWORDS = re.compile(
    r"""
    \b(
      webxr|metaverse|spatial|virtual\s+reality|augmented\s+reality|mixed\s+reality|
      3d\s+web|immersive|decentrali|web3|on[-\s]?chain|digital\s+twin|
      volumetric|holograph|generative|procedural|realtime\s+3d|
      digital\s+art|crypto\s+art|nft|blockchain|ethereum|
      phygital|digital\s+fashion|avatar|virtual\s+world|
      spatial\s+computing|apple\s+vision|vr\b|xr\b|ar\b|
      architecture|design\s+office|type\s+design|typography|
      ai\s+art|ai\s+music|open\s+source|p2p|trustless|
      museum\s+of\s+crypto|open\s+metaverse|hyperfy|
      ownership|permanence|identity|on[-\s]?chain|
      subcultural|street\s+culture|fashion\s+house|
      midi|algorithmic|sound\s+design|machine\s+learning
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Minimum içerik uzunluğu
_MIN_CHARS = 60

# scan_results için minimum engagement
_MIN_SCAN_ENGAGEMENT = 15


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _is_niche(text: str) -> bool:
    return bool(_NICHE_KEYWORDS.search(text))


def _is_substantial(text: str) -> bool:
    return len(text.strip()) >= _MIN_CHARS


def _collect_from_scan(existing_ids: set) -> list:
    """scan_results.json'dan kaliteli niche tweetler."""
    data = _load_json(SCAN_PATH, [])
    collected = []
    for t in data:
        if t["tweet_id"] in existing_ids:
            continue
        if t.get("engagement_score", 0) < _MIN_SCAN_ENGAGEMENT:
            continue
        if not _is_substantial(t["text"]):
            continue
        if not _is_niche(t["text"]):
            continue
        collected.append({
            "tweet_id":  t["tweet_id"],
            "author":    t["author"],
            "text":      t["text"],
            "source":    "niche_scan",
            "engagement": t.get("engagement_score", 0),
            "added_at":  _utcnow(),
        })
    return collected


def _collect_from_following(existing_ids: set) -> list:
    """following_archive.json'dan niche tweetler (curated liste, eşik daha düşük)."""
    data = _load_json(FOLLOWING_PATH, {})
    tweets = data.get("tweets", [])
    collected = []
    for t in tweets:
        if t["tweet_id"] in existing_ids:
            continue
        if not _is_substantial(t["text"]):
            continue
        if not _is_niche(t["text"]):
            continue
        collected.append({
            "tweet_id":  t["tweet_id"],
            "author":    t["author"],
            "text":      t["text"],
            "source":    "following",
            "engagement": 0,
            "added_at":  _utcnow(),
        })
    return collected


def run_brain_wip() -> int:
    """Brain WIP arşivini güncelle. Eklenen tweet sayısını döndür."""
    archive = _load_json(BRAIN_PATH, {"entries": []})
    entries = archive.get("entries", [])
    existing_ids = {e["tweet_id"] for e in entries}

    new_entries = []
    new_entries += _collect_from_scan(existing_ids)

    # Following ekle (ID'ler scan'den gelebilir, tekrar kontrol)
    all_ids = existing_ids | {e["tweet_id"] for e in new_entries}
    new_entries += _collect_from_following(all_ids)

    if new_entries:
        entries.extend(new_entries)
        tmp = BRAIN_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"entries": entries}, f, indent=2, ensure_ascii=False)
        os.replace(tmp, BRAIN_PATH)
        print(f"BRAIN-WIP: +{len(new_entries)} new entries (total: {len(entries)})")
    else:
        print(f"BRAIN-WIP: no new entries (total: {len(entries)})")

    return len(new_entries)


if __name__ == "__main__":
    run_brain_wip()
