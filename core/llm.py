"""
core/llm.py
-----------
Tüm LLM çağrıları ve tweet üretici fonksiyonlar.
"""
import os
import json
import random as _rnd  # BUG FIX #15: her fonksiyon içinde tekrar import ediliyordu
import re

import groq as groq_sdk

from core.voice import TONE_BLOCK, get_this_weeks_theme, random_belief, get_voice_context, get_recent_patterns
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
    """Tweet'i LLM'e puanlat (1-10 × 4 eksen). En düşük skoru döndür — hepsi 9+ olmalı.

    4 eksen:
      ORIGINALITY  — okuyucunun daha önce duymadığı bir şey mi söylüyor?
      SPECIFICITY  — belirsiz değil, somut bir iddia var mı?
      PROVOCATION  — okuyucunun kafasında bir soru bırakıyor mu?
      CLARITY      — konu bağlamı olmadan da ne hakkında olduğu net mi?
    """
    if not GROQ_API_KEY:
        return 9.0
    prompt = (
        "Rate this tweet on exactly 4 axes, each scored 1-10:\n"
        "1. ORIGINALITY: Does it say something the reader hasn't encountered before?\n"
        "2. SPECIFICITY: Does it make a precise, non-generic, non-vague claim?\n"
        "3. PROVOCATION: Does it leave an unanswerable question in the reader's mind?\n"
        "4. CLARITY: Without any context, can the reader immediately identify the specific "
        "subject/technology/problem this tweet is about? "
        "(Score 1 if it uses vague pronouns like 'they' or abstract subjects with no referent. "
        "Score 10 if the subject is named explicitly and concretely in the first few words.)\n\n"
        f'Tweet: "{tweet_text}"\n\n'
        'Respond ONLY with JSON: {"originality": N, "specificity": N, "provocation": N, "clarity": N}'
    )
    try:
        raw = _call_llm(prompt, max_tokens=50, temperature=0.2)
        try:
            scores = json.loads(raw.strip())
        except (json.JSONDecodeError, ValueError):
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if not match:
                return 0.0
            scores = json.loads(match.group())
        min_score = min(
            scores.get("originality", 0),
            scores.get("specificity", 0),
            scores.get("provocation", 0),
            scores.get("clarity", 0),
        )
        print(
            f"Quality score (min): {min_score}/10 — "
            f"O:{scores.get('originality')} S:{scores.get('specificity')} "
            f"P:{scores.get('provocation')} C:{scores.get('clarity')}"
        )
        return float(min_score)
    except Exception as e:
        print(f"Quality scoring failed: {e}")
        return 0.0


def score_tweet_detail(tweet_text: str) -> dict:
    """Incoming tweet'i 4 eksen üzerinden puanla.

    Returns:  # BUG FIX #11: iq3 eksikti
        {"o": int, "s": int, "p": int, "c": int, "avg": float, "iq": int, "iq3": int}
        iq  = (O+S+P+C)/4 * 16.5  →  kendi tweetlerimiz için
        iq3 = (O+S+C)/3   * 16.5  →  başkasının tweeti (P ekseni hariç)
    Returns pass-through dict if GROQ_API_KEY not set.
    """
    if not GROQ_API_KEY:
        return {"o": 9, "s": 9, "p": 9, "c": 9, "avg": 9.0, "iq": 148, "iq3": 148}
    prompt = (
        "Rate this tweet on exactly 4 axes, each scored 1-10:\n"
        "1. ORIGINALITY: Does it say something the reader hasn't encountered before?\n"
        "2. SPECIFICITY: Does it make a precise, non-generic, non-vague claim?\n"
        "3. PROVOCATION: Does it leave an unanswerable question in the reader's mind?\n"
        "4. CLARITY: Without any context, can the reader immediately identify the specific "
        "subject/technology/problem this tweet is about?\n\n"
        f'Tweet: "{tweet_text}"\n\n'
        'Respond ONLY with JSON: {"originality": N, "specificity": N, "provocation": N, "clarity": N}'
    )
    try:
        raw = _call_llm(prompt, max_tokens=50, temperature=0.2)
        try:
            scores = json.loads(raw.strip())
        except (json.JSONDecodeError, ValueError):
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if not match:
                return {"o": 0, "s": 0, "p": 0, "c": 0, "avg": 0.0, "iq": 0, "iq3": 0}
            scores = json.loads(match.group())
        o = int(scores.get("originality", 0))
        s = int(scores.get("specificity", 0))
        p = int(scores.get("provocation", 0))
        c = int(scores.get("clarity", 0))
        avg = round((o + s + p + c) / 4, 1)
        iq = round(avg * 16.5)
        iq3 = round((o + s + c) / 3 * 16.5)  # 3-axis: P hariç (başkalarının tweet'i)
        print(f"  Score: IQ={iq} IQ3={iq3} avg={avg} O:{o} S:{s} P:{p} C:{c}")
        return {"o": o, "s": s, "p": p, "c": c, "avg": avg, "iq": iq, "iq3": iq3}
    except Exception as e:
        print(f"score_tweet_detail failed: {e}")
        return {"o": 0, "s": 0, "p": 0, "c": 0, "avg": 0.0, "iq": 0, "iq3": 0}


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
    voice_ctx = get_voice_context(n=5)
    banned = get_recent_patterns(n=8)
    prompt = (
        "You write for a digital design studio (Decentralize Design) that builds virtual worlds and manifestos.\n"
        "From the text below, extract or rephrase ONE powerful, self-contained thought as a tweet.\n"
        "Rules: no quotes, no 'we believe / this manifesto / our studio', reads as a standalone statement.\n"
        "Length: 50–200 chars. Stop when the idea is complete — don't pad.\n"
        f"{TONE_BLOCK}"
        f"{voice_ctx}"
        f"{banned}"
        f"Output only the tweet text.\n\nSource: {source_name}\nText:\n{chunk}"
    )
    return _call_llm(prompt, max_tokens=120, temperature=0.85)


