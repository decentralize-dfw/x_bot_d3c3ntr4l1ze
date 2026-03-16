"""
core/twitter.py
---------------
Twitter client başlatma + medya upload yardımcıları.
"""
import os
import time

import tweepy

from utils.http import get_session, MAX_MEDIA_BYTES

TWITTER_API_KEY             = os.environ.get("TWITTER_API_KEY")
TWITTER_API_SECRET          = os.environ.get("TWITTER_API_SECRET")
TWITTER_ACCESS_TOKEN        = os.environ.get("TWITTER_ACCESS_TOKEN")
TWITTER_ACCESS_TOKEN_SECRET = os.environ.get("TWITTER_ACCESS_TOKEN_SECRET")
TWITTER_BEARER_TOKEN        = os.environ.get("BEARERTOKEN")


def get_twitter_clients():
    """(tweepy.Client, tweepy.API) tuple döndür."""
    client = tweepy.Client(
        consumer_key=TWITTER_API_KEY,
        consumer_secret=TWITTER_API_SECRET,
        access_token=TWITTER_ACCESS_TOKEN,
        access_token_secret=TWITTER_ACCESS_TOKEN_SECRET,
    )
    auth = tweepy.OAuth1UserHandler(
        TWITTER_API_KEY, TWITTER_API_SECRET,
        TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET,
    )
    api = tweepy.API(auth, wait_on_rate_limit=True)
    return client, api


def get_twitter_client_with_bearer():
    """Bearer token dahil tam client (search için)."""
    return tweepy.Client(
        bearer_token=TWITTER_BEARER_TOKEN,
        consumer_key=TWITTER_API_KEY,
        consumer_secret=TWITTER_API_SECRET,
        access_token=TWITTER_ACCESS_TOKEN,
        access_token_secret=TWITTER_ACCESS_TOKEN_SECRET,
    )


def fix_wix_video_url(url: str) -> str:
    """Wix video URL'lerini indirilebilir formata çevirir."""
    if "video.wixstatic.com" not in url:
        return url
    url = url.rstrip("/")
    if url.endswith("/mp4"):
        return url + "/file.mp4"
    if not url.endswith(".mp4"):
        return url + "/mp4/file.mp4"
    return url


def download_media(media_url: str) -> str | None:
    """Medyayı indir, geçici dosyaya yaz, dosya adını döndür.

    100MB hard limit (Faz 1.3 — rapor2.txt §1.3).
    Retry session kullanır (Faz 1.1 — rapor2.txt §1.1).
    """
    try:
        media_url = fix_wix_video_url(media_url)
        session = get_session()
        resp = session.get(media_url, stream=True, timeout=30)
        if resp.status_code != 200:
            print(f"Media download HTTP {resp.status_code}: {media_url}")
            return None

        ct = resp.headers.get("Content-Type", "").lower()
        lower_url = media_url.lower()
        if "video" in ct or ".mp4" in lower_url or "video.wixstatic.com" in lower_url:
            ext = ".mp4"
        elif "png" in ct or ".png" in lower_url:
            ext = ".png"
        elif "gif" in ct or ".gif" in lower_url:
            ext = ".gif"
        else:
            ext = ".jpg"

        temp_filename = f"temp_media_{int(time.time())}{ext}"
        total = 0
        with open(temp_filename, "wb") as f:
            for chunk in resp.iter_content(8192):
                total += len(chunk)
                if total > MAX_MEDIA_BYTES:
                    raise ValueError(
                        f"Media too large: >{MAX_MEDIA_BYTES // 1024 // 1024}MB — aborted."
                    )
                f.write(chunk)
        return temp_filename
    except Exception as e:
        print(f"Media download failed: {e}")
        return None
