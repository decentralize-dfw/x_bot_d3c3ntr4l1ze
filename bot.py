import os
import sys
import json
import random
import re
import tweepy
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
import time
import groq as groq_sdk

# --- 1. API KEYS ---
TWITTER_API_KEY = os.environ.get("TWITTER_API_KEY")
TWITTER_API_SECRET = os.environ.get("TWITTER_API_SECRET")
TWITTER_ACCESS_TOKEN = os.environ.get("TWITTER_ACCESS_TOKEN")
TWITTER_ACCESS_TOKEN_SECRET = os.environ.get("TWITTER_ACCESS_TOKEN_SECRET")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

# --- REPLY / VIRAL CONFIG ---
# Add Twitter usernames (without @) to reply specifically to their tweets.
# If empty, the bot searches by SEARCH_KEYWORDS instead.
WATCH_ACCOUNTS = []

# Keyword search query used for viral context and reply targeting.
SEARCH_KEYWORDS = (
    '(metaverse OR "virtual world" OR "AI agent" OR "on-chain" OR '
    '"spatial computing" OR "web3" OR "3D NFT")'
)

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
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(media_url, stream=True, headers=headers, timeout=30)
        if response.status_code == 200:
            ct = response.headers.get('Content-Type', '').lower()
            lower_url = media_url.lower()
            if 'video' in ct or '.mp4' in lower_url or 'video.wixstatic.com' in lower_url:
                ext = '.mp4'
            elif 'png' in ct or '.png' in lower_url:
                ext = '.png'
            elif 'gif' in ct or '.gif' in lower_url:
                ext = '.gif'
            else:
                ext = '.jpg'
            temp_filename = f"temp_media_{int(time.time())}{ext}"
            with open(temp_filename, 'wb') as f:
                for chunk in response.iter_content(1024 * 1024):
                    f.write(chunk)
            return temp_filename
        else:
            print(f"Media download HTTP {response.status_code}: {media_url}")
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

    # YouTube: video upload edilemez, URL tweete eklenir — Twitter kart önizlemesi gösterir
    if is_youtube(url):
        display_text = desc[:117] + "..." if len(desc) > 120 else desc
        tweet_text = f"{type_label} {name}\n\n{display_text}\n\n{url}"
        if len(tweet_text) > 280:
            tweet_text = tweet_text[:277] + "..."
        client.create_tweet(text=tweet_text)
        print(f"Morning broadcast (YouTube card): {name}")
        return

    # image/video type: url field IS the media file (Arweave, GitHub raw, Wix, etc.)
    media_url_to_download = url

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
                print(f"Upload failed ({type(e).__name__}): {e}")
                if os.path.exists(local_file): os.remove(local_file)

    # Build caption: LLM first, fallback to first complete sentence from desc
    display_text = ""
    if GROQ_API_KEY:
        try:
            display_text = generate_media_caption(name, desc, type_label)
        except Exception as e:
            print(f"Caption generation error: {e}")
    if not display_text:
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', desc) if 30 < len(s.strip()) <= 140]
        display_text = sentences[0] if sentences else (desc[:137] + "..." if len(desc) > 140 else desc)

    tweet_text = f"{type_label} {name}\n\n{display_text}"
    if len(tweet_text) > 280:
        tweet_text = tweet_text[:277] + "..."

    if media_ids:
        client.create_tweet(text=tweet_text, media_ids=media_ids)
    else:
        client.create_tweet(text=tweet_text)

    print(f"Morning broadcast complete: {name}")


# --- LLM HELPERS ---

def distill_to_tweet(chunk, source_name):
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


def fetch_context_tweets(query, max_results=10):
    """Fetch recent high-engagement tweets for use as creative context."""
    client, _ = get_twitter_clients()
    try:
        resp = client.search_recent_tweets(
            query=f"{query} -is:retweet -is:reply lang:en",
            max_results=max_results,
            tweet_fields=["public_metrics", "text"],
            sort_order="relevancy",
        )
        if not resp.data:
            return []
        tweets = sorted(
            resp.data,
            key=lambda t: (
                t.public_metrics.get("like_count", 0)
                + t.public_metrics.get("retweet_count", 0) * 2
            ),
            reverse=True,
        )
        return [t.text for t in tweets[:3]]
    except Exception as e:
        print(f"Context fetch error: {e}")
        return []