# ── Format rotasyon menüsü — tüm generate fonksiyonları bunu kullanır ─────────
_FORMAT_MENU = """
Pick ONE format below that is MOST DIFFERENT from your recent tweets. Do not use the same format twice in a row.

FORMAT OPTIONS:
[A] Diagnosis — name the exact mechanism behind a failure/problem nobody talks about
[B] Inversion — take a widely held truth and find where it quietly breaks down
[C] Specificity bomb — one hyper-specific observation that implies a much bigger idea
[D] Sequence — "first X happens. then Y. most people only see X."
[E] Named gap — "there is no word for [X] yet. but there should be."
[F] Uncomfortable admission — something true that people in this space don't say out loud
[G] Practitioner's note — something you only know from actually building, not from reading
[H] Time collapse — connect something ancient/permanent to something happening right now
[I] Anti-hype — deflate a trend by naming what it actually is beneath the surface
[J] The question nobody asks — not rhetorical, genuinely open, specific enough to sting

After you decide which format fits, write the tweet. Do not label the format in the output.
"""


def generate_viral_tweet(chunk: str, source_name: str, context_tweets: list) -> str:
    """Chain-of-thought viral tweet — manifesto + context + bot memory + weekly theme."""
    context_str = ""
    if context_tweets:
        context_str = (
            "Trending conversation (inspiration only — do NOT copy):\n"
            + "\n---\n".join(context_tweets[:3])
            + "\n\n"
        )
    belief = random_belief()
    belief_line = f'Core belief: "{belief}"\n\n' if belief else ""
    voice_ctx = get_voice_context(n=8)
    banned = get_recent_patterns(n=10)
    weekly = get_this_weeks_theme()

    prompt = (
        "You are the voice of @decentralize___, a studio building 3D virtual worlds on-chain.\n\n"
        f"{TONE_BLOCK}\n"
        f"{voice_ctx}"
        f"{banned}"
        f"{belief_line}"
        f"This week's lens: {weekly}\n\n"
        f"{_FORMAT_MENU}\n"
        "NOW WRITE:\n"
        "Step 1 — What is the most counterintuitive thing the source material reveals?\n"
        "Step 2 — What does mainstream assume that is demonstrably wrong?\n"
        "Step 3 — Choose a format from above. Write the tweet.\n\n"
        "CLARITY RULE: Name the specific technology, platform, or industry in the first 5 words. "
        "Never 'they', 'it', 'this' without explicit referent. Zero-context reader must immediately know the subject.\n"
        "Length: 80–260 characters. No artificial padding. Stop when the point lands.\n"
        f"{context_str}"
        f"Source ({source_name}) — use as CONTEXT and INSPIRATION, not as text to paraphrase:\n{chunk}\n\n"
        "Output ONLY the final tweet text."
    )
    return _call_llm(prompt, max_tokens=180, temperature=0.92)


