import json
import os
import re
from datetime import datetime, timedelta, timezone


def _utcnow():
    """Timezone-aware UTC şimdiki zaman. Naive timestamp legacy'i de handle eder."""
    return datetime.now(timezone.utc)


def _parse_dt(s):
    """ISO string'i timezone-aware datetime'a çevirir. Naive string ise UTC kabul eder."""
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

ARCHIVE_PATH = os.path.join(os.path.dirname(__file__), "tweet_archive.json")
COOLDOWN_DAYS = 60

# Tema bazlı ek cooldown (gün). Bir tema bu kadar gün içinde tekrar edilemez.
THEME_COOLDOWN = {
    "vr_failure":        14,
    "vr_retention":      14,
    "ownership":         21,
    "webxr":              7,
    "spatial_computing": 10,
    "metaverse":         10,
    "browser":           12,
    "3d_web":             8,
    "digital_identity":  14,
    "architecture":      10,
    "decentralization":  16,
    "ai_design":          9,
}

_STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "can", "not", "but", "and", "or",
    "for", "in", "on", "at", "to", "of", "with", "that", "this", "it",
    "its", "by", "we", "you", "they", "their", "our", "your", "which",
    "from", "been", "into", "as", "so", "if", "when", "then", "than",
    "no", "more", "just", "like", "about", "over", "still", "only",
    "what", "how", "who", "there", "here", "very", "too", "even",
    "now", "all", "some", "any", "each", "one", "two", "s", "t", "re",
    "don", "doesn", "isn", "aren", "wasn", "weren", "won", "ll", "ve", "m",
}

# Tema → anahtar kelime eşleşmesi (basit keyword mapping)
_THEME_KEYWORDS = {
    "vr_failure":        ["vr", "virtual reality", "oculus", "headset", "collapse", "fail", "dead"],
    "vr_retention":      ["retention", "return", "inhabitants", "visitor", "tourist", "live"],
    "ownership":         ["ownership", "own", "onchain", "on-chain", "wallet", "nft", "asset"],
    "webxr":             ["webxr", "web xr", "xr", "browser 3d", "three.js", "threejs"],
    "spatial_computing": ["spatial", "computing", "apple vision", "mixed reality", "mr"],
    "metaverse":         ["metaverse", "virtual world", "digital world", "roblox", "fortnite"],
    "browser":           ["browser", "web", "html", "internet", "decentralize publishing"],
    "3d_web":            ["3d web", "3d design", "webgl", "glb", "spatial web"],
    "digital_identity":  ["identity", "avatar", "digital self", "profile", "persona"],
    "architecture":      ["architecture", "architect", "design", "space", "permanence", "build"],
    "decentralization":  ["decentrali", "p2p", "distributed", "autonomous", "trustless"],
    "ai_design":         ["ai", "generated", "generative", "midjourney", "stable diffusion"],
}


def _keywords(text):
    tokens = re.findall(r"[a-z]+", text.lower())
    return {t for t in tokens if t not in _STOP_WORDS and len(t) > 2}


def _detect_theme(tweet_text: str) -> str | None:
    lower = tweet_text.lower()
    for theme, keywords in _THEME_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                return theme
    return None


def load_archive():
    if not os.path.exists(ARCHIVE_PATH):
        return []
    with open(ARCHIVE_PATH, "r") as f:
        return json.load(f)


def save_archive(entries):
    with open(ARCHIVE_PATH, "w") as f:
        json.dump(entries, f, indent=2)


def is_posted_recently(content_id, days=COOLDOWN_DAYS):
    entries = load_archive()
    cutoff = _utcnow() - timedelta(days=days)
    for entry in entries:
        if entry["content_id"] == content_id:
            posted_at = _parse_dt(entry["posted_at"])
            if posted_at > cutoff:
                return True
    return False


def is_theme_in_cooldown(tweet_text: str) -> bool:
    """Aynı tema bu cooldown penceresinde zaten atıldıysa True döner."""
    theme = _detect_theme(tweet_text)
    if not theme:
        return False
    cooldown_days = THEME_COOLDOWN.get(theme, 7)
    entries = load_archive()
    cutoff = _utcnow() - timedelta(days=cooldown_days)
    for entry in entries:
        if entry.get("theme") == theme:
            posted_at = _parse_dt(entry["posted_at"])
            if posted_at > cutoff:
                print(f"Theme cooldown: '{theme}' posted within last {cooldown_days} days.")
                return True
    return False


def record_post(content_id, content_type="unknown", tweet_text=None, tweet_id=None):
    entries = load_archive()
    entries = [e for e in entries if e["content_id"] != content_id]
    entry = {
        "content_id":   content_id,
        "content_type": content_type,
        "posted_at":    _utcnow().isoformat(),
    }
    if tweet_text:
        entry["tweet_text"] = tweet_text
        theme = _detect_theme(tweet_text)
        if theme:
            entry["theme"] = theme
    if tweet_id:
        entry["tweet_id"] = str(tweet_id)
    entries.append(entry)
    save_archive(entries)


def get_recent_tweet_texts(days=COOLDOWN_DAYS):
    entries = load_archive()
    cutoff = _utcnow() - timedelta(days=days)
    return [
        e["tweet_text"]
        for e in entries
        if _parse_dt(e["posted_at"]) > cutoff and e.get("tweet_text")
    ]


def get_recent_tweet_ids(hours=48):
    """Son N saatte atılan tweet'lerin Twitter ID'lerini döndür (analytics için)."""
    entries = load_archive()
    cutoff = _utcnow() - timedelta(hours=hours)
    return [
        e["tweet_id"]
        for e in entries
        if _parse_dt(e["posted_at"]) > cutoff and e.get("tweet_id")
    ]


def is_too_similar(candidate, days=COOLDOWN_DAYS, threshold=0.35):
    """Jaccard keyword similarity ile yakın zamanda atılan tweet'lere benzerlik kontrolü."""
    candidate_keys = _keywords(candidate)
    if not candidate_keys:
        return False
    for text in get_recent_tweet_texts(days=days):
        other_keys = _keywords(text)
        if not other_keys:
            continue
        intersection = candidate_keys & other_keys
        union = candidate_keys | other_keys
        similarity = len(intersection) / len(union)
        if similarity >= threshold:
            print(f"Similarity check: {similarity:.2f} >= {threshold} — too similar to recent tweet.")
            return True
    return False


def cleanup_old_entries(days=COOLDOWN_DAYS):
    entries = load_archive()
    cutoff = _utcnow() - timedelta(days=days)
    fresh = [e for e in entries if _parse_dt(e["posted_at"]) > cutoff]
    if len(fresh) < len(entries):
        save_archive(fresh)
        print(f"Archive: removed {len(entries) - len(fresh)} expired entries.")
    return fresh
