import os
import json
import random
import re
import tweepy
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
import time

# --- 1. API KEYS ---
TWITTER_API_KEY = os.environ.get("TWITTER_API_KEY")
TWITTER_API_SECRET = os.environ.get("TWITTER_API_SECRET")
TWITTER_ACCESS_TOKEN = os.environ.get("TWITTER_ACCESS_TOKEN")
TWITTER_ACCESS_TOKEN_SECRET = os.environ.get("TWITTER_ACCESS_TOKEN_SECRET")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

# --- TYPE LABELS ---
TYPE_LABELS = {
    'glb': '[3D Asset]',
    'vrm': '[Avatar/VRM]',
    'video': '[Video Archive]',
    'image': '[Visual Archive]',
    'html': '[Web Experience]',
    'website': '[Web Experience]',
    'text': '[Manifesto/Text]',
    'pdf': '[Document]'
}

# Sabah tweeti için sadece direkt upload edilebilen tipler
MEDIA_TYPES = {'image', 'video'}

def is_youtube(url):
    return 'youtube.com' in url or 'youtu.be' in url

def get_twitter_clients():
    client = tweepy.Client(
        consumer_key=TWITTER_API_KEY,
        consumer_secret=TWITTER_API_SECRET,
        access_token=TWITTER_ACCESS_TOKEN,
        access_token_secret=TWITTER_ACCESS_TOKEN_SECRET
    )
    auth = tweepy.OAuth1UserHandler(
        TWITTER_API_KEY, TWITTER_API_SECRET,
        TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET
    )
    api = tweepy.API(auth, wait_on_rate_limit=True)
    return client, api

def load_db():
    with open('database.json', 'r', encoding='utf-8') as file:
        return json.load(file)

def fix_wix_video_url(url):
    """Wix video URL'lerini indirilebilir formata çevirir."""
    if 'video.wixstatic.com' not in url:
        return url
    url = url.rstrip('/')
    if url.endswith('/mp4'):
        return url + '/file.mp4'
    if not url.endswith('.mp4'):
        return url + '/mp4/file.mp4'
    return url

def download_media(media_url):
    try:
        media_url = fix_wix_video_url(media_url)
        ext = ".jpg"
        lower_url = media_url.lower()
        if ".mp4" in lower_url or "video.wixstatic.com" in lower_url: ext = ".mp4"
        elif ".png" in lower_url: ext = ".png"
        elif ".gif" in lower_url: ext = ".gif"

        temp_filename = f"temp_media_{int(time.time())}{ext}"
        headers = {'User-Agent': 'Mozilla/5.0'}

        response = requests.get(media_url, stream=True, headers=headers, timeout=15)
        if response.status_code == 200:
            with open(temp_filename, 'wb') as f:
                for chunk in response.iter_content(1024 * 1024):
                    f.write(chunk)
            return temp_filename
    except Exception as e:
        print(f"Media download failed: {e}")
    return None

def extract_data_from_html(url):
    text_content = None
    media_url = None
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code == 200:
            soup = BeautifulSoup(response.content, 'html.parser')

            paragraphs = soup.find_all(['p', 'h1', 'h2', 'div'])
            texts = [p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 30]
            if texts:
                text_content = texts[0]

            video_tag = soup.find('source', src=True)
            if video_tag:
                media_url = video_tag['src']
            else:
                og_image = soup.find('meta', property='og:image')
                if og_image and og_image.get('content'):
                    media_url = og_image['content']
                else:
                    img_tag = soup.find('img', src=True)
                    if img_tag:
                        media_url = img_tag['src']

            if media_url and media_url.startswith('/'):
                base_url = "/".join(url.split("/")[:3])
                media_url = f"{base_url}{media_url}"

    except Exception as e:
        print(f"HTML Scrape Error: {e}")

    return text_content, media_url