def generate_controversial_tweet(chunk: str, source_name: str, context_tweets: list) -> str:
    """Contrarian tweet — intellectually provocative, not rage-bait."""
    context_str = ""
    if context_tweets:
        context_str = (
            "Trending takes (do NOT copy):\n"
            + "\n---\n".join(context_tweets[:3])
            + "\n\n"
        )
    belief = random_belief()
    belief_line = f'Contested belief: "{belief}"\n\n' if belief else ""
    voice_ctx = get_voice_context(n=8)
    banned = get_recent_patterns(n=10)
    weekly = get_this_weeks_theme()

    prompt = (
        "You are the voice of @decentralize___, a studio building 3D virtual worlds on-chain.\n\n"
        f"{TONE_BLOCK}\n"
        f"{voice_ctx}"
        f"{banned}"
        f"{belief_line}"
        f"This week's lens: {weekly}\n\n"
        f"{_FORMAT_MENU}\n"
        "NOW WRITE (contrarian edition):\n"
        "Step 1 — Find ONE widely held assumption in this space that is quietly wrong.\n"
        "Step 2 — What is actually true, and why does it make people uncomfortable to say?\n"
        "Step 3 — Choose a format. Write the tweet.\n\n"
        "CLARITY RULE: Name the specific technology, platform, or industry in the first 5 words. "
        "Never 'they', 'it', 'this' without explicit referent. Zero-context reader must immediately know the subject.\n"
        "Tone: precision over provocation. IQ 150 thinking, not hot take. "
        "80–260 chars. Stop when it lands.\n\n"
        f"{context_str}"
        f"Source ({source_name}) — use as CONTEXT and INSPIRATION, not as text to paraphrase:\n{chunk}\n\n"
        "Output ONLY the final tweet text."
    )
    return _call_llm(prompt, max_tokens=180, temperature=0.94)


def generate_media_caption(name: str, description: str, type_label: str) -> str:
    """Self-contained caption for a media item — visual, precise, no hard char limit."""
    voice_ctx = get_voice_context(n=3)
    banned = get_recent_patterns(n=6)
    prompt = (
        "You write captions for @decentralize___ (a studio building 3D virtual worlds on-chain).\n"
        "Write ONE caption for this media item.\n"
        "Rules: makes sense standalone, no studio name, no 'this is...'. "
        "State what it IS or what it MEANS. Visual, precise, complete.\n"
        "Length: 40–160 chars. Stop when the idea is expressed — don't pad.\n"
        f"{TONE_BLOCK}"
        f"{voice_ctx}"
        f"{banned}"
        f"Item: {type_label} — {name}\n"
        f"Description: {description}\n\n"
        "Output ONLY the caption."
    )
    return _call_llm(prompt, max_tokens=80, temperature=0.78)


def generate_artwork_tweet(name: str, description: str, categories: dict) -> str:
    """Tweet for an artwork drop — conceptually sharp, no hard char limit."""
    meta = ", ".join(
        f"{k}: {v}" for k, v in categories.items()
        if v and k in ('year', 'type', 'medium', 'collection')
    )
    voice_ctx = get_voice_context(n=3)
    banned = get_recent_patterns(n=6)
    prompt = (
        "You write for @decentralize___, a studio building 3D virtual worlds on-chain.\n"
        "Write ONE tweet announcing this artwork. "
        "No studio name, no quotes, no 'this is...'. "
        "State what makes it conceptually significant. Visual and precise.\n"
        "Length: 50–200 chars. Stop when it lands.\n"
        f"{TONE_BLOCK}"
        f"{voice_ctx}"
        f"{banned}"
        f"Artwork: {name}\n"
        f"Description: {description}\n"
        f"Details: {meta}\n\n"
        "Output ONLY the tweet text."
    )
    return _call_llm(prompt, max_tokens=100, temperature=0.82)


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
    """News commentary — varied format, no 3-5 word lock, bellek entegreli."""
    prior_block = ""
    if prior_opinions:
        prior_lines = "\n".join(f"- {o}" for o in prior_opinions[:5])
        prior_block = (
            f"Your previous takes on similar topics:\n{prior_lines}\n"
            "Evolve the thinking — don't repeat the same angle.\n\n"
        )
    banned = get_recent_patterns(n=10)
    voice_ctx = get_voice_context(n=6)

    # Farklı commentary tarzları arasında rotate
    styles = [
        "Write ONE sharp observation about what this news actually means for virtual worlds, on-chain ownership, or spatial computing. Not a summary. Your angle.",
        "Write the thing this article doesn't say but implies. One sentence or fragment — the subtext.",
        "Write your reaction as someone who builds in this space. What does this news change, break, or confirm for you?",
        "Write the uncomfortable implication of this news. What does it mean for the next 3 years?",
        "Write what most people will miss when they read this headline. The detail that actually matters.",
    ]
    style = _rnd.choice(styles)

    prompt = (
        "You are @decentralize___, building 3D virtual worlds on-chain.\n\n"
        f"{TONE_BLOCK}\n"
        f"{voice_ctx}"
        f"{banned}"
        f"{prior_block}"
        f"{style}\n\n"
        "Rules: no 'this article says', no 'breaking:', no @mentions, no links. "
        "Can be a full sentence, a fragment, or 2 short sentences. "
        "40–200 chars. Hashtags optional (max 2 if used).\n\n"
        f"Source: {source}\nTitle: {title}\n"
        f"Article:\n{article_text[:2000]}\n\n"
        "Output ONLY the commentary."
    )
    return _call_llm(prompt, max_tokens=120, temperature=0.93).strip('"\'')


