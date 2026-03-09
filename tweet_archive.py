import json
import os
from datetime import datetime, timedelta

ARCHIVE_PATH = os.path.join(os.path.dirname(__file__), "tweet_archive.json")
COOLDOWN_DAYS = 60


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


def record_post(content_id, content_type="unknown"):
    entries = load_archive()
    entries = [e for e in entries if e["content_id"] != content_id]
    entries.append({
        "content_id": content_id,
        "content_type": content_type,
        "posted_at": datetime.utcnow().isoformat()
    })
    save_archive(entries)


def cleanup_old_entries(days=COOLDOWN_DAYS):
    entries = load_archive()
    cutoff = datetime.utcnow() - timedelta(days=days)
    fresh = [e for e in entries if datetime.fromisoformat(e["posted_at"]) > cutoff]
    if len(fresh) < len(entries):
        save_archive(fresh)
        print(f"Archive: removed {len(entries) - len(fresh)} expired entries.")
    return fresh
