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
    | \b(airdrop|giveaway|free\s+tokens?|win\s+\d|drop\s+your\s+wallets?|enter\s+to\s+win)\b
    # Follower farming
    | \b(follow\s+(&|and)\s+(rt|retweet)|rt\s+to\s+(win|enter)|like\s+(and|&)\s+(follow|rt)|like\s+and\s+follow)\b
    # Finansal spam
    | \b(buy\s+now|last\s+chance|limited\s+supply|floor\s+price|bullish|bearish|ath\b)\b
    # Proje isim kalıpları
    | \b(8bit|babydoge|babyape|pepe\s+coin|shib\b|degens?\b|based\s+dev|doxed\s+team)\b
    # Boot sequence / hype intro
    | boot\s+sequence|gathering[\s,]+ready
    # Wallet/contract spam
    | \b(ca:|contract\s+address|0x[0-9a-f]{6,})\b
    # Token ticker ($XOOB, $ACH, $OS1, $KVAI vb. — harf+rakam karışık dahil)
    | \$[A-Z][A-Z0-9]{1,7}\b
    # Yield farming
    | \b(farming|yield\s+farm|liquidity\s+pool|staking\s+reward)\b
    # Wallet bağlantı spamı
    | \b(link\s+(your\s+)?(evm\s+)?wallet|connect\s+(your\s+)?wallet|evm\s+wallet|evm\s+address)\b
    # VIP / upgrade shill
    | \b(vip\s+access|unlock\s+vip|upgrade\s+(your|to)\s+(vip|access))\b
    # Çok sayıda 🚀 veya 💎 (3+)
    | (?:🚀){3,}|(?:💎){3,}|(?:🔥){4,}
    # AMA / etkinlik duyuruları
    | \b(ama\s+announcement|exclusive\s+\#?ama|join\s+us\s+for\s+(an?\s+)?(exclusive|live|ama|special)|featuring\s+guests?|set\s+your\s+reminders?)\b
    # Tarih damgalı etkinlik
    | \bdate:\s*(january|february|march|april|may|june|july|august|september|october|november|december|\d)\b
    # Proje ranking / ödül spami
    | \b(ranked\s+\#\d|\#1\s+(metaverse|game|project|platform)|most\s+played\s+(game|metaverse))\b
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
    # NSFW / adult / ERP içerik
    | \b(nsfw|erp|lewd|18\+)\b|\#(nsfw|erp)
    # Memecoin / meme token
    | \b(memecoin|meme\s+coin|step\s+into\s+the\s+rhythm\s+of\s+web3)\b
    # "@Proje is trying to / built to / designed to" ürün shili
    | @\w+\s+is\s+(trying\s+to\s+(solve|fix|change|disrupt|modernize|revolutionize)|designed\s+to|built\s+to\s+(solve|fix|change)|here\s+to\s+(solve|fix|change|disrupt))\b
    # "Discover it:" / "Link in bio" / tıklama CTAları
    | \b(discover\s+it\s*:|link\s+in\s+(bio|the\s+replies?|below)|tap\s+the\s+link|click\s+(below|here\s+to))\b
    # "modernize/revolutionize/reinvent" + web3/blockchain bağlamı
    | \b(modernize|revolutionize|reinvent)\b.{0,80}\b(web3|blockchain|crypto|on[-\s]?chain|defi)\b
    # "The future of X is here/becoming more" hype kalıpları
    | \b(the\s+future\s+of\s+\w+\s+is\s+(here|becoming|now|never)\b|more\s+(accessible|affordable)\s+than\s+ever)\b
    # Üçlü proje kolaborasyonu "A x B x C" (multi-word proje isimleri dahil)
    | \b\w+(?:\s+\w+)?\s+x\s+\w+(?:\s+\w+)?\s+x\s+\w+
    # "Happy N(th) anniversary" — kripto proje kutlaması
    | \bhappy\s+\d+\w*\s+(anniversary|birthday)\b
    # "Introducing [platform/protocol/sdk/framework]" — ürün duyurusu
    | \bintroducing\b.{0,60}\b(platform|protocol|development|framework|sdk|infrastructure|network)\b
    # "event is LIVE" / "is now LIVE!" — etkinlik duyurusu
    | \b(event\s+is\s+(live|now\s+live)|collection\s+race|season\s+pass|spring\s+event)\b
    # Hackathon/workshop event spami
    | \b(hackathon|hands[-\s]?on\s+workshops?|world[-\s]?class\s+mentors?|devkit)\b
    # "years of speed/momentum/building" — proje anma metinleri
    | \b(years\s+of\s+(speed|momentum|builders?|nonstop|growth)|here.s\s+to\s+the\s+next\s+chapter)\b
    # Yetişkin AI eşlik servisi / "digital twin" seks ürünü
    | \b(get\s+(you|me)\s+off(\s+in\s+under)?|try\s+(her|him)\s+out\s+here)\b
    | \bmy\s+digital\s+twin\s+(is\s+waiting|never\s+does|can\s+get)\b
    | \b(talking\s+in\s+my\s+voice|giving\s+you\s+attention\s+i|you\s+know\s+where\s+the\s+link\s+is|don.t\s+be\s+shy)\b
    # Koordineli GM/morning + proje shill kalıpları (shill farm)
    | \b(while\s+most\s+people\s+(scroll|sleep)|a\s+few\s+are\s+(building|earning|positioning))\b
    | \b(quietly\s+creating\s+a\s+space|positioning\s+(early|yourself)\s+(in\s+)?web3|early\s+in\s+web3)\b
    | \b(signal\s+over\s+noise|beyond\s+vanity\s+metrics|reward\s+impact|creator\s+ecosystem)\b
    | \b(morning\s+greetings?\s+winners?|gm\s+and\s+happy\s+taco|happy\s+taco\s+tuesday)\b
    | \b(is\s+all\s+about\s+community\s+growth|every\s+(building|logo)\s+(you\s+see|is\s+a))\b
    | \bmost\s+partnered\s+(metaverse|game|platform)\b
    # Tekrarlayan spam projeleri (koordineli shill kampanyaları)
    | \b(xoob(network)?|permacast(app)?|permaweb\s*(dao)?|0g[\s_]labs|dypians|nasun[\s_.]io|letscatapult|yom[\s_]official|wallchain\b|mindoai|3look[\s_.]io)\b
    # Konu dışı içerik: manga / anime / K-drama / spor
    | \b(romcom|manga|manhwa|webtoon|kdrama|k[-\s]drama|anime\b|springbok|rugby)\b
    | \b(copies\s+in\s+circulation|twin\s+tails|childhood\s+friend\s+but)\b
    | \bboyfriend.on.demand\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# ── Konu geçerlilik filtresi — en az bir keyword zorunlu ──────────────────────
# Kullanıcının belirlediği niche: WebXR, metaverse, virtual fashion,
# virtual architecture, digital art, smart cities, avatar, VRM,
# digital twin, interoperability, NFT art, generative AI, 3D world building.
_TOPIC_REQUIRED = re.compile(
    r"\b("
    # XR / VR / AR core
    r"webxr|openxr|"
    r"vr\b|virtual\s+reality|vr\s+headsets?|mixed\s+reality|augmented\s+reality|"
    r"spatial\s+(computing|audio|web|mapping|presence|design)|"
    r"immersive\s+(web|tech|experience|technology)|"
    r"gaussian\s+spl[ai]t|3dgs\b|haptic|volumetric|holograph|"
    r"webgl|three[.\s]?js|babylon[.\s]?js|a-frame\b|hmd\b|point\s+cloud|"
    r"xr\s+(development|developer|scene|space|headset|experience|app)|"
    r"immersive-web|spatial-web|"
    r"vr\s+(development|developer|scene|game|app|experience)|"
    # Metaverse / virtual world
    r"metaverse|virtual\s+(world|space|city|cities|gallery|museum|event|studio)|"
    # Virtual fashion / architecture / design
    r"virtual\s+(fashion|architecture|design)|"
    r"digital\s+(fashion|couture)|"
    r"metaverse\s+(design|fashion|architecture|art)|"
    # Digital art / generative / NFT art
    r"digital\s+art|generative\s+(art|ai)|ai\s+(art|generated|design|artist|model|agent|tool)|"
    r"nft\s*(art|drop|artist|collection|creator)|on.chain\s+(art|ownership|identity|asset)|"
    # Smart cities / future cities
    r"smart\s+cit(y|ies)|future\s+(city|cities)|urban\s+(digital|tech|design)|"
    # Avatar / VRM / interoperability / digital twin
    r"avatar\b|vrm\b|interoperability|digital\s+twin|"
    # 3D world building
    r"3d\s+(world|design|art|building|scene|space|model|web)|"
    r"world.?build(ing|er)|"
    # Spatial web / point cloud
    r"spatial\s+web|point\s+cloud"
    r")\b",
    re.IGNORECASE,
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


def is_off_topic(text: str) -> bool:
    """True dönerse tweet niche'imizle (WebXR/spatial/immersive) ilgisiz.

    daily_scan'de spam filtresiyle birlikte kullanılır.
    True döndürürse tweet kesinlikle kabul edilmez.
    """
    return not bool(_TOPIC_REQUIRED.search(text))
