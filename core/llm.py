"""
core/llm.py
-----------
Tüm LLM çağrıları ve tweet üretici fonksiyonlar.
"""
import os
import json
import re

import groq as groq_sdk

from core.voice import TONE_BLOCK, get_this_weeks_theme, random_belief, get_voice_context
import tweet_archive

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

# ── Multi-model rotation ───────────────────────────────────────────────────────
_LLM_MODELS = [
    "llama-3.3-70b-versatile",   # Pazartesi — en güçlü
    "llama-3.3-70b-versatile",   # Salı — ağırlık 2x
    "mixtral-8x7b-32768",        # Çarşamba — farklı ses
    "llama-3.3-70b-versatile",   # Perşembe
    "gemma2-9b-it",              # Cuma — daha direkt üslup
    "llama-3.3-70b-versatile",   # Cumartesi
    "mixtral-8x7b-32768",        # Pazar
]


def _get_model() -> str:
    from datetime import datetime, timezone
    day = datetime.now(timezone.utc).weekday()
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


def score_tweet_quality(tweet_text: str) -> float:
    """Tweet'i LLM'e puanlat (1-10 × 3 eksen). Ortalama 6 altıysa reddet."""
    if not GROQ_API_KEY:
        return 7.0
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
        try:
            scores = json.loads(raw.strip())
        except (json.JSONDecodeError, ValueError):
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
        return 7.0


def is_semantically_duplicate(candidate: str) -> bool:
    """LLM tabanlı semantic benzerlik kontrolü."""
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
        return False


def distill_to_tweet(chunk: str, source_name: str) -> str:
    voice_ctx = get_voice_context(n=3)
    prompt = (
        "You write for a digital design studio (Decentralize Design) that builds virtual worlds and manifestos.\n"
        "From the text below, extract or rephrase ONE powerful, self-contained thought as a tweet (max 130 chars).\n"
        "Rules: no quotes, no 'we believe / this manifesto / our studio', reads as a standalone statement.\n"
        f"{TONE_BLOCK}"
        f"{voice_ctx}"
        f"Output only the tweet text.\n\nSource: {source_name}\nText:\n{chunk}"
    )
    client = groq_sdk.Groq(api_key=GROQ_API_KEY)
    resp = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=60,
        temperature=0.8,
    )
    return resp.choices[0].message.content.strip()


def generate_viral_tweet(chunk: str, source_name: str, context_tweets: list) -> str:
    """Chain-of-thought viral tweet — manifesto + context + bot memory + weekly theme."""
    context_str = ""
    if context_tweets:
        context_str = (
            "Trending conversation in this space (inspiration only, do NOT copy or quote):\n"
            + "\n---\n".join(context_tweets[:2])
            + "\n\n"
        )
    belief = random_belief()
    belief_line = f'One of our core beliefs: "{belief}"\n\n' if belief else ""
    voice_ctx = get_voice_context(n=4)
    weekly = get_this_weeks_theme()

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


def generate_controversial_tweet(chunk: str, source_name: str, context_tweets: list) -> str:
    """Chain-of-thought contrarian tweet — intellectually provocative, not rage-bait."""
    context_str = ""
    if context_tweets:
        context_str = (
            "Trending takes (inspiration only, do NOT copy):\n"
            + "\n---\n".join(context_tweets[:2])
            + "\n\n"
        )
    belief = random_belief()
    belief_line = f'One of our contested beliefs: "{belief}"\n\n' if belief else ""
    voice_ctx = get_voice_context(n=4)
    weekly = get_this_weeks_theme()

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


def generate_media_caption(name: str, description: str, type_label: str) -> str:
    """One complete, self-contained caption for a media item. ≤120 chars."""
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


def generate_artwork_tweet(name: str, description: str, categories: dict) -> str:
    """Short punchy tweet for an artwork drop. ≤ 130 chars."""
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


def generate_news_headline(title: str, article_text: str, source: str) -> str:
    """1-sentence factual headline summary. Max 115 chars."""
    prompt = (
        "Summarize this news article in ONE factual sentence. Max 115 characters.\n"
        "Rules: state what happened, no opinion, no hashtags, no 'this article...'. Just the fact.\n\n"
        f"Source: {source}\nTitle: {title}\nArticle:\n{article_text[:1500]}\n\n"
        "Output ONLY the summary sentence."
    )
    return _call_llm(prompt, max_tokens=50, temperature=0.3).strip('"\'')


def generate_news_tweet(title: str, article_text: str, source: str, prior_opinions: list = None) -> str:
    """3-5 word sharp fragment as news commentary."""
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


def generate_thread_reply(main_tweet: str) -> str | None:
    """Reply that directly extends the main tweet's argument."""
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


def generate_quote_commentary(original_tweet: str) -> str:
    """LLM: target tweet'e quote commentary — kendi sesimizden, keskin görüş."""
    voice_ctx = get_voice_context(n=3)
    belief = random_belief()
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


def generate_viral_mix_tweet(target_tweets: list, manifesto_chunk: str, source_name: str) -> str:
    """Chain-of-thought viral mix — trending headlines + manifesto + bot memory + weekly theme."""
    voice_ctx = get_voice_context(n=4)
    weekly = get_this_weeks_theme()
    belief = random_belief()

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
