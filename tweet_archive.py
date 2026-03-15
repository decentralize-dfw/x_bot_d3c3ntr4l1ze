import json
import os
import re
from datetime import datetime, timedelta

ARCHIVE_PATH = os.path.join(os.path.dirname(__file__), "tweet_archive.json")
COOLDOWN_DAYS = 60

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


def _keywords(text):
    """Return a set of meaningful lowercased tokens from text."""
    tokens = re.findall(r"[a-z]+", text.lower())
    return {t for t in tokens if t not in _STOP_WORDS and len(t) > 2}


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
    cutoff = datetime.utcnow() - timedelta(days=days)
    for entry in entries:
        if entry["content_id"] == content_id:
            posted_at = datetime.fromisoformat(entry["posted_at"])
            if posted_at > cutoff:
                return True
    return False


def record_post(content_id, content_type="unknown", tweet_text=None):
    entries = load_archive()
    entries = [e for e in entries if e["content_id"] != content_id]
    entry = {
        "content_id": content_id,
        "content_type": content_type,
        "posted_at": datetime.utcnow().isoformat()
    }
    if tweet_text:
        entry["tweet_text"] = tweet_text
    entries.append(entry)
    save_archive(entries)


def get_recent_tweet_texts(days=COOLDOWN_DAYS):
    """Return list of stored tweet texts posted within the last N days."""
    entries = load_archive()
    cutoff = datetime.utcnow() - timedelta(days=days)
    texts = []
    for entry in entries:
        posted_at = datetime.fromisoformat(entry["posted_at"])
        if posted_at > cutoff and entry.get("tweet_text"):
            texts.append(entry["tweet_text"])
    return texts


def is_too_similar(candidate, days=COOLDOWN_DAYS, threshold=0.35):
    """Return True if candidate is too similar to any recently posted tweet.

    Uses Jaccard similarity on meaningful keywords (stop words removed).
    A threshold of 0.35 means 35% keyword overlap triggers rejection.
    """
    candidate_keys = _keywords(candidate)
    if not candidate_keys:
        return False
    recent_texts = get_recent_tweet_texts(days=days)
    for text in recent_texts:
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
    cutoff = datetime.utcnow() - timedelta(days=days)
    fresh = [e for e in entries if datetime.fromisoformat(e["posted_at"]) > cutoff]
    if len(fresh) < len(entries):
        save_archive(fresh)
        print(f"Archive: removed {len(entries) - len(fresh)} expired entries.")
    return fresh
