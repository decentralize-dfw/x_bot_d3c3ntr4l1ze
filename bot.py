import os
import sys
import json
import random
import re
import tweepy
import requests
import xml.etree.ElementTree as ET
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

# Keyword search query used for viral context.
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


def format_tweet(text):
    """Strip surrounding quotes, uppercase, add 🥶 prefix and ꩜ suffix."""
    text = text.strip().strip('"\'')
    text = text.upper()
    return f"🥶 {text} ꩜"


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
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', desc) if 30 < len(s.strip()) <= 137]
        display_text = sentences[0] if sentences else (desc[:134] + "..." if len(desc) > 137 else desc)

    tweet_text = format_tweet(f"{type_label} {name}\n\n{display_text}")

    print(f"Attempting morning tweet:\n{tweet_text}")
    try:
        if media_ids:
            client.create_tweet(text=tweet_text, media_ids=media_ids)
        else:
            client.create_tweet(text=tweet_text)
        print(f"Morning broadcast complete: {name}")
    except tweepy.errors.Forbidden as e:
        api_codes = getattr(e, 'api_codes', [])
        print(f"Twitter 403 — codes: {api_codes}")
        if 187 in api_codes:
            print("Duplicate detected, retrying with a different item...")
            selected2 = random.choice([i for i in media_items if i != selected])
            name2 = selected2.get('name', 'ARCHIVE_ITEM')
            desc2 = selected2.get('description', '')
            type_label2 = TYPE_LABELS.get(selected2.get('type', 'image'), '[Archive]')
            caption2 = ""
            if GROQ_API_KEY:
                try:
                    caption2 = generate_media_caption(name2, desc2, type_label2)
                except Exception:
                    pass
            if not caption2:
                caption2 = desc2[:134] + "..." if len(desc2) > 137 else desc2
            retry_text = format_tweet(f"{type_label2} {name2}\n\n{caption2}")
            client.create_tweet(text=retry_text)
            print(f"Morning broadcast complete (retry): {name2}")
        else:
            raise


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


def generate_controversial_tweet(chunk, source_name, context_tweets):
    """LLM: deliberately contrarian viral tweet — ragebait grounded in philosophy."""
    groq_client = groq_sdk.Groq(api_key=GROQ_API_KEY)

    context_str = ""
    if context_tweets:
        context_str = (
            "Trending takes (inspiration only, do NOT copy):\n"
            + "\n---\n".join(context_tweets[:2])
            + "\n\n"
        )

    prompt = (
        "You are the voice of @decentralize___, a studio building 3D virtual worlds on-chain.\n"
        "Voice: punk architect, prophetic outsider, zero corporate speak.\n\n"
        "Write ONE controversial tweet. Choose ONE format:\n"
        "A. 'Everyone believes X. They're wrong.' + your counter-truth\n"
        "B. A prediction that makes the mainstream uncomfortable\n"
        "C. Expose the contradiction in how people think about virtual vs physical space\n"
        "D. Something that sounds provocative but is actually just ahead of its time\n\n"
        "Rules: NO hashtags. Max 240 chars. First line is the hook — make it sting a little. "
        "Ragebait-adjacent but philosophically grounded. Anger that makes you think, not rage for its own sake.\n\n"
        f"{context_str}"
        f"Source philosophy ({source_name}):\n{chunk}\n\n"
        "Output ONLY the tweet text."
    )
    resp = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=120,
        temperature=0.95,
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


def generate_artwork_tweet(name, description, categories):
    """LLM: short punchy tweet for an artwork drop. ≤ 130 chars."""
    groq_client = groq_sdk.Groq(api_key=GROQ_API_KEY)
    meta = ", ".join(
        f"{k}: {v}" for k, v in categories.items()
        if v and k in ('year', 'type', 'medium', 'collection')
    )
    prompt = (
        "You write for @decentralize___, a studio building 3D virtual worlds on-chain.\n"
        "Write ONE tweet announcing this artwork drop. Max 130 characters.\n"
        "Rules: complete sentence, no hashtags, no studio name, no quotes, no 'this is...'.\n"
        "State what it IS or what makes it special. Punchy and visual.\n\n"
        f"Artwork: {name}\n"
        f"Description: {description}\n"
        f"Details: {meta}\n\n"
        "Output ONLY the tweet text."
    )
    resp = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=55,
        temperature=0.75,
    )
    return resp.choices[0].message.content.strip()