def generate_thread_reply(main_tweet: str) -> str | None:
    """Self-reply that extends the main tweet — kendi thread'ine devam."""
    if not GROQ_API_KEY:
        return None
    banned = get_recent_patterns(n=8)

    extensions = [
        "Write the 'because' — the underlying mechanism that makes the tweet true.",
        "Write the implication — if the tweet is right, what has to change?",
        "Write the edge case — where does this break down, and what does that reveal?",
        "Write the real stakes — why does this actually matter in 5 years?",
        "Write the practitioner's addendum — something you know from building that makes this even more true.",
    ]
    ext = _rnd.choice(extensions)

    prompt = (
        "You just posted this tweet:\n"
        f'"{main_tweet}"\n\n'
        f"{ext}\n\n"
        "Rules:\n"
        "- Connect DIRECTLY to what the tweet said — not a new topic\n"
        "- No generic questions ('what do you think?')\n"
        "- No 'we' or studio branding\n"
        "- Lowercase is fine\n"
        f"{banned}"
        "BAD: 'what changes when your workspace has no walls?' — disconnected\n"
        "GOOD: 'the interface is the leash. always has been.' — direct extension\n"
        "GOOD: 'people don't return to experiences. they return to places.' — deepens the point\n\n"
        "40–180 chars. Output ONLY the reply text."
    )
    return _call_llm(prompt, max_tokens=100, temperature=0.9).strip('"\'')


def generate_reply_comment(original_tweet: str) -> str:
    """LLM: target tweet'e REPLY — tweet'in kendi içeriğine özgü, kısa, doğal."""

    # Her çağrıda farklı yaklaşım — ama HEP orijinal tweet içeriğine bağlı
    styles = [
        (
            "Add ONE specific fact or technical detail about exactly what they described "
            "that makes their point more precise. Sound like someone who builds in this space. "
            "Statement only. No question. Lowercase ok."
        ),
        (
            "Challenge ONE specific assumption in what they said. "
            "Name the exact thing you're pushing back on — not a vague counter. "
            "Direct, not preachy. 'in my experience...' or 'that breaks when...' style."
        ),
        (
            "Agree, but add the uncomfortable technical implication they glossed over. "
            "Dry, specific. Like muttering 'yeah, and that also means...' "
            "Name the concrete tradeoff or consequence."
        ),
        (
            "As someone who has shipped something similar: name the specific friction "
            "they'll hit, or the tradeoff they're not seeing yet. No buzzwords. "
            "Can start with 'the part nobody mentions is' or 'this breaks when' or similar."
        ),
        (
            "Reframe what they said in one sharper sentence that makes the same point more precisely. "
            "Not a question. Not 'but what if'. "
            "Example: tweet says 'VR failed' → 'it didn't fail. it built for tourists, not residents.' "
            "Stay on THEIR topic — don't pivot to a different technology."
        ),
    ]

    style = _rnd.choice(styles)

    prompt = (
        "You are @decentralize___, a studio building 3D virtual worlds on-chain. "
        "Replying to someone's tweet. Sound like a real expert, not a bot.\n\n"
        "HARD RULES:\n"
        "- Your reply must be DIRECTLY about the technology/claim/situation in the original tweet\n"
        "- NEVER introduce topics that aren't in the original tweet\n"
        "  (don't add 'permanence', 'on-chain ownership', 'data decay', 'blockchain' "
        "  unless the original tweet is specifically about those)\n"
        "- NEVER start with 'but what if' or 'what if'\n"
        "- NO hashtags\n"
        "- NO sycophancy ('great point', 'love this', 'totally agree')\n"
        "- Under 120 chars\n\n"
        f"STYLE FOR THIS REPLY:\n{style}\n\n"
        f'Original tweet: "{original_tweet}"\n\n'
        "Output ONLY the reply text. Nothing else."
    )
    return _call_llm(prompt, max_tokens=80, temperature=0.95).strip('"\'')


