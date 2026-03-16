"""
core/voice.py
-------------
Bot sesi, haftalık tema, NICHE_KEYWORDS merkezi.

Faz 2.3 — TONE_BLOCK strategy güncellemesi (d3c3ntr4l1z3_strategy.docx)
Faz 2.4 — NICHE_KEYWORDS tek yerden (rapor2.txt §2.4)
"""
import json
import os
import random
from datetime import datetime, timezone

import tweet_archive

# ── NICHE_KEYWORDS — tek merkezi tanım ────────────────────────────────────────
# Faz 2.4: Şu an 3 ayrı yerde tanımlı, burası tek kaynak oldu.
NICHE_KEYWORDS = [
    "webxr", "metaverse", "spatial computing", "virtual reality",
    "augmented reality", "mixed reality", "3d web", "immersive",
    "decentralized", "web3", "on-chain", "digital twin",
    "volumetric", "xr", "vr", "ar", "holographic",
    "generative ai", "procedural", "realtime 3d", "digital art", "nft",
]

# ── Shared voice rules — her LLM prompt'una inject edilir ─────────────────────
# Faz 2.3: Strategy'e göre hashtag artık zorunlu (optional değil).
# Dış link yasak (d3c3ntr4l1z3_strategy.docx §03 Uygulama).
TONE_BLOCK = (
    "Voice rules (non-negotiable):\n"
    "- Write in natural sentence case. Never all-caps.\n"
    "- IQ 150+ thinking: carry a specific insight the reader hasn't encountered before.\n"
    "- Leave the reader with a question they can't immediately answer — make them stop and think.\n"
    "- Position as a thought leader in virtual design, metaverse, WebXR, spatial computing, on-chain worlds.\n"
    "- No buzzword salads. No clichés. No 'the future is here.' Say something true and surprising.\n"
    "- ALWAYS end with 2-3 relevant hashtags chosen from: "
    "#SpatialComputing #AI #Metaverse #Web3 #VR #Crypto #WebXR #DigitalArt #OnChain #ImmersiveWeb\n"
    "- No external links in tweet text — internal X references only.\n"
)

# ── Follow-up questions ────────────────────────────────────────────────────────
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

# ── Weekly theme rotation (12 haftalık, deterministik) ────────────────────────
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


# ── Beliefs ───────────────────────────────────────────────────────────────────
def _load_beliefs() -> dict:
    beliefs_path = os.path.join(os.path.dirname(__file__), "..", "beliefs.json")
    try:
        with open(beliefs_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


_used_beliefs_this_session: set = set()


def random_belief() -> str:
    """Belief seç. Aynı session içinde aynı belief tekrarlanmaz."""
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