# --- MORNING: MULTIMEDIA ARCHIVE ---
def post_morning_tweet():
    client, api = get_twitter_clients()
    db = load_db()

    media_items = []
    for category, items in db.items():
        if isinstance(items, list):
            for item in items:
                if item.get('type') in MEDIA_TYPES:
                    media_items.append(item)

    selected = random.choice(media_items)
    name = selected.get('name', 'ARCHIVE_ITEM')
    item_type = selected.get('type', 'image')
    type_label = TYPE_LABELS.get(item_type, '[Archive]')
    desc = selected.get('description', '')

    raw_url = selected.get('url', 'https://decentralize.design')
    url = raw_url.replace('digitalforgerywork.shop', 'decentralize.design')

    # Açıklama metni oluştur
    display_text = desc.split('.')[0] + "." if desc else ""
    if len(display_text) > 120:
        display_text = display_text[:117] + "..."

    # YouTube: video upload edilemez, URL tweete eklenir — Twitter kart önizlemesi gösterir
    if is_youtube(url):
        tweet_text = f"{type_label} {name}\n\n{display_text}\n\n{url}"
        if len(tweet_text) > 280:
            tweet_text = tweet_text[:277] + "..."
        client.create_tweet(text=tweet_text)
        print(f"Morning broadcast (YouTube card): {name}")
        return

    # Wix ve direkt medya URL'leri: indir ve upload et
    media_url_to_download = None
    if any(ext in url.lower() for ext in ['.mp4', '.png', '.jpg', '.jpeg', '.gif']) or 'video.wixstatic.com' in url:
        media_url_to_download = url
    elif selected.get('thumbnailUrl'):
        media_url_to_download = selected['thumbnailUrl'].replace('digitalforgerywork.shop', 'decentralize.design')
    elif selected.get('iconUrl'):
        media_url_to_download = selected['iconUrl'].replace('digitalforgerywork.shop', 'decentralize.design')

    media_ids = []
    if media_url_to_download:
        print(f"Downloading media: {media_url_to_download}")
        local_file = download_media(media_url_to_download)
        if local_file:
            try:
                if local_file.endswith('.mp4'):
                    print("Uploading Video (Chunked)...")
                    media = api.media_upload(local_file, media_category='tweet_video', chunked=True)
                else:
                    print("Uploading Image...")
                    media = api.media_upload(local_file)
                media_ids.append(media.media_id)
                os.remove(local_file)
            except Exception as e:
                print(f"Upload failed: {e}")
                if os.path.exists(local_file): os.remove(local_file)

    tweet_text = f"{type_label} {name}\n\n{display_text}"
    if len(tweet_text) > 280:
        tweet_text = tweet_text[:277] + "..."

    if media_ids:
        client.create_tweet(text=tweet_text, media_ids=media_ids)
    else:
        client.create_tweet(text=tweet_text)

    print(f"Morning broadcast complete: {name}")

def distill_to_tweet(chunk, source_name):
    import groq as groq_sdk
    groq_client = groq_sdk.Groq(api_key=GROQ_API_KEY)
    prompt = (
        "You write for a digital design studio (Decentralize Design) that builds virtual worlds and manifestos.\n"
        "From the text below, extract or rephrase ONE powerful, self-contained thought as a tweet (max 240 chars).\n"
        "Rules: no quotes around it, no 'we believe / this manifesto / our studio', reads as a standalone statement.\n"
        f"Output only the tweet text.\n\nSource: {source_name}\nText:\n{chunk}"
    )
    resp = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=80,
        temperature=0.8,
    )
    return resp.choices[0].message.content.strip()


# --- EVENING: ARCHIVE THOUGHT ---
def post_evening_tweet():
    client, _ = get_twitter_clients()
    db = load_db()

    text_items = [
        item
        for category, items in db.items()
        if isinstance(items, list)
        for item in items
        if item.get('type') == 'text' and len(item.get('content', '')) > 500
    ]

    selected = random.choice(text_items)
    content = selected.get('content', '')
    name = selected.get('name', '')

    # Rastgele ~150 kelimelik parça seç
    words = content.split()
    if len(words) > 150:
        start = random.randint(0, len(words) - 150)
        chunk = ' '.join(words[start:start + 150])
    else:
        chunk = content

    # LLM ile distill, başarısızsa cümle bazlı fallback
    tweet_text = None
    if GROQ_API_KEY:
        try:
            tweet_text = distill_to_tweet(chunk, name)
        except Exception as e:
            print(f"Groq error: {e}")

    if not tweet_text:
        sentences = [
            s.strip() for s in re.split(r'(?<=[.!?])\s+', content)
            if 70 < len(s.strip()) < 240
            and not s.strip().isupper()
            and '\n' not in s.strip()[:30]
        ]
        tweet_text = random.choice(sentences) if sentences else content[:240]

    if len(tweet_text) > 280:
        tweet_text = tweet_text[:277] + "..."

    print(f"Attempting evening tweet:\n{tweet_text}")
    try:
        client.create_tweet(text=tweet_text)
        print(f"Evening broadcast complete:\n{tweet_text}")
    except tweepy.errors.Forbidden as e:
        api_codes = getattr(e, 'api_codes', [])
        api_messages = getattr(e, 'api_messages', [])
        print(f"Twitter 403 — codes: {api_codes}, messages: {api_messages}")
        # 187 = duplicate status; retry with a fresh chunk
        if 187 in api_codes and GROQ_API_KEY:
            print("Duplicate detected, retrying with a different chunk...")
            words = content.split()
            start = random.randint(0, max(0, len(words) - 150))
            new_chunk = ' '.join(words[start:start + 150])
            tweet_text = distill_to_tweet(new_chunk, name)
            if len(tweet_text) > 280:
                tweet_text = tweet_text[:277] + "..."
            print(f"Retry tweet:\n{tweet_text}")
            client.create_tweet(text=tweet_text)
            print(f"Evening broadcast complete (retry):\n{tweet_text}")
        else:
            raise


if __name__ == "__main__":
    current_utc_hour = datetime.now(timezone.utc).hour

    # UTC 6 = TR Saati 09:00 / UTC 16 = TR Saati 19:00
    if current_utc_hour in [5, 6, 7]:
        post_morning_tweet()
    elif current_utc_hour in [15, 16, 17]:
        post_evening_tweet()
    else:
        print(f"Test Mode (Hour: {current_utc_hour} UTC). Running Evening routine...")
        post_evening_tweet()