def generate_viral_tweet(chunk, source_name, context_tweets):
    """LLM: viral-format tweet from manifesto content + trending context."""
    groq_client = groq_sdk.Groq(api_key=GROQ_API_KEY)

    context_str = ""
    if context_tweets:
        context_str = (
            "Trending conversation in this space (inspiration only, do NOT copy or quote):\n"
            + "\n---\n".join(context_tweets[:2])
            + "\n\n"
        )

    prompt = (
        "You are the voice of @decentralize___, a studio building 3D virtual worlds on-chain.\n"
        "Voice: visionary, punk architect, no corporate speak.\n\n"
        "Write ONE viral tweet. Pick the sharpest format for the content below:\n"
        "A. Bold claim + short proof + declaration\n"
        "B. Contrast (physical world vs virtual)\n"
        "C. Prediction + implication\n"
        "D. Hot take + one supporting line + call to see differently\n\n"
        "Rules: NO hashtags. Max 240 chars. First line must hook. "
        "Sound like someone who has seen the future, not a marketer.\n\n"
        f"{context_str}"
        f"Our philosophy (source: {source_name}):\n{chunk}\n\n"
        "Output ONLY the tweet text."
    )
    resp = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=120,
        temperature=0.9,
    )
    return resp.choices[0].message.content.strip()


def generate_reply(target_tweet_text, manifesto_chunk):
    """LLM: contextual reply to a given tweet, in our voice."""
    groq_client = groq_sdk.Groq(api_key=GROQ_API_KEY)
    prompt = (
        "You are @decentralize___, a studio building 3D virtual worlds on-chain.\n"
        "Reply to the tweet below from your perspective. Add a genuine angle, spark conversation.\n"
        "Under 200 chars. No hashtags. Don't be sycophantic. Offer a real point of view.\n\n"
        f"Tweet: {target_tweet_text}\n\n"
        f"Your world (context only): {manifesto_chunk[:300]}\n\n"
        "Output ONLY the reply text."
    )
    resp = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=80,
        temperature=0.85,
    )
    return resp.choices[0].message.content.strip()


def generate_media_caption(name, description, type_label):
    """LLM: one complete, self-contained caption for a media item. ≤120 chars."""
    groq_client = groq_sdk.Groq(api_key=GROQ_API_KEY)
    prompt = (
        "You write captions for @decentralize___ (a studio building 3D virtual worlds on-chain).\n"
        "Write ONE complete sentence caption for this media item. Max 120 characters.\n"
        "Rules: complete sentence, makes sense to someone who has never heard of this project, "
        "no hashtags, no studio name, no 'this is...'. State what it IS or what it MEANS.\n\n"
        f"Item: {type_label} — {name}\n"
        f"Description: {description}\n\n"
        "Output ONLY the caption."
    )
    resp = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=50,
        temperature=0.7,
    )
    return resp.choices[0].message.content.strip()


# --- EVENING: VIRAL MANIFESTO TWEET ---
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

    words = content.split()
    if len(words) > 150:
        start = random.randint(0, len(words) - 150)
        chunk = ' '.join(words[start:start + 150])
    else:
        chunk = content

    tweet_text = None
    if GROQ_API_KEY:
        try:
            context_tweets = fetch_context_tweets(SEARCH_KEYWORDS)
            tweet_text = generate_viral_tweet(chunk, name, context_tweets)
        except Exception as e:
            print(f"Viral generation error: {e}, falling back to distill...")
            try:
                tweet_text = distill_to_tweet(chunk, name)
            except Exception as e2:
                print(f"Groq error: {e2}")

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


