import os
import sys
import json
import random
import re
import hashlib
import tweepy
import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from datetime import datetime, timezone
import time
import groq as groq_sdk
import tweet_archive

# --- 1. API KEYS ---
TWITTER_API_KEY = os.environ.get("TWITTER_API_KEY")
TWITTER_API_SECRET = os.environ.get("TWITTER_API_SECRET")
TWITTER_ACCESS_TOKEN = os.environ.get("TWITTER_ACCESS_TOKEN")
TWITTER_ACCESS_TOKEN_SECRET = os.environ.get("TWITTER_ACCESS_TOKEN_SECRET")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

# Keyword search query used for viral context.
SEARCH_KEYWORDS = (
    '(metaverse OR "virtual world" OR "AI agent" OR "on-chain" OR '
    '"spatial computing" OR "web3" OR "3D NFT" OR "WebXR")'
)

# ── Multi-model rotation ───────────────────────────────────────────────────────
# Her gün farklı model kullanılır — ses çeşitliliği + klişeye düşmeme
_LLM_MODELS = [
    "llama-3.3-70b-versatile",   # Ana model — en güçlü
    "llama-3.3-70b-versatile",   # Ağırlık 2x — en güvenilir
    "mixtral-8x7b-32768",        # Alternatif — farklı ses
    "llama-3.3-70b-versatile",   # Ana model
    "gemma2-9b-it",              # Hafif, daha direkt üslup
    "llama-3.3-70b-versatile",   # Ana model
    "mixtral-8x7b-32768",        # Alternatif
]

def _get_model() -> str:
    """Haftanın gününe göre model seç — tutarlı ama çeşitli."""
    day = datetime.now(timezone.utc).weekday()  # 0=Pazartesi … 6=Pazar
    return _LLM_MODELS[day % len(_LLM_MODELS)]

def _call_llm(prompt: str, max_tokens: int = 120, temperature: float = 0.9) -> str:
    """Groq çağrısı — model rotasyonu + fallback zinciri."""
    primary = _get_model()
    fallback_chain = ["llama-3.3-70b-versatile", "mixtral-8x7b-32768", "gemma2-9b-it"]
    models_to_try = [primary] + [m for m in fallback_chain if m != primary]

    client = groq_sdk.Groq(api_key=GROQ_API_KEY)
    last_err = None
    for model in models_to_try:
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            print(f"LLM [{model}] failed: {e} — trying next...")
            last_err = e
    raise RuntimeError(f"All LLM providers failed. Last error: {last_err}")