def generate_news_tweet(title, article_text, source):
    """LLM: viral controversial take on a full news article. ≤ 130 chars."""
    groq_client = groq_sdk.Groq(api_key=GROQ_API_KEY)
    prompt = (
        "You are @decentralize___, building 3D virtual worlds on-chain. Voice: punk architect, prophetic outsider.\n"
        "Read the article below and write ONE viral tweet with a sharp, controversial take on it.\n"
        "Formats: bold prediction, uncomfortable truth, expose a contradiction, or 'everyone is missing the point'.\n"
        "Rules: Max 130 chars. No hashtags. No @mentions. Hook in the first line — make it sting.\n\n"
        f"Source: {source}\n"
        f"Title: {title}\n"
        f"Article:\n{article_text[:2000]}\n\n"
        "Output ONLY the tweet text."
    )
    resp = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=60,
        temperature=0.95,
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

    tweet_text = format_tweet(tweet_text)

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
            tweet_text = format_tweet(distill_to_tweet(new_chunk, name))
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

    # Generate tweet text via LLM (≤130 chars, no truncation)
    tweet_text = None
    if GROQ_API_KEY:
        try:
            tweet_text = generate_artwork_tweet(name, description, cats)
        except Exception as e:
            print(f"Artwork LLM error: {e}")

    if not tweet_text:
        # Fallback: word-boundary trim, no ellipsis
        base = f"{name} — {description}".strip()
        words = base.split()
        tweet_text = ""
        for w in words:
            candidate = (tweet_text + " " + w).strip()
            if len(candidate) > 130:
                break
            tweet_text = candidate

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

    tweet_text = format_tweet(tweet_text)
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




# --- EVENING CONTROVERSIAL: MANIFESTO TWEET WITH CONTRARIAN ANGLE ---
def post_controversial_evening_tweet():
    """Manifesto tweet — deliberately contrarian take to spark engagement."""
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
            tweet_text = generate_controversial_tweet(chunk, name, context_tweets)
        except Exception as e:
            print(f"Controversial generation error: {e}, falling back to distill...")
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

    tweet_text = format_tweet(tweet_text)

    print(f"Attempting controversial evening tweet:\n{tweet_text}")
    try:
        client.create_tweet(text=tweet_text)
        print(f"Controversial evening tweet complete:\n{tweet_text}")
    except tweepy.errors.Forbidden as e:
        api_codes = getattr(e, 'api_codes', [])
        if 187 in api_codes and GROQ_API_KEY:
            print("Duplicate detected, retrying with different chunk...")
            words = content.split()
            start = random.randint(0, max(0, len(words) - 150))
            new_chunk = ' '.join(words[start:start + 150])
            tweet_text = format_tweet(distill_to_tweet(new_chunk, name))
            client.create_tweet(text=tweet_text)
            print(f"Controversial evening tweet complete (retry):\n{tweet_text}")
        else:
            raise




# --- NEWS TWEETS: DECRYPT.CO & VENTUREBEAT ---

# RSS feed URLs — more reliable than homepage scraping
_RSS_FEEDS = {
    "decrypt.co": "https://decrypt.co/feed/",
    "venturebeat.com": "https://venturebeat.com/feed/",
}

def _scrape_article_body(article_url, source_name):
    """Scrape the body text from an article URL. Returns plain text or ''."""
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36'
        )
    }
    try:
        resp = requests.get(article_url, headers=headers, timeout=15)
        if resp.status_code != 200:
            print(f"{source_name} article fetch HTTP {resp.status_code}: {article_url}")
            return ""
        soup = BeautifulSoup(resp.content, 'html.parser')

        # Remove boilerplate tags
        for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside']):
            tag.decompose()

        # Try <article> tag first, then main content div heuristics
        body_tag = (
            soup.find('article')
            or soup.find('div', class_=re.compile(r'(article|post|entry|content|story)[-_]?(body|text|content)?', re.I))
        )
        if body_tag:
            paragraphs = [
                p.get_text(separator=' ', strip=True)
                for p in body_tag.find_all('p')
                if len(p.get_text(strip=True)) > 50
            ]
        else:
            paragraphs = [
                p.get_text(separator=' ', strip=True)
                for p in soup.find_all('p')
                if len(p.get_text(strip=True)) > 80
            ]

        return ' '.join(paragraphs)[:2500]
    except Exception as e:
        print(f"{source_name} article scrape error: {e}")
        return ""


def _parse_rss(rss_url, source_name):
    """Fetch and parse an RSS feed using stdlib xml + requests.
    Returns (title, article_url, body_text) or None.
    """
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36'
        )
    }
    try:
        resp = requests.get(rss_url, headers=headers, timeout=15)
        if resp.status_code != 200:
            print(f"{source_name} RSS fetch HTTP {resp.status_code}")
            return None

        # RSS is XML — parse with stdlib (no external deps)
        root = ET.fromstring(resp.content)
        channel = root.find('channel')
        if channel is None:
            print(f"{source_name}: RSS has no <channel> element.")
            return None

        item = channel.find('item')
        if item is None:
            print(f"{source_name}: RSS <channel> has no <item> elements.")
            return None

        title = (item.findtext('title') or '').strip()
        article_url = (item.findtext('link') or '').strip()

        # content:encoded namespace (most full-text RSS feeds use this)
        CONTENT_NS = 'http://purl.org/rss/1.0/modules/content/'
        raw_body = (
            item.findtext(f'{{{CONTENT_NS}}}encoded')
            or item.findtext('description')
            or ''
        )

        body_text = ''
        if raw_body:
            body_text = BeautifulSoup(raw_body, 'html.parser').get_text(separator=' ', strip=True)

        return title, article_url, body_text

    except ET.ParseError as e:
        print(f"{source_name} RSS XML parse error: {e}")
        return None
    except Exception as e:
        print(f"{source_name} RSS error: {e}")
        return None