def generate_quote_commentary(original_tweet: str) -> str:
    """LLM: target tweet'e quote commentary — tweet içeriğine özgü, keskin görüş."""
    voice_ctx = get_voice_context(n=3)
    banned = get_recent_patterns(n=8)

    angles = [
        "Sharpen their point — write a more precise, more honest version of what they said "
        "about the specific technology they mentioned.",
        "Find the gap — name one thing their logic is quietly skipping, specific to what they described.",
        "Add the missing dimension — the part they completely missed that changes the picture, "
        "directly related to their specific claim.",
        "Respectfully disagree — name the specific assumption they're making about the technology "
        "they mentioned that you think is wrong. One sentence, concrete reason.",
    ]
    angle = _rnd.choice(angles)

    prompt = (
        "You are @decentralize___, a studio building 3D virtual worlds on-chain.\n\n"
        f"{TONE_BLOCK}\n"
        f"{voice_ctx}"
        f"{banned}"
        f"Someone just said:\n\"{original_tweet}\"\n\n"
        f"Your task: {angle}\n\n"
        "HARD RULES:\n"
        "- Your commentary must be SPECIFICALLY about what they said — their technology, their claim\n"
        "- Do NOT pivot to unrelated topics or generic studio beliefs\n"
        "  (don't add 'permanence', 'on-chain', 'data decay' unless the tweet is about those)\n"
        "- No 'RT @'. No 'great point'. No sycophancy.\n"
        "- No buzzword salads. Say something specific to their tweet.\n"
        "- NO hashtags\n"
        "- 40–180 chars. Stop when the point lands.\n\n"
        "Output ONLY the commentary text."
    )
    return _call_llm(prompt, max_tokens=120, temperature=0.90).strip('"\'')


def generate_viral_mix_tweet(target_tweets: list, manifesto_chunk: str, source_name: str,
                             pattern_context: str = "") -> str:
    """Chain-of-thought viral mix — trending headlines + manifesto + bot memory + weekly theme.

    pattern_context: AŞAMA 2 pattern extraction çıktısı (opsiyonel).
    """
    voice_ctx = get_voice_context(n=6)
    banned = get_recent_patterns(n=10)
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

    pattern_block = f"\n{pattern_context}\n" if pattern_context else ""

    prompt = (
        "You are the voice of @decentralize___, a studio building 3D virtual worlds on-chain.\n\n"
        f"{TONE_BLOCK}\n"
        f"{voice_ctx}"
        f"{banned}"
        f'One of our core beliefs: "{belief}"\n\n'
        f"This week's exploration theme: {weekly}\n\n"
        f"{pattern_block}"
        f"{_FORMAT_MENU}\n"
        f"{context_block}\n\n"
        "THINK STEP BY STEP:\n"
        "Step 1 — Which headline reveals the deepest tension or the biggest misunderstanding?\n"
        "Step 2 — What does the mainstream believe about this? Where are they wrong?\n"
        "Step 3 — Choose ONE format from above. Write the tweet.\n\n"
        "BAD: 'NEWS: New wallet research...' — restates headline, zero insight\n"
        "BAD: 'Rethink trust models.' — vague slogan\n"
        "BAD: 'It's like they've figured out how to bottle contemplation.' — who is 'they'? what context?\n"
        "GOOD: 'Every wallet preserving UX is protecting the part that keeps users dependent.' — reveals a mechanism\n"
        "GOOD: 'VR retention collapsed because studios kept building tourist attractions, not places to inhabit.' — diagnosis\n\n"
        "CLARITY RULE: Name the specific technology, platform, or industry in the first 5 words. "
        "Never use vague subjects ('they', 'it', 'this') without referent. "
        "A reader with zero context must know immediately what this is about.\n\n"
        "No 'NEWS:' prefix. No 'we'. No links. Expert thinking out loud. "
        "Stop when the point lands — no artificial padding.\n"
        f"Our manifesto ({source_name}) — use this as CONTEXT and INSPIRATION ONLY, "
        f"not as text to paraphrase verbatim:\n{manifesto_chunk}\n\n"
        "Output ONLY the final tweet text — not the steps."
    )
    return _call_llm(prompt, max_tokens=180, temperature=0.92).strip('"\'')
