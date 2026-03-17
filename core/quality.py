"""
core/quality.py
---------------
Kalite kontrol + ortak retry döngüsü.

Faz 2.2 — post_with_retry ortak fonksiyon (rapor2.txt §2.2)
Şu an 5 yerde kopyala-yapıştır olan döngüyü tek yere çıkardık.
"""
import tweet_archive
from core.llm import score_tweet_quality, is_semantically_duplicate


# BUG FIX #19: Default threshold 9.0 → 6.0
# 9.0 = tüm 4 eksen 9+/10 zorunlu. viral_mix her gün 5 denemede pass edemez,
# fallback'e düşer. 6.0 production-safe minimum (her eksen en az 6/10).
def post_with_retry(
    generator_fn,
    max_attempts: int = 5,
    quality_threshold: float = 6.0,
) -> str | None:
    """Tweet üretim + kalite + dedup döngüsü — tek merkezi implementation.

    generator_fn: () -> str  (tweet text üretir)
    Returns: tweet text veya None (3 denemede de geçemezse)
    """
    for attempt in range(max_attempts):
        try:
            text = generator_fn()
            if not text or len(text) < 10:
                print(f"Attempt {attempt+1}: text too short, retrying...")
                continue
            if tweet_archive.is_too_similar(text):
                print(f"Attempt {attempt+1}: too similar (Jaccard), retrying...")
                continue
            if tweet_archive.is_theme_in_cooldown(text):
                print(f"Attempt {attempt+1}: theme in cooldown, retrying...")
                continue
            if is_semantically_duplicate(text):
                print(f"Attempt {attempt+1}: too similar (semantic), retrying...")
                continue
            score = score_tweet_quality(text)
            if score >= quality_threshold:
                return text
            print(f"Attempt {attempt+1}: quality {score:.1f}/10 < {quality_threshold}, retrying...")
        except Exception as e:
            print(f"Attempt {attempt+1} failed: {e}")
    return None
