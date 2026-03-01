import os
import json
import random
import tweepy
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import google.generativeai as genai
import time

# --- 1. API KEYS ---
TWITTER_API_KEY = os.environ.get("TWITTER_API_KEY")
TWITTER_API_SECRET = os.environ.get("TWITTER_API_SECRET")
TWITTER_ACCESS_TOKEN = os.environ.get("TWITTER_ACCESS_TOKEN")
TWITTER_ACCESS_TOKEN_SECRET = os.environ.get("TWITTER_ACCESS_TOKEN_SECRET")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# --- TYPE LABELS (Ne olduğunu belirten etiketler) ---
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

def download_media(media_url):
    """Medyayı sunucuya indirir ve formatını belirler."""
    try:
        ext = ".jpg"
        lower_url = media_url.lower()
        if ".mp4" in lower_url: ext = ".mp4"
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
    """HTML içinden hem ilk anlamlı cümleyi hem de medyayı (video/görsel) çeker."""
    text_content = None
    media_url = None
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=15)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # 1. METİN ÇEKME (Sadece ilk anlamlı/kısa cümleyi al)
            paragraphs = soup.find_all(['p', 'h1', 'h2', 'div'])
            texts = [p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 30]
            if texts:
                text_content = texts[0] # İlk bulunan anlamlı metin
            
            # 2. MEDYA ÇEKME (Sırasıyla video kaynağı, sosyal medya resmi veya standart resim)
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
                        
            # Medya URL'si göreceli (relative) ise tam URL'ye çevir
            if media_url and media_url.startswith('/'):
                base_url = "/".join(url.split("/")[:3]) # https://decentralize.design
                media_url = f"{base_url}{media_url}"
                
    except Exception as e:
        print(f"HTML Scrape Error: {e}")
        
    return text_content, media_url

# --- MORNING: MULTIMEDIA ARCHIVE EXCAVATOR ---
def post_morning_tweet():
    client, api = get_twitter_clients()
    
    with open('database.json', 'r', encoding='utf-8') as file:
        db = json.load(file)
        
    all_items = []
    for category, items in db.items():
        for item in items:
            all_items.append(item)
            
    selected = random.choice(all_items)
    name = selected.get('name', 'ARCHIVE_ITEM')
    item_type = selected.get('type', 'folder')
    type_label = TYPE_LABELS.get(item_type, '[Archive]')
    desc = selected.get('description', '')
    
    # URL'den eski siteyi tamamen kazı
    raw_url = selected.get('url', 'https://decentralize.design')
    url = raw_url.replace('digitalforgerywork.shop', 'decentralize.design')
    
    media_url_to_download = None
    extracted_text = None
    
    # EĞER İÇERİK HTML İSE İÇİNE GİR VE MEDYAYI ORADAN KOPAR
    if item_type in ['html', 'website'] and url.startswith('http'):
        extracted_text, html_media = extract_data_from_html(url)
        if html_media:
            media_url_to_download = html_media

    # EĞER HTML DEĞİLSE VEYA HTML İÇİNDE MEDYA BULUNAMADIYSA VERİTABANINA BAK
    if not media_url_to_download:
        if any(ext in url.lower() for ext in ['.mp4', '.png', '.jpg', '.jpeg', '.gif']):
            media_url_to_download = url
        elif selected.get('thumbnailUrl'):
            media_url_to_download = selected['thumbnailUrl'].replace('digitalforgerywork.shop', 'decentralize.design')
        elif selected.get('iconUrl'):
            media_url_to_download = selected['iconUrl'].replace('digitalforgerywork.shop', 'decentralize.design')

    # MEDYAYI İNDİR VE TWITTER'A YÜKLE
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

    # TWEET METNİNİ OLUŞTUR (Sadece başlık, etiket ve tek bir cümle)
    display_text = ""
    if extracted_text:
        display_text = extracted_text[:100] + "..." if len(extracted_text) > 100 else extracted_text
    elif desc:
        # Açıklamanın sadece ilk cümlesini al
        display_text = desc.split('.')[0] + "."
        if len(display_text) > 120:
            display_text = display_text[:117] + "..."
            
    # Temiz ve profesyonel format (Hashtag yok)
    tweet_text = f"{type_label} {name}\n\n{display_text}\n\nENTER: {url}"
    
    # TWEETİ GÖNDER
    if media_ids:
        client.create_tweet(text=tweet_text, media_ids=media_ids)
    else:
        client.create_tweet(text=tweet_text)
        
    print(f"Morning broadcast complete: {name}")

# --- EVENING: CYBER PHILOSOPHER ---
def post_evening_tweet():
    client, api = get_twitter_clients()
    genai.configure(api_key=GEMINI_API_KEY)
    
    model = genai.GenerativeModel('gemini-1.5-pro-latest')
    
    prompt = """
    You are an avant-garde Spatial Web and Metaverse architecture studio based in Milan named 'Decentralize Design'. 
    Your core philosophy is 'The Internet is Still Flat' and 'Religious Robotics'. You oppose humans being trapped in 2D screens and advocate for the digital materiality of code and space.
    
    Please find a CURRENT tech news/trend from today or this week regarding 'Metaverse, Spatial Web, AR/VR, Digital Identity, or Virtual Architecture'.
    Write a cold, philosophical, dark, metallic, and sharp English tweet consisting of EXACTLY 2 sentences.
    1st sentence: State the current event or trend objectively but coldly.
    2nd sentence: Make a striking, post-physical commentary or rebellion from your studio's perspective.
    
    DO NOT USE ANY HASHTAGS. Do not use quotes around the text. Do not add introductory phrases. Just provide the raw tweet text in English.
    """
    
    response = model.generate_content(prompt)
    tweet_text = response.text.strip()
    # Olası bir hatada yapay zekanın eklediği hashtagleri zorla temizle
    tweet_text = ' '.join(word for word in tweet_text.split() if not word.startswith('#'))
    
    client.create_tweet(text=tweet_text)
    print(f"Evening broadcast complete:\n{tweet_text}")

if __name__ == "__main__":
    current_utc_hour = datetime.utcnow().hour
    
    # UTC 6 = TR Saati 09:00 / UTC 16 = TR Saati 19:00
    if current_utc_hour in [5, 6, 7]:
        post_morning_tweet()
    elif current_utc_hour in [15, 16, 17]:
        post_evening_tweet()
    else:
        print(f"Test Mode (Hour: {current_utc_hour} UTC). Running Morning routine...")
        post_morning_tweet()