# --- 23:00 (TR): ARTWORK DROP ---
def post_artwork_tweet():
    """Pick a random artwork, post image + metadata, then reply with site link."""
    client, api = get_twitter_clients()

    with open('artworks.json', 'r', encoding='utf-8') as f:
        artworks = json.load(f)

    if not artworks:
        print("No artworks found in artworks.json.")
        return

    artwork = random.choice(artworks)
    name = artwork.get('name', '')
    description = artwork.get('description', '')
    cats = artwork.get('categories', {})

    # Build metadata block — each field on its own line
    lines = [name, description, '']
    for key in ['year', 'type', 'subtype', 'collection', 'medium', 'expression', 'reality', 'contents']:
        val = cats.get(key)
        if val is not None:
            lines.append(str(val))

    tweet_text = '\n'.join(lines).strip()
    if len(tweet_text) > 280:
        tweet_text = tweet_text[:277] + '...'

    # Download and upload the first image
    media_ids = []
    media_list = artwork.get('media', [])
    if media_list:
        img_url = media_list[0].get('src')
        if img_url:
            print(f"Downloading artwork image: {img_url}")
            local_file = download_media(img_url)
            if local_file:
                try:
                    print("Uploading artwork image...")
                    media = api.media_upload(local_file)
                    media_ids.append(media.media_id)
                    os.remove(local_file)
                except Exception as e:
                    print(f"Upload failed: {e}")
                    if os.path.exists(local_file):
                        os.remove(local_file)

    print(f"Posting artwork tweet:\n{tweet_text}")
    if media_ids:
        resp = client.create_tweet(text=tweet_text, media_ids=media_ids)
    else:
        resp = client.create_tweet(text=tweet_text)

    tweet_id = resp.data['id']

    # Thread: second tweet with site link
    client.create_tweet(
        text="visit here to explore: de-centralize.com",
        in_reply_to_tweet_id=tweet_id
    )
    print(f"Artwork thread posted: {name}")


# --- REPLIES: ENGAGE WITH TRENDING / WATCHED TWEETS ---
def post_replies():
    """Find 2 high-engagement tweets and reply to them in our voice."""
    if not GROQ_API_KEY:
        print("GROQ_API_KEY not set, skipping replies.")
        return

    client, _ = get_twitter_clients()
    db = load_db()

    text_items = [
        item
        for category, items in db.items()
        if isinstance(items, list)
        for item in items
        if item.get("type") == "text" and len(item.get("content", "")) > 200
    ]
    if not text_items:
        print("No text items for context.")
        return

    # Build query: specific accounts or keyword search
    if WATCH_ACCOUNTS:
        from_part = " OR ".join(f"from:{a}" for a in WATCH_ACCOUNTS)
        query = f"({from_part}) -is:retweet"
    else:
        query = f"{SEARCH_KEYWORDS} -is:retweet -is:reply lang:en"

    try:
        resp = client.search_recent_tweets(
            query=query,
            max_results=20,
            tweet_fields=["public_metrics", "text", "id"],
            sort_order="relevancy",
        )
    except Exception as e:
        print(f"Reply search error: {e}")
        return

    if not resp.data:
        print("No candidate tweets found.")
        return

    # Sort by engagement score, try top candidates
    candidates = sorted(
        resp.data,
        key=lambda t: (
            t.public_metrics.get("like_count", 0)
            + t.public_metrics.get("retweet_count", 0) * 3
        ),
        reverse=True,
    )

    replied = 0
    for tweet in candidates:
        if replied >= 2:
            break

        selected = random.choice(text_items)
        content = selected.get("content", "")
        words = content.split()
        start = random.randint(0, max(0, len(words) - 80))
        chunk = " ".join(words[start: start + 80])

        try:
            reply_text = generate_reply(tweet.text, chunk)
        except Exception as e:
            print(f"Reply generation error: {e}")
            continue

        if len(reply_text) > 280:
            reply_text = reply_text[:277] + "..."

        print(f"Replying to {tweet.id}:\n  > {tweet.text[:100]}\n  Reply: {reply_text}")
        try:
            client.create_tweet(text=reply_text, in_reply_to_tweet_id=tweet.id)
            replied += 1
            if replied < 2:
                time.sleep(30)
        except tweepy.errors.Forbidden as e:
            api_codes = getattr(e, "api_codes", [])
            print(f"Reply forbidden: codes={api_codes}")
        except Exception as e:
            print(f"Reply post error: {e}")

    print(f"Done. Posted {replied} replies.")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else None

    if mode == "morning":
        post_morning_tweet()
    elif mode == "evening":
        post_evening_tweet()
    elif mode == "artwork":
        post_artwork_tweet()
    elif mode == "reply":
        post_replies()
    else:
        # Time-based fallback (for backward-compatible manual/test runs)
        current_utc_hour = datetime.now(timezone.utc).hour
        if current_utc_hour in [5, 6, 7]:
            post_morning_tweet()
        elif current_utc_hour in [15, 16, 17]:
            post_evening_tweet()
        elif current_utc_hour == 20:
            post_artwork_tweet()
        else:
            print(f"Test Mode (Hour: {current_utc_hour} UTC). Running Evening routine...")
            post_evening_tweet()
