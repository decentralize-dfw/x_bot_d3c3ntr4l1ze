"""
core/quality.py
---------------
Kalite kontrol + ortak retry döngüsü.

Faz 2.2 — post_with_retry ortak fonksiyon (rapor2.txt §2.2)
Şu an 5 yerde kopyala-yapıştır olan döngüyü tek yere çıkardık.
"""
import tweet_archive
from core.llm import score_tweet_detail, is_semantically_duplicate

IQ_THRESHOLD = 150   # Tüm 4 eksen ~9+ olmalı; en az bir eksen 10 gerektirir
AXIS_THRESHOLD = 9.0  # Her eksenden asgari puan


def post_with_retry(
    generator_fn,
    max_attempts: int = 5,
    quality_threshold: float = AXIS_THRESHOLD,
) -> str | None:
    """Tweet üretim + kalite + dedup döngüsü — tek merkezi implementation.

    generator_fn: () -> str  (tweet text üretir)
    Kabul koşulları:
      - Her eksen (O/S/P/C) >= quality_threshold (varsayılan 9.0)
      - IQ >= 150
      - Arşivdeki herhangi bir tweet'e %50'den fazla benzemiyor (Jaccard)
    Returns: tweet text veya None (max_attempts sonunda geçemezse)
    """
    for attempt in range(max_attempts):
        try:
            text = generator_fn()
            if not text or len(text) < 10:
                print(f"Attempt {attempt+1}: text too short, retrying...")
                continue
            if tweet_archive.is_too_similar(text):
                print(f"Attempt {attempt+1}: too similar (Jaccard >50%), retrying...")
                continue
            if tweet_archive.is_theme_in_cooldown(text):
                print(f"Attempt {attempt+1}: theme in cooldown, retrying...")
                continue
            if is_semantically_duplicate(text):
                print(f"Attempt {attempt+1}: too similar (semantic), retrying...")
                continue
            detail = score_tweet_detail(text)
            min_axis = min(detail["o"], detail["s"], detail["p"], detail["c"])
            iq = detail["iq"]
            if min_axis < quality_threshold:
                print(f"Attempt {attempt+1}: min axis {min_axis}/10 < {quality_threshold}, retrying...")
                continue
            if iq < IQ_THRESHOLD:
                print(f"Attempt {attempt+1}: IQ {iq} < {IQ_THRESHOLD}, retrying...")
                continue
            return text
        except Exception as e:
            print(f"Attempt {attempt+1} failed: {e}")
    return None