def _fetch_article_content(site_url, source_name):
    """Fetch the top article via RSS feed and return (title, full_text).

    Strategy:
    1. Parse the RSS feed with stdlib xml (no extra dependencies).
    2. Use content:encoded or description from the feed as the body.
    3. If the feed body is too short (<300 chars), scrape the article URL.
    4. Falls back to homepage HTML scraping if the RSS feed is unavailable.
    """
    rss_url = _RSS_FEEDS.get(source_name)

    # --- Primary: RSS feed ---
    if rss_url:
        print(f"{source_name}: parsing RSS feed {rss_url}")
        rss_result = _parse_rss(rss_url, source_name)
        if rss_result:
            title, article_url, body_text = rss_result

            # If RSS body is short, scrape the full article page
            if len(body_text) < 300 and article_url:
                print(f"{source_name}: RSS body short ({len(body_text)} chars), scraping article...")
                scraped = _scrape_article_body(article_url, source_name)
                if scraped:
                    body_text = scraped

            if body_text:
                print(f"{source_name} RSS article: '{title}' ({len(body_text)} chars)")
                return title, body_text[:2500]
            else:
                print(f"{source_name}: no article body found in RSS entry.")

    # --- Fallback: homepage HTML scraping ---
    print(f"{source_name}: falling back to homepage HTML scraping...")
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36'
        )
    }
    try:
        resp = requests.get(site_url, headers=headers, timeout=15)
        if resp.status_code != 200:
            print(f"{source_name} homepage fetch failed: {resp.status_code}")
            return None
        soup = BeautifulSoup(resp.content, 'html.parser')

        # Find first article link from heading tags
        article_url = None
        base = site_url.rstrip('/')
        for tag in soup.find_all(['h1', 'h2', 'h3', 'article']):
            a = tag.find('a', href=True)
            if a:
                href = a['href']
                if href.startswith('http'):
                    article_url = href
                elif href.startswith('/'):
                    article_url = base + href
                if article_url:
                    break

        if not article_url:
            print(f"{source_name}: no article link found on homepage.")
            return None

        print(f"{source_name} fallback article: {article_url}")
        full_text = _scrape_article_body(article_url, source_name)

        # Title from og:title or h1
        title = ""
        try:
            art_resp = requests.get(article_url, headers=headers, timeout=15)
            if art_resp.status_code == 200:
                art_soup = BeautifulSoup(art_resp.content, 'html.parser')
                og = art_soup.find('meta', property='og:title')
                if og and og.get('content'):
                    title = og['content'].strip()
                elif art_soup.find('h1'):
                    title = art_soup.find('h1').get_text(strip=True)
        except Exception:
            pass

        if not full_text:
            print(f"{source_name}: no article text found.")
            return None

        return title, full_text

    except Exception as e:
        print(f"{source_name} fallback fetch error: {e}")
        return None


def _post_news_tweet(site_url, source_name):
    if not GROQ_API_KEY:
        print(f"GROQ_API_KEY not set, skipping {source_name} tweet.")
        return
    client, _ = get_twitter_clients()
    result = _fetch_article_content(site_url, source_name)
    if not result:
        print(f"No article content from {source_name}, skipping.")
        return
    title, article_text = result
    print(f"{source_name} title: {title}")
    try:
        tweet_text = generate_news_tweet(title, article_text, source_name)
    except Exception as e:
        print(f"News tweet generation error: {e}")
        return
    # Safety: word-boundary trim to 137 chars, no ellipsis
    if len(tweet_text) > 137:
        words = tweet_text.split()
        tweet_text = ""
        for w in words:
            candidate = (tweet_text + " " + w).strip()
            if len(candidate) > 137:
                break
            tweet_text = candidate
    tweet_text = format_tweet(tweet_text)
    print(f"Posting {source_name} tweet ({len(tweet_text)} chars): {tweet_text}")
    try:
        client.create_tweet(text=tweet_text)
        print(f"{source_name} tweet posted.")
    except Exception as e:
        print(f"{source_name} tweet post error: {e}")


def post_decrypt_tweet():
    _post_news_tweet("https://decrypt.co", "decrypt.co")


def post_venturebeat_tweet():
    _post_news_tweet("https://venturebeat.com", "venturebeat.com")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else None

    if mode == "morning":
        post_morning_tweet()
    elif mode == "evening":
        post_evening_tweet()
    elif mode == "evening_controversial":
        post_controversial_evening_tweet()
    elif mode == "artwork":
        post_artwork_tweet()
    elif mode == "decrypt":
        post_decrypt_tweet()
    elif mode == "venturebeat":
        post_venturebeat_tweet()
    else:
        # Time-based fallback (for backward-compatible manual/test runs)
        current_utc_hour = datetime.now(timezone.utc).hour
        if current_utc_hour == 4:
            post_artwork_tweet()
        elif current_utc_hour == 13:
            post_controversial_evening_tweet()
        elif current_utc_hour == 22:
            post_morning_tweet()
        else:
            print(f"Test Mode (Hour: {current_utc_hour} UTC). Running Evening routine...")
            post_evening_tweet()
