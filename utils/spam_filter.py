"""
utils/spam_filter.py
--------------------
Merkezi spam / scam filtresi — daily_scan, like_mode ve her yerde kullanılır.
"""
import re

# ── Ana scam/spam pattern listesi ─────────────────────────────────────────────
_SCAM_PATTERNS = re.compile(
    r"""
    # NFT mint / drop spam
    \b(mint|minting|presale|whitelist|wl\s+spot|free\s+mint|claim\s+your)\b
    # Pump sinyalleri
    | \b(100x|1000x|mooning|to\s+the\s+moon|next\s+gem|hidden\s+gem|ape\s+in|dyor)\b
    # Airdrop / giveaway
    | \b(airdrop|giveaway|free\s+tokens?|win\s+\d|drop\s+your\s+wallet|enter\s+to\s+win)\b
    # Follower farming
    | \b(follow\s+(&|and)\s+(rt|retweet)|rt\s+to\s+(win|enter)|like\s+and\s+follow)\b
    # Finansal spam
    | \b(buy\s+now|last\s+chance|limited\s+supply|floor\s+price|bullish|bearish|ath\b)\b
    # Proje isim kalıpları
    | \b(8bit|babydoge|babyape|pepe\s+coin|shib\b|degens?\b|based\s+dev|doxed\s+team)\b
    # Boot sequence / hype intro
    | boot\s+sequence|gathering[\s,]+ready
    # Wallet/contract spam
    | \b(ca:|contract\s+address|0x[0-9a-f]{6,})\b
    # Token ticker ($XOOB, $ACH, $KVAI vb.)
    | \$[A-Z]{2,8}\b
    # Yield farming
    | \b(farming|yield\s+farm|liquidity\s+pool|staking\s+reward)\b
    # Wallet bağlantı spamı
    | \b(link\s+(your\s+)?(evm\s+)?wallet|connect\s+(your\s+)?wallet|evm\s+wallet|evm\s+address)\b
    # VIP / upgrade shill
    | \b(vip\s+access|unlock\s+vip|upgrade\s+(your|to)\s+(vip|access))\b
    # Çok sayıda 🚀 veya 💎 (3+)
    | (?:🚀){3,}|(?:💎){3,}|(?:🔥){4,}
    # AMA / etkinlik duyuruları
    | \b(ama\s+announcement|exclusive\s+#?ama|join\s+us\s+for\s+(an?\s+)?(exclusive|live|ama|special)|featuring\s+guests?|set\s+your\s+reminders?)\b
    # Tarih damgalı etkinlik
    | \bdate:\s*(january|february|march|april|may|june|july|august|september|october|november|december|\d)\b
    # Proje ranking / ödül spami
    | \b(ranked\s+#\d|#1\s+(metaverse|game|project|platform)|most\s+played\s+(game|metaverse))\b
    # Ürün güncelleme / Discord katılım spami
    | \b(update\s+is\s+(now\s+)?available(\s+at\s+no\s+additional\s+cost)?|join\s+our\s+discord\s+today)\b
    # ImpactFi / campaign / token puanı sistemi
    | \b(impactfi|impact\s*share|proof.of.influence|campaign\s+live\s+since|leaderboard\s+(back\s+)?live|points\s+now\s+reflect)\b
    # Proje işbirliği başlıkları "X x Y - AMA/Partnership/Launch"
    | \b\w+\s+x\s+\w+\s*[-—]\s*(ama|partnership|collab|announcement|launch)\b
    # Paid partnership / sponsorlu içerik
    | \b(paid\s+partnership|sponsored\s+by|in\s+partnership\s+with)\b
    # "Quick alpha" / degen hype girişi
    | \b(quick\s+alpha|alpha\s+for\s+(the\s+)?serious|morning\s+builders|gm\s+builders)\b
    # "@Project kills that / does this" proje shill kalıbı
    | @\w+\s+(kills\s+that|solves\s+that|fixes\s+this|does\s+this)\b
    # "isn't just another" proje narrative kalıbı
    | \bisn.t\s+just\s+another\s+web3\s+project\b
    # Earn rewards / airdrop farming dili
    | \b(earn\s+rewards(\s+all)?\s+(on|on[-\s]?chain)|gasless\s+quests?|join\s+tasks?)\b
    # DeFi / trading bot shili
    | \b(defi\s+protocol|trading\s+bot|auto[-\s]?trade|copy\s+trade|on[-\s]?chain\s+trader)\b
    # Fiat-to-crypto onramp spami
    | \b(fiat\s+(payment|rail|on[-\s]?ramp)|payment\s+rail|crypto\s+on[-\s]?ramp)\b
    # Grant / award self-promotion (proje başarı reklamı)
    | \b(secured\s+\d+\s+(competitive\s+)?(grants?|awards?))\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Maksimum hashtag sayısı
_MAX_HASHTAGS = 4
# Minimum kelime sayısı
_MIN_WORDS = 12


def is_spam(text: str) -> bool:
    """True dönerse tweet spam/scam — like/reply/quote YAPMA."""
    if _SCAM_PATTERNS.search(text):
        return True
    if text.count("#") > _MAX_HASHTAGS:
        return True
    if len(text.split()) < _MIN_WORDS:
        return True
    # 3+ @mention = reply farming (URL olmadan)
    if len(re.findall(r"@\w+", text)) >= 3 and "http" not in text:
        return True
    return False