# ── Beliefs (fikir haritası) ───────────────────────────────────────────────────
def _load_beliefs() -> dict:
    beliefs_path = os.path.join(os.path.dirname(__file__), "beliefs.json")
    try:
        with open(beliefs_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

_used_beliefs_this_session: set = set()

def _random_belief() -> str:
    """Belief seç. Aynı çalışma (session) içinde aynı belief tekrarlanmaz."""
    global _used_beliefs_this_session
    b = _load_beliefs()
    pool = b.get("core_beliefs", []) + b.get("contested_claims", [])
    if not pool:
        return ""
    unused = [p for p in pool if p not in _used_beliefs_this_session]
    if not unused:
        _used_beliefs_this_session.clear()
        unused = pool
    choice = random.choice(unused)
    _used_beliefs_this_session.add(choice)
    return choice

# ── Bot hafızası (rolling context window) ─────────────────────────────────────
def get_voice_context(n: int = 5) -> str:
    """Son n tweet'i al — ses tutarlılığı ve tekrar önleme için LLM'e ver."""
    recent = tweet_archive.get_recent_tweet_texts(days=30)
    if not recent:
        return ""
    sample = recent[-n:] if len(recent) >= n else recent
    lines = "\n".join(f"- {t}" for t in sample)
    return (
        f"Your {len(sample)} most recent tweets (DO NOT repeat these ideas, maintain this voice):\n"
        f"{lines}\n\n"
    )

# ── Haftalık tema sistemi ──────────────────────────────────────────────────────
WEEKLY_THEMES = [
    "the permanence problem in virtual spaces — why most digital experiences feel disposable",
    "why WebXR hasn't replaced native apps yet — and the exact moment it will",
    "on-chain ownership as a design constraint — what it forces you to get right",
    "the UX debt of the metaverse — what spectacle-first design cost us",
    "spatial computing vs virtual reality — the terminology war that delayed both",
    "browser as spatial platform — the most underestimated surface in 3D design",
    "virtual identity and permanence — what it means to exist in a space you don't own",
    "AI-generated 3D and the coming commoditization of visual novelty",
    "decentralized worlds and the architecture of trustless space",
    "ambient presence vs active engagement — designing for the former",
    "the studio that builds in public — visibility as competitive advantage",
    "virtual space has gravity — ignoring it is why most digital experiences fail",
]

def get_this_weeks_theme() -> str:
    week_number = datetime.now(timezone.utc).isocalendar()[1]
    return WEEKLY_THEMES[week_number % len(WEEKLY_THEMES)]

# ── Self-critique kalite kapısı ────────────────────────────────────────────────
def score_tweet_quality(tweet_text: str) -> float:
    """Tweet'i LLM'e puanlat (1-10 × 3 eksen). Ortalama 6 altıysa reddet."""
    if not GROQ_API_KEY:
        return 7.0  # API yoksa geç
    prompt = (
        "Rate this tweet on exactly 3 axes, each 1-10:\n"
        "1. ORIGINALITY: Does it say something the reader hasn't thought before?\n"
        "2. SPECIFICITY: Does it make a precise, non-generic claim?\n"
        "3. PROVOCATION: Does it leave an unanswerable question in the reader's mind?\n\n"
        f'Tweet: "{tweet_text}"\n\n'
        'Respond ONLY with JSON: {"originality": N, "specificity": N, "provocation": N}'
    )
    try:
        raw = _call_llm(prompt, max_tokens=40, temperature=0.2)
        # Önce direkt parse dene (LLM bazen düz JSON döner)
        try:
            scores = json.loads(raw.strip())
        except (json.JSONDecodeError, ValueError):
            # Açıklama metni içinde gömülü JSON'ı bul — re.DOTALL ile nested destekli
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if not match:
                return 7.0
            scores = json.loads(match.group())
        avg = sum([
            scores.get("originality", 7),
            scores.get("specificity", 7),
            scores.get("provocation", 7),
        ]) / 3
        print(f"Quality score: {avg:.1f}/10 — O:{scores.get('originality')} S:{scores.get('specificity')} P:{scores.get('provocation')}")
        return avg
    except Exception as e:
        print(f"Quality scoring failed: {e}")
        return 7.0  # Hata durumunda geç


def is_semantically_duplicate(candidate: str) -> bool:
    """
    Jaccard'ı tamamlayan LLM tabanlı semantic benzerlik kontrolü.
    'virtual spaces need inhabitants' ile 'digital environments require residents'
    gibi kelimesi farklı ama fikri aynı tweet'leri yakalar.
    """
    recent = tweet_archive.get_recent_tweet_texts(days=60)
    if not recent:
        return False
    sample = recent[-20:]
    sample_block = "\n".join(f"- {t}" for t in sample)
    prompt = (
        "Does this new tweet express the same core idea as ANY tweet in the list below?\n"
        "Focus on the underlying argument, not surface wording. "
        "Answer 'yes' only if the core claim would feel repetitive to a reader.\n\n"
        f'New tweet: "{candidate}"\n\n'
        f"Recent tweets:\n{sample_block}\n\n"
        "Answer ONLY with 'yes' or 'no'."
    )
    try:
        result = _call_llm(prompt, max_tokens=5, temperature=0.1)
        return result.strip().lower().startswith("yes")
    except Exception:
        return False  # hata durumunda izin ver


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

# Follow-up questions posted as thread reply to evening/controversial tweets
FOLLOW_UP_QUESTIONS = [
    "is your studio already building here?",
    "how long before the physical feels limited?",
    "what changes when your workspace has no walls?",
    "when does virtual become the default?",
    "are you designing for the world that exists or the one coming?",
    "who decides what's real when everything can be copied?",
    "how do you build permanence in a virtual space?",
    "is your next project physical or digital first?",
    "what does community look like without a physical address?",
    "how long before this is unavoidable?",
]

# Shared voice/quality rules injected into every LLM prompt
TONE_BLOCK = (
    "Voice rules (non-negotiable):\n"
    "- Write in natural sentence case. Never all-caps.\n"
    "- IQ 150+ thinking: carry a specific insight the reader hasn't encountered before.\n"
    "- Leave the reader with a question they can't immediately answer — make them stop and think.\n"
    "- Position as a thought leader in virtual design, metaverse, WebXR, spatial computing, on-chain worlds.\n"
    "- No buzzword salads. No clichés. No 'the future is here.' Say something true and surprising.\n"
    "- You may include 1-2 relevant hashtags (#virtualdesign #WebXR #metaverse #spatialcomputing #onchain) "
    "at the end if they add context. Optional.\n"
)


def trim_for_format(text, limit=135):
    """Word-boundary trim before format_tweet() — keeps total tweet ≤ 140 chars."""
    if len(text) <= limit:
        return text
    trimmed = text[:limit]
    last_space = trimmed.rfind(' ')
    # Son boşluk metnin %75'inden sonraysa oradan kes (kelimeyi kırma).
    # Daha önce ise hard-cut daha az içerik kaybı verir.
    return trimmed[:last_space] if last_space > (limit * 3 // 4) else trimmed


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
    """Strip surrounding quotes and add ꩜ brand suffix."""
    text = text.strip().strip('"\'')
    return f"{text} ꩜"


def load_db():
    try:
        with open('database.json', 'r', encoding='utf-8') as file:
            return json.load(file)
    except FileNotFoundError:
        print("database.json not found, returning empty list.")
        return []
    except json.JSONDecodeError as e:
        print(f"database.json parse error: {e}")
        return []

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

    tweet_archive.cleanup_old_entries()

    media_items = []
    for category, items in db.items():
        if isinstance(items, list):
            for item in items:
                if item.get('type') in MEDIA_TYPES:
                    media_items.append(item)

    fresh_items = [i for i in media_items if not tweet_archive.is_posted_recently(i['id'])]
    if not fresh_items:
        print("Archive: all media items posted recently, picking random anyway.")
        fresh_items = media_items
    selected = random.choice(fresh_items)
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

    inner = trim_for_format(f"{type_label} {name}\n\n{display_text}")
    tweet_text = format_tweet(inner)

    print(f"Attempting morning tweet ({len(tweet_text)} chars):\n{tweet_text}")
    try:
        if media_ids:
            resp = client.create_tweet(text=tweet_text, media_ids=media_ids)
        else:
            resp = client.create_tweet(text=tweet_text)
        tweet_archive.record_post(selected['id'], content_type="morning_media",
                                  tweet_text=tweet_text, tweet_id=resp.data['id'],
                                  weekly_theme=get_this_weeks_theme(),
                                  media_url=media_url_to_download if media_ids else None)
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
            retry_text = format_tweet(trim_for_format(f"{type_label2} {name2}\n\n{caption2}"))
            resp2 = client.create_tweet(text=retry_text)
            tweet_archive.record_post(selected2['id'], content_type="morning_media",
                                      tweet_text=retry_text, tweet_id=resp2.data['id'],
                                      weekly_theme=get_this_weeks_theme())
            print(f"Morning broadcast complete (retry): {name2}")
        elif "2 minutes" in str(e) or "longer than 2" in str(e).lower():
            # Video >2 dakika — medya yüklenemez, link tweet olarak paylaş
            print(f"Video >2min upload rejected — falling back to link tweet: {name}")
            prefix = format_tweet(trim_for_format(f"[Video >2min] {name}\n\n{display_text}"))
            # Twitter URL'leri ~23 karakter olarak sayar; prefix 255'te bırak
            if len(prefix) > 255:
                prefix = prefix[:252] + "..."
            link_text = f"{prefix}\n\n{url}"
            resp_link = client.create_tweet(text=link_text)
            tweet_archive.record_post(selected['id'], content_type="morning_media_long_video",
                                      tweet_text=link_text, tweet_id=resp_link.data['id'],
                                      weekly_theme=get_this_weeks_theme(),
                                      media_url=media_url_to_download)
            print(f"Morning broadcast complete (link fallback >2min): {name}")
        else:
            raise


# --- LLM HELPERS ---

def distill_to_tweet(chunk, source_name):
    groq_client = groq_sdk.Groq(api_key=GROQ_API_KEY)
    voice_ctx = get_voice_context(n=3)
    prompt = (
        "You write for a digital design studio (Decentralize Design) that builds virtual worlds and manifestos.\n"
        "From the text below, extract or rephrase ONE powerful, self-contained thought as a tweet (max 130 chars).\n"
        "Rules: no quotes, no 'we believe / this manifesto / our studio', reads as a standalone statement.\n"
        f"{TONE_BLOCK}"
        f"{voice_ctx}"
        f"Output only the tweet text.\n\nSource: {source_name}\nText:\n{chunk}"
    )
    resp = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=60,
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
    """LLM: chain-of-thought viral tweet — manifesto + context + bot memory + weekly theme."""
    context_str = ""
    if context_tweets:
        context_str = (
            "Trending conversation in this space (inspiration only, do NOT copy or quote):\n"
            + "\n---\n".join(context_tweets[:2])
            + "\n\n"
        )

    belief = _random_belief()
    belief_line = f'One of our core beliefs: "{belief}"\n\n' if belief else ""
    voice_ctx   = get_voice_context(n=4)
    weekly      = get_this_weeks_theme()

    prompt = (
        "You are the voice of @decentralize___, a studio building 3D virtual worlds on-chain.\n\n"
        f"{TONE_BLOCK}\n"
        f"{voice_ctx}"
        f"{belief_line}"
        f"This week's exploration theme: {weekly}\n\n"
        "THINK STEP BY STEP before writing:\n"
        "Step 1 — What is the most counterintuitive thing the source material reveals?\n"
        "Step 2 — What does the mainstream assume that is wrong?\n"
        "Step 3 — Write ONE tweet that surfaces Step 2 through the lens of Step 1.\n\n"
        "Tweet format (pick the sharpest fit):\n"
        "A. Precise observation + unexpected implication\n"
        "B. Physical vs virtual contrast with a non-obvious takeaway\n"
        "C. Prediction grounded in a specific mechanism, not vibes\n"
        "D. Reframe that makes a familiar idea suddenly strange\n\n"
        "Max 130 chars. First line earns attention through insight, not shock.\n\n"
        f"{context_str}"
        f"Source philosophy ({source_name}):\n{chunk}\n\n"
        "Output ONLY the final tweet text — not the steps."
    )
    return _call_llm(prompt, max_tokens=120, temperature=0.9)


def generate_controversial_tweet(chunk, source_name, context_tweets):
    """LLM: chain-of-thought contrarian tweet — intellectually provocative, not rage-bait."""
    context_str = ""
    if context_tweets:
        context_str = (
            "Trending takes (inspiration only, do NOT copy):\n"
            + "\n---\n".join(context_tweets[:2])
            + "\n\n"
        )

    belief    = _random_belief()
    belief_line = f'One of our contested beliefs: "{belief}"\n\n' if belief else ""
    voice_ctx = get_voice_context(n=4)
    weekly    = get_this_weeks_theme()

    prompt = (
        "You are the voice of @decentralize___, a studio building 3D virtual worlds on-chain.\n\n"
        f"{TONE_BLOCK}\n"
        f"{voice_ctx}"
        f"{belief_line}"
        f"This week's exploration theme: {weekly}\n\n"
        "THINK STEP BY STEP before writing:\n"
        "Step 1 — What widely held assumption about virtual/physical space is demonstrably wrong?\n"
        "Step 2 — What is the actual truth, and why does it make people uncomfortable?\n"
        "Step 3 — Write ONE tweet that delivers Step 2 with precision, not rage.\n\n"
        "Format options:\n"
        "A. Wrong assumption + actual truth\n"
        "B. Prediction that exposes what mainstream is missing\n"
        "C. Uncomfortable contradiction in how people think about virtual vs physical space\n"
        "D. Reframe that makes a familiar idea suddenly strange\n\n"
        "Max 130 chars. Intellectually provocative, not rage-bait.\n\n"
        f"{context_str}"
        f"Source philosophy ({source_name}):\n{chunk}\n\n"
        "Output ONLY the final tweet text — not the steps."
    )
    return _call_llm(prompt, max_tokens=120, temperature=0.92)


def generate_media_caption(name, description, type_label):
    """LLM: one complete, self-contained caption for a media item. ≤120 chars."""
    prompt = (
        "You write captions for @decentralize___ (a studio building 3D virtual worlds on-chain).\n"
        "Write ONE complete sentence caption for this media item. Max 120 characters.\n"
        "Rules: complete sentence, makes sense to someone who has never heard of this project, "
        "no studio name, no 'this is...'. State what it IS or what it MEANS with precision.\n"
        f"{TONE_BLOCK}"
        f"Item: {type_label} — {name}\n"
        f"Description: {description}\n\n"
        "Output ONLY the caption."
    )
    return _call_llm(prompt, max_tokens=50, temperature=0.7)


def generate_artwork_tweet(name, description, categories):
    """LLM: short punchy tweet for an artwork drop. ≤ 130 chars."""
    meta = ", ".join(
        f"{k}: {v}" for k, v in categories.items()
        if v and k in ('year', 'type', 'medium', 'collection')
    )
    prompt = (
        "You write for @decentralize___, a studio building 3D virtual worlds on-chain.\n"
        "Write ONE tweet announcing this artwork drop. Max 130 characters.\n"
        "Rules: complete sentence, no studio name, no quotes, no 'this is...'.\n"
        "State what it IS or what makes it conceptually significant. Visual and precise.\n"
        f"{TONE_BLOCK}"
        f"Artwork: {name}\n"
        f"Description: {description}\n"
        f"Details: {meta}\n\n"
        "Output ONLY the tweet text."
    )
    return _call_llm(prompt, max_tokens=55, temperature=0.75)


def generate_news_tweet(title, article_text, source, prior_opinions=None):
    """LLM: 3-5 word sharp fragment as news commentary for thread reply.

    prior_opinions: list of past tweet texts on same topic — enables opinion evolution.
    """
    prior_block = ""
    if prior_opinions:
        prior_lines = "\n".join(f"- {o}" for o in prior_opinions[:3])
        prior_block = (
            f"You previously commented on similar topics:\n{prior_lines}\n"
            "Build on this perspective — evolve the idea, don't repeat it.\n\n"
        )
    prompt = (
        "You are @decentralize___, building 3D virtual worlds on-chain.\n"
        f"{TONE_BLOCK}"
        f"{prior_block}"
        "Read the article below and write a SINGLE sharp commentary fragment.\n"
        "Rules: EXACTLY 3 to 5 words. No sentence structure needed. No @mentions. "
        "A precise gut-punch fragment that reveals something the headline missed. Max 30 chars.\n\n"
        f"Source: {source}\n"
        f"Title: {title}\n"
        f"Article:\n{article_text[:1500]}\n\n"
        "Output ONLY the 3-5 word fragment."
    )
    return _call_llm(prompt, max_tokens=20, temperature=0.92).strip('"\'')


def _get_prior_opinions_for_topic(title: str, n: int = 3) -> list:
    """Arşivden bu konuyla ilgili eski yorumları çek — opinion evolution için."""
    topic_words = set(w.lower() for w in title.split() if len(w) > 4)
    recent = tweet_archive.get_recent_tweet_texts(days=30)
    matches = []
    for t in reversed(recent):
        tweet_words = set(w.lower() for w in t.split())
        if len(topic_words & tweet_words) >= 2:
            matches.append(t)
        if len(matches) >= n:
            break
    return matches


def generate_news_headline(title, article_text, source):
    """LLM: 1-sentence factual headline summary. Max 115 chars."""
    prompt = (
        "Summarize this news article in ONE factual sentence. Max 115 characters.\n"
        "Rules: state what happened, no opinion, no hashtags, no 'this article...'. Just the fact.\n\n"
        f"Source: {source}\nTitle: {title}\nArticle:\n{article_text[:1500]}\n\n"
        "Output ONLY the summary sentence."
    )
    return _call_llm(prompt, max_tokens=50, temperature=0.3).strip('"\'')


def _get_hashtags_for_source(source_name):
    if "decrypt" in source_name:
        return "#web3 #crypto"
    return "#metaverse #web3"


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

    fresh_texts = [i for i in text_items if not tweet_archive.is_posted_recently(i['id'])]
    if not fresh_texts:
        print("Archive: all text items posted recently, picking random anyway.")
        fresh_texts = text_items
    selected = random.choice(fresh_texts)
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
        context_tweets = []
        try:
            context_tweets = fetch_context_tweets(SEARCH_KEYWORDS)
        except Exception as e:
            print(f"Context fetch error: {e}")
        for attempt in range(3):
            try:
                candidate = generate_viral_tweet(chunk, name, context_tweets)
                if tweet_archive.is_too_similar(candidate):
                    print(f"Evening attempt {attempt+1}: too similar (Jaccard), retrying...")
                    if len(words) > 150:
                        start = random.randint(0, len(words) - 150)
                        chunk = ' '.join(words[start:start + 150])
                    continue
                if is_semantically_duplicate(candidate):
                    print(f"Evening attempt {attempt+1}: too similar (semantic), retrying...")
                    if len(words) > 150:
                        start = random.randint(0, len(words) - 150)
                        chunk = ' '.join(words[start:start + 150])
                    continue
                if tweet_archive.is_theme_in_cooldown(candidate):
                    print(f"Evening attempt {attempt+1}: theme in cooldown, retrying...")
                    if len(words) > 150:
                        start = random.randint(0, len(words) - 150)
                        chunk = ' '.join(words[start:start + 150])
                    continue
                quality = score_tweet_quality(candidate)
                if quality < 6.0:
                    print(f"Evening attempt {attempt+1}: quality {quality:.1f}/10 < 6.0, retrying...")
                    if len(words) > 150:
                        start = random.randint(0, len(words) - 150)
                        chunk = ' '.join(words[start:start + 150])
                    continue
                tweet_text = candidate
                break
            except Exception as e:
                print(f"Viral generation error (attempt {attempt+1}): {e}")
                try:
                    tweet_text = distill_to_tweet(chunk, name)
                    break
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

    tweet_text = format_tweet(trim_for_format(tweet_text))

    print(f"Attempting evening tweet ({len(tweet_text)} chars):\n{tweet_text}")
    try:
        resp = client.create_tweet(text=tweet_text)
        tweet_id = resp.data['id']
        tweet_archive.record_post(selected['id'], content_type="evening_text",
                                  tweet_text=tweet_text, tweet_id=tweet_id,
                                  weekly_theme=get_this_weeks_theme())
        question = generate_thread_reply(tweet_text)
        if question:
            client.create_tweet(text=question, in_reply_to_tweet_id=tweet_id)
        print(f"Evening broadcast complete with thread:\n{tweet_text}\n→ {question}")
    except tweepy.errors.Forbidden as e:
        api_codes = getattr(e, 'api_codes', [])
        api_messages = getattr(e, 'api_messages', [])
        print(f"Twitter 403 — codes: {api_codes}, messages: {api_messages}")
        if 187 in api_codes and GROQ_API_KEY:
            print("Duplicate detected, retrying with a different chunk...")
            words = content.split()
            start = random.randint(0, max(0, len(words) - 150))
            new_chunk = ' '.join(words[start:start + 150])
            tweet_text = format_tweet(trim_for_format(distill_to_tweet(new_chunk, name)))
            print(f"Retry tweet:\n{tweet_text}")
            resp = client.create_tweet(text=tweet_text)
            tweet_id = resp.data['id']
            question = random.choice(FOLLOW_UP_QUESTIONS)
            client.create_tweet(text=question, in_reply_to_tweet_id=tweet_id)
            print(f"Evening broadcast complete (retry) with thread:\n{tweet_text}\n→ {question}")
        else:
            raise


# --- 23:00 (TR): ARTWORK DROP ---
def post_artwork_tweet():
    """Pick a random artwork, post image + metadata, then reply with site link."""
    client, api = get_twitter_clients()

    try:
        with open('artworks.json', 'r', encoding='utf-8') as f:
            artworks = json.load(f)
    except FileNotFoundError:
        print("artworks.json not found, skipping artwork tweet.")
        return
    except json.JSONDecodeError as e:
        print(f"artworks.json parse error: {e}")
        return

    if not artworks:
        print("No artworks found in artworks.json.")
        return

    fresh_artworks = [a for a in artworks if not tweet_archive.is_posted_recently(a['id'])]
    if not fresh_artworks:
        print("Archive: all artworks posted recently, picking random anyway.")
        fresh_artworks = artworks
    artwork = random.choice(fresh_artworks)
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
    img_url = None
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

    tweet_archive.record_post(artwork['id'], content_type="artwork",
                              tweet_text=tweet_text, tweet_id=tweet_id,
                              weekly_theme=get_this_weeks_theme(),
                              media_url=img_url)
    # Thread: second tweet with site link + hashtags
    client.create_tweet(
        text="explore the collection: de-centralize.com #digitalart #metaverse",
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

    fresh_texts = [i for i in text_items if not tweet_archive.is_posted_recently(i['id'])]
    if not fresh_texts:
        print("Archive: all text items posted recently, picking random anyway.")
        fresh_texts = text_items
    selected = random.choice(fresh_texts)
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
        context_tweets = []
        try:
            context_tweets = fetch_context_tweets(SEARCH_KEYWORDS)
        except Exception as e:
            print(f"Context fetch error: {e}")
        for attempt in range(3):
            try:
                candidate = generate_controversial_tweet(chunk, name, context_tweets)
                if tweet_archive.is_too_similar(candidate):
                    print(f"Controversial attempt {attempt+1}: too similar (Jaccard), retrying...")
                    if len(words) > 150:
                        start = random.randint(0, len(words) - 150)
                        chunk = ' '.join(words[start:start + 150])
                    continue
                if is_semantically_duplicate(candidate):
                    print(f"Controversial attempt {attempt+1}: too similar (semantic), retrying...")
                    if len(words) > 150:
                        start = random.randint(0, len(words) - 150)
                        chunk = ' '.join(words[start:start + 150])
                    continue
                if tweet_archive.is_theme_in_cooldown(candidate):
                    print(f"Controversial attempt {attempt+1}: theme in cooldown, retrying...")
                    if len(words) > 150:
                        start = random.randint(0, len(words) - 150)
                        chunk = ' '.join(words[start:start + 150])
                    continue
                quality = score_tweet_quality(candidate)
                if quality < 6.0:
                    print(f"Controversial attempt {attempt+1}: quality {quality:.1f}/10 < 6.0, retrying...")
                    if len(words) > 150:
                        start = random.randint(0, len(words) - 150)
                        chunk = ' '.join(words[start:start + 150])
                    continue
                tweet_text = candidate
                break
            except Exception as e:
                print(f"Controversial generation error (attempt {attempt+1}): {e}")
                try:
                    tweet_text = distill_to_tweet(chunk, name)
                    break
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

    tweet_text = format_tweet(trim_for_format(tweet_text))

    print(f"Attempting controversial evening tweet ({len(tweet_text)} chars):\n{tweet_text}")
    try:
        resp = client.create_tweet(text=tweet_text)
        tweet_id = resp.data['id']
        tweet_archive.record_post(selected['id'], content_type="evening_controversial",
                                  tweet_text=tweet_text, tweet_id=tweet_id,
                                  weekly_theme=get_this_weeks_theme())
        question = generate_thread_reply(tweet_text)
        if question:
            client.create_tweet(text=question, in_reply_to_tweet_id=tweet_id)
        print(f"Controversial evening tweet complete with thread:\n{tweet_text}\n→ {question}")
    except tweepy.errors.Forbidden as e:
        api_codes = getattr(e, 'api_codes', [])
        if 187 in api_codes and GROQ_API_KEY:
            print("Duplicate detected, retrying with different chunk...")
            words = content.split()
            start = random.randint(0, max(0, len(words) - 150))
            new_chunk = ' '.join(words[start:start + 150])
            tweet_text = format_tweet(trim_for_format(distill_to_tweet(new_chunk, name)))
            resp = client.create_tweet(text=tweet_text)
            tweet_id = resp.data['id']
            question = random.choice(FOLLOW_UP_QUESTIONS)
            client.create_tweet(text=question, in_reply_to_tweet_id=tweet_id)
            print(f"Controversial evening tweet complete (retry) with thread:\n{tweet_text}\n→ {question}")
        else:
            raise




# --- NEWS TWEETS: DECRYPT.CO & VENTUREBEAT ---

# RSS feed URLs — genişletilmiş niche ekosistem
_RSS_FEEDS = {
    "decrypt.co":       "https://decrypt.co/feed/",
    "venturebeat.com":  "https://venturebeat.com/feed/",
    "roadtovr.com":     "https://www.roadtovr.com/feed/",
    "awwwards.com":     "https://www.awwwards.com/blog/rss",
    "webxr.news":       "https://webxr.news/rss",
    "sketchfab.com":    "https://sketchfab.com/blogs/community/feed",
}

# Viral context + community pulse için ek niche RSS kaynakları
_CONTEXT_RSS_FEEDS = {
    "roadtovr.com":     "https://www.roadtovr.com/feed/",
    "awwwards.com":     "https://www.awwwards.com/blog/rss",
    "techcrunch.com":   "https://techcrunch.com/feed/",
    "theverge.com":     "https://www.theverge.com/rss/index.xml",
    "a16z.com":         "https://a16z.com/feed/",
    "ieee_spectrum_vr": "https://spectrum.ieee.org/feeds/topic/virtual-reality.rss",
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


def _parse_rss_all(rss_url, source_name, max_items=20):
    """Fetch ALL items from an RSS feed.
    Returns list of {"title": str, "url": str, "summary": str} dicts, or [].
    Used by community_pulse and data_viz which need multiple items.
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
            return []
        root = ET.fromstring(resp.content)
        channel = root.find('channel')
        if channel is None:
            return []
        CONTENT_NS = 'http://purl.org/rss/1.0/modules/content/'
        items = []
        for item in channel.findall('item')[:max_items]:
            title = (item.findtext('title') or '').strip()
            url = (item.findtext('link') or '').strip()
            raw_body = (
                item.findtext(f'{{{CONTENT_NS}}}encoded')
                or item.findtext('description')
                or ''
            )
            summary = ''
            if raw_body:
                summary = BeautifulSoup(raw_body, 'html.parser').get_text(separator=' ', strip=True)
            if title:
                items.append({"title": title, "url": url, "summary": summary})
        return items
    except ET.ParseError as e:
        print(f"{source_name} RSS XML parse error: {e}")
        return []
    except Exception as e:
        print(f"{source_name} RSS error: {e}")
        return []


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

    # Archive check: skip if this article was already posted in last 60 days
    article_id = "news_" + hashlib.md5(title.encode()).hexdigest()[:12]
    if tweet_archive.is_posted_recently(article_id):
        print(f"Archive: article '{title[:60]}' already posted in last 60 days, skipping.")
        return

    # Tweet 1: factual headline summary
    try:
        headline = generate_news_headline(title, article_text, source_name)
    except Exception as e:
        print(f"Headline generation error: {e}")
        headline = title[:115] if title else "breaking news in the space"

    tweet1 = f"NEWS: {headline}"
    if len(tweet1) > 140:
        tweet1 = tweet1[:137].rsplit(' ', 1)[0] + "..."

    print(f"Posting {source_name} tweet 1 ({len(tweet1)} chars): {tweet1}")
    try:
        resp = client.create_tweet(text=tweet1)
        tweet1_id = resp.data['id']
        tweet_archive.record_post(article_id, content_type="news",
                                  tweet_text=tweet1, tweet_id=tweet1_id,
                                  weekly_theme=get_this_weeks_theme())
    except Exception as e:
        print(f"{source_name} tweet 1 post error: {e}")
        return

    # Tweet 2: 3-5 word commentary as reply + hashtags
    # Opinion evolution: geçmişteki benzer konulardaki yorumları çek
    prior_opinions = _get_prior_opinions_for_topic(title)
    if prior_opinions:
        print(f"  Opinion evolution: {len(prior_opinions)} prior opinion(s) found.")
    try:
        commentary = generate_news_tweet(title, article_text, source_name, prior_opinions=prior_opinions)
    except Exception as e:
        print(f"Commentary generation error: {e}")
        commentary = "nobody saw this coming"

    hashtags = _get_hashtags_for_source(source_name)
    tweet2 = f"{commentary} {hashtags}".strip()
    if len(tweet2) > 140:
        tweet2 = commentary[:140]

    print(f"Posting {source_name} tweet 2 ({len(tweet2)} chars): {tweet2}")
    try:
        resp2 = client.create_tweet(text=tweet2, in_reply_to_tweet_id=tweet1_id)
        tweet_archive.record_post(article_id + "_reply", content_type="news_reply",
                                  tweet_text=tweet2, tweet_id=resp2.data['id'],
                                  weekly_theme=get_this_weeks_theme())
        print(f"{source_name} thread posted.")
    except Exception as e:
        print(f"{source_name} tweet 2 post error: {e}")


def post_decrypt_tweet():
    _post_news_tweet("https://decrypt.co", "decrypt.co")


def post_venturebeat_tweet():
    _post_news_tweet("https://venturebeat.com", "venturebeat.com")


# --- VIRAL MIX: TARGET AUDIENCE + MANIFESTO FUSION ---

# Tech news RSS feeds — confirmed 200 from GitHub Actions (CDN-served, no datacenter block)
_RSS_SOURCES = [
    ("The Verge",  "https://www.theverge.com/rss/index.xml"),
    ("TechCrunch", "https://techcrunch.com/feed/"),
    ("Decrypt",    "https://decrypt.co/feed"),
    ("Road to VR", "https://www.roadtovr.com/feed/"),
]

# Keywords to filter headlines relevant to our niche
_NICHE_KEYWORDS = [
    "metaverse", "virtual reality", "augmented reality", " xr", "vr ", " ar ",
    "web3", "nft", "blockchain", "spatial", "3d", "avatar", "digital twin",
    "on-chain", "decentralized", "immersive", "mixed reality",
]

# Nitter public instances (tried first — rarely works from CI)
_NITTER_INSTANCES = [
    "nitter.privacydev.net",
    "nitter.poast.org",
    "nitter.cz",
    "nitter.1d4.us",
]


def fetch_target_tweets_nitter(n_targets=8, tweets_per_user=3):
    """Fetch recent tweets from top target accounts via Nitter RSS (no API key needed)."""
    try:
        with open('targets.json', 'r') as f:
            targets = json.load(f)
    except Exception as e:
        print(f"targets.json load error: {e}")
        return []

    top_targets = sorted(targets, key=lambda t: t.get('engagement_score', 0), reverse=True)[:n_targets]
    headers = {'User-Agent': 'Mozilla/5.0 (compatible; RSS reader)'}
    all_tweets = []

    for target in top_targets:
        username = target.get('username', '')
        if not username:
            continue

        fetched = False
        for instance in _NITTER_INSTANCES:
            try:
                rss_url = f"https://{instance}/{username}/rss"
                resp = requests.get(rss_url, headers=headers, timeout=8)
                if resp.status_code != 200:
                    continue

                root = ET.fromstring(resp.content)
                count = 0
                for item in root.findall('.//item'):
                    if count >= tweets_per_user:
                        break
                    title = item.findtext('title', '').strip()
                    # Skip retweets
                    if 'RT @' in title:
                        continue
                    # Nitter titles are "Name: tweet text" — strip the name prefix
                    if ': ' in title:
                        title = title.split(': ', 1)[1]
                    # Fallback to description stripped of HTML
                    if not title:
                        desc = item.findtext('description', '')
                        title = BeautifulSoup(desc, 'html.parser').get_text(separator=' ', strip=True)
                    if len(title) > 30:
                        all_tweets.append(title)
                        count += 1

                if count > 0:
                    print(f"Nitter ({instance}): {count} tweets from @{username}")
                    fetched = True
                    break

            except Exception as e:
                print(f"Nitter {instance} @{username}: {e}")
                continue

        if not fetched:
            print(f"Nitter: could not fetch @{username} from any instance")

    print(f"Nitter total: {len(all_tweets)} tweets from {n_targets} target accounts")
    return all_tweets[:15]


def fetch_viral_context():
    """Fetch trending news headlines from tech RSS feeds.
    Sources confirmed 200 from GitHub Actions: The Verge, TechCrunch, Decrypt, Road to VR.
    Filters by niche keywords so LLM gets content actually relevant to our space."""
    headers = {'User-Agent': 'Mozilla/5.0 (compatible; RSS reader/1.0)'}
    headlines = []

    for source_name, url in _RSS_SOURCES:
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code != 200:
                print(f"RSS {source_name}: HTTP {resp.status_code}")
                continue

            root = ET.fromstring(resp.content)
            matched = 0
            for item in root.findall('.//item'):
                title_el = item.find('title')
                if title_el is None or not title_el.text:
                    continue
                title = title_el.text.strip()
                if len(title) > 15 and any(kw in title.lower() for kw in _NICHE_KEYWORDS):
                    headlines.append(f"[{source_name}] {title}")
                    matched += 1
                    if matched >= 5:
                        break

            print(f"RSS {source_name}: {matched} relevant headlines")
        except Exception as e:
            print(f"RSS {source_name} error: {e}")

    print(f"Viral context total: {len(headlines)} headlines")
    return headlines[:15]


def fetch_target_tweets(n_targets=10, max_results=20):
    """Fetch trending content from the niche.
    Priority: Twitter API → Nitter RSS → Reddit (reliable fallback)."""
    # 1. Twitter API
    try:
        with open('targets.json', 'r') as f:
            targets = json.load(f)
        top_targets = sorted(targets, key=lambda t: t.get('engagement_score', 0), reverse=True)[:n_targets]
        usernames = [t['username'] for t in top_targets if t.get('username')]
        if usernames:
            client, _ = get_twitter_clients()
            from_clause = " OR ".join(f"from:{u}" for u in usernames)
            resp = client.search_recent_tweets(
                query=f"({from_clause}) -is:retweet -is:reply lang:en",
                max_results=max_results,
                tweet_fields=["public_metrics", "text"],
                sort_order="relevancy",
            )
            if resp.data:
                tweets = sorted(
                    resp.data,
                    key=lambda t: (
                        t.public_metrics.get("like_count", 0)
                        + t.public_metrics.get("retweet_count", 0) * 3
                    ),
                    reverse=True,
                )
                results = [t.text for t in tweets[:5]]
                print(f"Twitter API: {len(results)} tweets fetched.")
                return results
    except Exception as e:
        print(f"Twitter API error ({type(e).__name__}): {e}")

    # 2. Nitter RSS
    nitter_results = fetch_target_tweets_nitter(n_targets)
    if nitter_results:
        return nitter_results

    # 3. Tech news RSS (The Verge, TechCrunch, Decrypt, Road to VR)
    print("Nitter failed, fetching viral context from tech news RSS...")
    return fetch_viral_context()


def generate_thread_reply(main_tweet):
    """Generate a reply that directly extends the main tweet's argument."""
    if not GROQ_API_KEY:
        return None
    prompt = (
        "You just posted this tweet:\n"
        f'"{main_tweet}"\n\n'
        "Write ONE reply to yourself that deepens the argument — the 'because', the implication, or the real stakes.\n"
        "The reply must connect DIRECTLY to what the tweet said. Not a generic question. Not a new topic.\n\n"
        "BAD REPLY: 'what changes when your workspace has no walls?' — disconnected\n"
        "BAD REPLY: 'Rethink trust models.' — vague\n"
        "GOOD REPLY (wallets/dependency tweet): 'the interface is the leash. always has been.'\n"
        "GOOD REPLY (VR retention tweet): 'people don't return to experiences. they return to places.'\n\n"
        f"{TONE_BLOCK}"
        "40-120 characters. No 'we'. Natural follow-through thought.\n"
        "Output ONLY the reply text."
    )
    return _call_llm(prompt, max_tokens=60, temperature=0.88).strip('"\'')


def generate_viral_mix_tweet(target_tweets, manifesto_chunk, source_name):
    """LLM: chain-of-thought viral mix — trending headlines + manifesto + bot memory + weekly theme."""
    voice_ctx = get_voice_context(n=4)
    weekly    = get_this_weeks_theme()
    belief    = _random_belief()

    if target_tweets:
        context_block = (
            "REAL headlines trending in the metaverse, XR, web3, spatial computing space RIGHT NOW:\n"
            + "\n---\n".join(target_tweets[:8])
            + "\n\nPick the most charged topic. Find the tension beneath the surface."
        )
    else:
        context_block = (
            "No external headlines available. Draw from your knowledge of what's most contested "
            "in WebXR, on-chain ownership, virtual studio design, and spatial computing right now."
        )

    prompt = (
        "You are the voice of @decentralize___, a studio building 3D virtual worlds on-chain.\n\n"
        f"{TONE_BLOCK}\n"
        f"{voice_ctx}"
        f'One of our core beliefs: "{belief}"\n\n'
        f"This week's exploration theme: {weekly}\n\n"
        f"{context_block}\n\n"
        "THINK STEP BY STEP:\n"
        "Step 1 — Which headline reveals the deepest tension or the biggest misunderstanding?\n"
        "Step 2 — What does the mainstream believe about this? Where are they wrong?\n"
        "Step 3 — Write ONE tweet from our perspective that corrects Step 2 with precision.\n\n"
        "BAD: 'NEWS: New wallet research...' — restates headline, zero insight\n"
        "BAD: 'Rethink trust models.' — vague slogan\n"
        "GOOD: 'Every wallet preserving UX is protecting the part that keeps users dependent.' — reveals a mechanism\n"
        "GOOD: 'VR retention collapsed because studios kept building tourist attractions, not places to inhabit.' — diagnosis\n\n"
        "100-220 characters. No 'NEWS:' prefix. No 'we'. No links. Expert thinking out loud.\n"
        f"Our manifesto ({source_name}):\n{manifesto_chunk}\n\n"
        "Output ONLY the final tweet text — not the steps."
    )
    return _call_llm(prompt, max_tokens=130, temperature=0.92).strip('"\'')


# ── QUOTE-TWEET MOTORU ────────────────────────────────────────────────────────

def generate_quote_commentary(original_tweet: str) -> str:
    """LLM: target tweet'e quote commentary yaz — kendi sesimizden, keskin görüş."""
    voice_ctx = get_voice_context(n=3)
    belief    = _random_belief()
    prompt = (
        "You are @decentralize___, a studio building 3D virtual worlds on-chain.\n\n"
        f"{TONE_BLOCK}\n"
        f"{voice_ctx}"
        f'One of our beliefs: "{belief}"\n\n'
        "Someone just said this:\n"
        f'"{original_tweet}"\n\n'
        "Write a quote-tweet commentary from our perspective. Options:\n"
        "A. Sharpen their point with a more precise version\n"
        "B. Find the gap in their logic and name it\n"
        "C. Add the dimension they completely missed\n"
        "D. Respectfully disagree with the specific assumption they're making\n\n"
        "Max 200 characters. No 'RT @'. No 'great point'. No sycophancy. Your own voice.\n"
        "Output ONLY the commentary text."
    )
    return _call_llm(prompt, max_tokens=90, temperature=0.88).strip('"\'')


def fetch_target_tweets_with_ids(n_targets: int = 3) -> list[dict]:
    """Target tweet'leri id + text ile birlikte döndür (quote-tweet için)."""
    client, _ = get_twitter_clients()
    try:
        with open('targets.json', 'r', encoding='utf-8') as f:
            targets = json.load(f)
    except Exception:
        return []

    tier1 = [t for t in targets if t.get('followers', 0) < 50_000]
    sample = random.sample(tier1, min(n_targets, len(tier1))) if tier1 else random.sample(targets, min(n_targets, len(targets)))

    results = []
    for target in sample:
        try:
            resp = client.search_recent_tweets(
                query=f"from:{target['username']} -is:retweet -is:reply lang:en",
                max_results=5,
                tweet_fields=["public_metrics", "text"],
                sort_order="relevancy",
            )
            if resp.data:
                best = max(resp.data,
                           key=lambda t: t.public_metrics.get("like_count", 0) + t.public_metrics.get("retweet_count", 0) * 2)
                results.append({"id": str(best.id), "text": best.text, "author": target['username']})
        except Exception as e:
            print(f"fetch_target_tweets_with_ids error for @{target['username']}: {e}")
        time.sleep(1)

    return results


def post_quote_tweet():
    """Free tier'da desteklenmiyor — search_recent_tweets API erişimi gerektirir."""
    print("post_quote_tweet: free tier'da kullanılamaz (search API gerekli). Atlanıyor.")
    return

    client, _ = get_twitter_clients()  # noqa: unreachable

    print("Fetching target tweets for quote-tweet...")
    candidates = fetch_target_tweets_with_ids(n_targets=5)
    if not candidates:
        print("No candidates for quote-tweet, skipping.")
        return

    # En az archived olmayan birini seç
    selected = None
    for c in candidates:
        archive_id = "qt_" + hashlib.md5(c['text'].encode()).hexdigest()[:12]
        if not tweet_archive.is_posted_recently(archive_id, days=14):
            selected = c
            break
    if not selected:
        selected = random.choice(candidates)

    commentary = generate_quote_commentary(selected['text'])
    quality = score_tweet_quality(commentary)
    if quality < 5.5:
        print(f"Quote-tweet quality {quality:.1f}/10 too low, skipping.")
        return

    print(f"Posting quote-tweet of @{selected['author']}:\n{commentary}")
    try:
        resp = client.create_tweet(text=commentary, quote_tweet_id=selected['id'])
        archive_id = "qt_" + hashlib.md5(selected['text'].encode()).hexdigest()[:12]
        tweet_archive.record_post(archive_id, content_type="quote_tweet",
                                  tweet_text=commentary, tweet_id=resp.data['id'],
                                  weekly_theme=get_this_weeks_theme())
        print(f"Quote-tweet posted: {resp.data['id']}")
    except Exception as e:
        print(f"Quote-tweet failed: {e}")


def post_viral_mix_tweet():
    """Fetch top target tweets, mix with manifesto chunk, post one viral tweet + archive."""
    client, _ = get_twitter_clients()
    db = load_db()

    text_items = [
        item
        for category, items in db.items()
        if isinstance(items, list)
        for item in items
        if item.get('type') == 'text' and len(item.get('content', '')) > 500
    ]

    if not text_items:
        print("No text items in database, skipping viral mix.")
        return

    fresh_texts = [i for i in text_items if not tweet_archive.is_posted_recently(i['id'] + '_viral')]
    if not fresh_texts:
        print("Archive: all text items used for viral mix recently, picking random anyway.")
        fresh_texts = text_items
    manifesto_item = random.choice(fresh_texts)
    content = manifesto_item.get('content', '')
    name = manifesto_item.get('name', '')

    words = content.split()
    if len(words) > 100:
        start = random.randint(0, len(words) - 100)
        chunk = ' '.join(words[start:start + 100])
    else:
        chunk = content

    print("Fetching top target tweets for viral mix...")
    target_tweets = fetch_target_tweets()
    if not target_tweets:
        print("No target tweets available (all sources failed), generating from manifesto only...")

    tweet_text = None
    if GROQ_API_KEY:
        for attempt in range(3):
            try:
                candidate = generate_viral_mix_tweet(target_tweets, chunk, name)
                if len(candidate) < 50:
                    print(f"Attempt {attempt+1}: tweet too short ({len(candidate)} chars), retrying...")
                    if len(words) > 100:
                        start = random.randint(0, len(words) - 100)
                        chunk = ' '.join(words[start:start + 100])
                    continue
                if tweet_archive.is_too_similar(candidate):
                    print(f"Viral mix attempt {attempt+1}: too similar, retrying...")
                    if len(words) > 100:
                        start = random.randint(0, len(words) - 100)
                        chunk = ' '.join(words[start:start + 100])
                    continue
                if tweet_archive.is_theme_in_cooldown(candidate):
                    print(f"Viral mix attempt {attempt+1}: theme in cooldown, retrying...")
                    if len(words) > 100:
                        start = random.randint(0, len(words) - 100)
                        chunk = ' '.join(words[start:start + 100])
                    continue
                quality = score_tweet_quality(candidate)
                if quality < 6.0:
                    print(f"Viral mix attempt {attempt+1}: quality {quality:.1f}/10 < 6.0, retrying...")
                    if len(words) > 100:
                        start = random.randint(0, len(words) - 100)
                        chunk = ' '.join(words[start:start + 100])
                    continue
                tweet_text = candidate
                break
            except Exception as e:
                print(f"Viral mix generation error (attempt {attempt+1}): {e}")
        if not tweet_text:
            print("Falling back to generate_viral_tweet...")
            try:
                tweet_text = generate_viral_tweet(chunk, name, [])
            except Exception as e2:
                print(f"generate_viral_tweet fallback error: {e2}")

    if not tweet_text:
        sentences = [
            s.strip() for s in re.split(r'(?<=[.!?])\s+', content)
            if 70 < len(s.strip()) < 240
            and not s.strip().isupper()
        ]
        tweet_text = random.choice(sentences) if sentences else content[:240]

    tweet_text = format_tweet(trim_for_format(tweet_text))

    print(f"Posting viral mix tweet ({len(tweet_text)} chars):\n{tweet_text}")
    try:
        resp = client.create_tweet(text=tweet_text)
        tweet_id = resp.data['id']
        archive_id = "viral_" + hashlib.md5(tweet_text.encode()).hexdigest()[:12]
        tweet_archive.record_post(archive_id, content_type="viral_mix",
                                  tweet_text=tweet_text, tweet_id=tweet_id,
                                  weekly_theme=get_this_weeks_theme())
        tweet_archive.record_post(manifesto_item['id'] + '_viral', content_type="viral_mix_source",
                                  weekly_theme=get_this_weeks_theme())
        question = generate_thread_reply(tweet_text)
        if question:
            client.create_tweet(text=question, in_reply_to_tweet_id=tweet_id)
        print(f"Viral mix tweet posted:\n{tweet_text}\n→ {question}")
    except tweepy.errors.Forbidden as e:
        api_codes = getattr(e, 'api_codes', [])
        if 187 in api_codes:
            print("Duplicate viral mix tweet, skipping.")
        else:
            raise


# --- COMMUNITY PULSE THREAD: Haftalık RSS özeti ---

def post_community_pulse_thread():
    """Her Pazartesi: niche RSS'ten bu haftanın önemli gelişmelerini thread olarak özetle."""
    client, _ = get_twitter_clients()

    pulse_id = "pulse_" + datetime.now(timezone.utc).strftime("%Y_W%V")
    if tweet_archive.is_posted_recently(pulse_id, days=6):
        print("Community pulse already posted this week, skipping.")
        return

    # Tüm niche RSS kaynaklarından başlıkları topla
    all_headlines = []
    niche_kw = ["vr", "xr", "ar", "virtual", "spatial", "metaverse", "webxr", "3d", "immersive",
                "decentrali", "on-chain", "blockchain", "web3", "avatar", "digital twin"]
    feeds = {**_RSS_FEEDS, **_CONTEXT_RSS_FEEDS}

    for source, feed_url in feeds.items():
        try:
            articles = _parse_rss_all(feed_url, source)
            for a in articles[:6]:
                title_lower = a['title'].lower()
                if any(kw in title_lower for kw in niche_kw):
                    all_headlines.append(f"[{source}] {a['title']}")
        except Exception as e:
            print(f"RSS fetch error {source}: {e}")

    if not all_headlines:
        print("No niche headlines for community pulse, skipping.")
        return

    print(f"Community pulse: {len(all_headlines)} niche headlines found.")
    headlines_block = "\n".join(all_headlines[:25])

    prompt = (
        "You are @decentralize___, thought leader in WebXR, virtual design, and spatial computing.\n"
        f"{TONE_BLOCK}"
        f"Here are this week's niche headlines:\n{headlines_block}\n\n"
        "Write EXACTLY 4 tweet-sized insights for a weekly pulse thread.\n"
        "Rules:\n"
        "- Each insight is a standalone synthesis, NOT a headline summary\n"
        "- Find the pattern nobody else is naming\n"
        "- Use first-person perspective\n"
        "- Max 200 chars each, no hashtags\n"
        "- Each on its own line\n"
        "Output: 4 lines only."
    )
    try:
        raw = _call_llm(prompt, max_tokens=400, temperature=0.88)
        insights = [l.strip() for l in raw.strip().split('\n') if l.strip()][:4]
    except Exception as e:
        print(f"Pulse LLM error: {e}")
        return

    if not insights:
        print("No pulse insights generated, skipping.")
        return

    header = "this week in virtual design & spatial computing ꩜"
    print(f"Posting community pulse thread ({len(insights)} insights)...")

    try:
        resp = client.create_tweet(text=header)
        parent_id = resp.data['id']
        tweet_archive.record_post(pulse_id, content_type="community_pulse",
                                  tweet_text=header, tweet_id=parent_id,
                                  weekly_theme=get_this_weeks_theme())
    except Exception as e:
        print(f"Pulse header error: {e}")
        return

    for i, insight in enumerate(insights):
        tweet_text = format_tweet(insight)
        if len(tweet_text) > 280:
            tweet_text = insight[:274] + "... ꩜"
        try:
            resp = client.create_tweet(text=tweet_text, in_reply_to_tweet_id=parent_id)
            parent_id = resp.data['id']
            print(f"  Insight {i+1}: {tweet_text[:70]}...")
        except Exception as e:
            print(f"  Insight {i+1} error: {e}")

    print("Community pulse thread done.")


# --- DATA VIZ TWEET: Haftalık niche keyword frekans grafiği ---

def post_data_viz_tweet():
    """Haftada 1: niche konuların RSS'deki frekansını bar chart ile görselleştir ve tweet'le."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import tempfile as _tempfile
    import os as _os

    client, api = get_twitter_clients()

    viz_id = "dataviz_" + datetime.now(timezone.utc).strftime("%Y_W%V")
    if tweet_archive.is_posted_recently(viz_id, days=6):
        print("Data viz already posted this week, skipping.")
        return

    # Keyword → eşleşme sayısı
    keyword_map = {
        "WebXR":            ["webxr", "web xr"],
        "Metaverse":        ["metaverse"],
        "Spatial Computing":["spatial computing", "spatial"],
        "Virtual Reality":  ["virtual reality", "vr "],
        "Decentralized":    ["decentrali", "on-chain", "web3"],
        "AI + 3D":          ["generative 3d", "ai design", "ai 3d", "3d ai"],
    }
    counts = {k: 0 for k in keyword_map}

    for source, feed_url in {**_RSS_FEEDS, **_CONTEXT_RSS_FEEDS}.items():
        try:
            articles = _parse_rss_all(feed_url, source)
            for a in articles[:10]:
                combined = (a['title'] + " " + a.get('summary', '')).lower()
                for label, kws in keyword_map.items():
                    if any(kw in combined for kw in kws):
                        counts[label] += 1
        except Exception:
            pass

    if sum(counts.values()) == 0:
        print("All zero counts, skipping data viz.")
        return

    sorted_items = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    labels = [k for k, v in sorted_items if v > 0]
    values = [v for k, v in sorted_items if v > 0]
    if not labels:
        print("No positive counts, skipping data viz.")
        return

    # Dark-theme bar chart
    fig, ax = plt.subplots(figsize=(9, 4.5))
    fig.patch.set_facecolor('#0d1117')
    ax.set_facecolor('#0d1117')
    bars = ax.barh(labels, values, color='#FF3B6F', height=0.55)
    ax.set_xlabel('mentions in tech media this week', color='#8b949e', fontsize=10)
    ax.tick_params(colors='#e6edf3', labelsize=10)
    for spine in ['top', 'right']:
        ax.spines[spine].set_visible(False)
    for spine in ['bottom', 'left']:
        ax.spines[spine].set_color('#30363d')
    ax.xaxis.label.set_color('#8b949e')
    for bar, val in zip(bars, values):
        ax.text(bar.get_width() + 0.08, bar.get_y() + bar.get_height() / 2,
                str(val), va='center', color='#e6edf3', fontsize=9)
    ax.set_title('what tech media is covering this week', color='#e6edf3', fontsize=12, pad=10)
    plt.tight_layout()

    tmp_path = None
    try:
        with _tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
            tmp_path = tmp.name
        plt.savefig(tmp_path, dpi=150, bbox_inches='tight', facecolor='#0d1117')
        plt.close()

        # LLM caption
        top_topic = labels[0]
        caption_prompt = (
            f"You are @decentralize___.\n{TONE_BLOCK}"
            f"Write a 1-sentence tweet for a data chart showing '{top_topic}' dominates tech media coverage this week. "
            f"Be provocative — not just descriptive. What does this signal? Max 150 chars."
        )
        try:
            caption = _call_llm(caption_prompt, max_tokens=50, temperature=0.9).strip('"\'')
        except Exception:
            caption = f"{top_topic} is dominating the conversation. the question is whether the industry is building or just talking."

        tweet_text = format_tweet(caption)
        if len(tweet_text) > 280:
            tweet_text = caption[:274] + "... ꩜"

        media = api.media_upload(tmp_path)
        resp = client.create_tweet(text=tweet_text, media_ids=[media.media_id_string])
        tweet_archive.record_post(viz_id, content_type="data_viz",
                                  tweet_text=tweet_text, tweet_id=resp.data['id'],
                                  weekly_theme=get_this_weeks_theme())
        print(f"Data viz tweet posted: {resp.data['id']}\n{tweet_text}")
    except Exception as e:
        print(f"Data viz error: {e}")
    finally:
        if tmp_path:
            try:
                _os.unlink(tmp_path)
            except Exception:
                pass


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else None

    # ── Dinlenme günü (Pazar = 6): sadece morning/pulse/viz çalışır ────────────
    _QUIET_DAY = 6  # Sunday
    _today = datetime.now(timezone.utc).weekday()
    # None: argümansız çalıştırıldığında (test/manual) saat-bazlı routing yürür —
    # bu da kendi içinde quiet day'e saygı gösterir, burada bloklanmamalı.
    _quiet_exempt = {"morning", "community_pulse", "data_viz", "drift_check", "quote_tweet", None}
    if _today == _QUIET_DAY and mode not in _quiet_exempt:
        print(f"Quiet day (Sunday) — skipping '{mode}' to maintain selectivity. Only morning/pulse/viz run today.")
        sys.exit(0)

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
    elif mode == "viral_mix":
        post_viral_mix_tweet()
    elif mode == "community_pulse":
        post_community_pulse_thread()
    elif mode == "data_viz":
        post_data_viz_tweet()
    elif mode == "quote_tweet":
        post_quote_tweet()
    else:
        # Time-based fallback (for backward-compatible manual/test runs)
        current_utc_hour = datetime.now(timezone.utc).hour
        if current_utc_hour == 7:
            post_morning_tweet()
        elif current_utc_hour == 9:
            post_decrypt_tweet()
        elif current_utc_hour == 11:
            post_artwork_tweet()
        elif current_utc_hour == 13:
            post_venturebeat_tweet()
        elif current_utc_hour in [15, 18]:
            post_evening_tweet()
        elif current_utc_hour in [16, 20]:
            post_controversial_evening_tweet()
        else:
            print(f"Test Mode (Hour: {current_utc_hour} UTC). Running Evening routine...")
            post_evening_tweet()
