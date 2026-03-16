"""
bot.py
------
@decentralize___ Twitter Bot — Ana giriş noktası.

Sadece mod routing içerir. Tüm iş mantığı modes/, core/, utils/ altında.

Kullanım:
    python bot.py <mode>

Modlar:
    morning              Medya arşivi tweeti (sabah)
    evening              Viral manifesto tweeti
    evening_controversial Contrarian manifesto tweeti
    artwork              Artwork drop tweeti
    decrypt              Decrypt.co haber thread'i
    venturebeat          VentureBeat haber thread'i
    viral_mix            Target + manifesto fusion tweeti
    community_pulse      Haftalık RSS özet thread'i (Pazartesi)
    data_viz             Haftalık niche frekans bar chart tweeti
    quote_tweet          Target tweet'leri quote et (Premium — AKTİF)
    reply_mode           Target tweet'lerine kriptik yanıt ver (Premium — AKTİF)
    like_mode            Target tweet'lerine like at (Premium — AKTİF)
    retweet_mode         Target tweet'lerini retweet et (Premium — YENİ)
"""
import sys
from datetime import datetime, timezone

import tweet_archive

# ── Quiet Day Kontrolü ─────────────────────────────────────────────────────────
_QUIET_DAY = 6  # Pazar
_QUIET_EXEMPT = {
    "morning", "community_pulse", "data_viz",
    "drift_check", "quote_tweet",
    "reply_mode", "like_mode", "retweet_mode",  # engagement modları Pazar da çalışır
    None,
}


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else None
    today = datetime.now(timezone.utc).weekday()

    if today == _QUIET_DAY and mode not in _QUIET_EXEMPT:
        print(f"Quiet day (Sunday) — skipping '{mode}'. Only morning/pulse/viz/engagement run today.")
        sys.exit(0)

    # Her moddan önce archive temizliği (Faz 3.6 — rapor2.txt §3.6)
    try:
        tweet_archive.cleanup_old_entries()
    except Exception as _e:
        print(f"Archive cleanup warning: {_e}")

    if mode == "morning":
        from modes.morning import post_morning_tweet
        post_morning_tweet()

    elif mode == "evening":
        from modes.evening import post_evening_tweet
        post_evening_tweet()

    elif mode == "evening_controversial":
        from modes.evening import post_controversial_evening_tweet
        post_controversial_evening_tweet()

    elif mode == "artwork":
        from modes.artwork import post_artwork_tweet
        post_artwork_tweet()

    elif mode == "decrypt":
        from modes.news import post_decrypt_tweet
        post_decrypt_tweet()

    elif mode == "venturebeat":
        from modes.news import post_venturebeat_tweet
        post_venturebeat_tweet()

    elif mode == "viral_mix":
        from modes.viral_mix import post_viral_mix_tweet
        post_viral_mix_tweet()

    elif mode == "community_pulse":
        from modes.community_pulse import post_community_pulse_thread
        post_community_pulse_thread()

    elif mode == "data_viz":
        from modes.data_viz import post_data_viz_tweet
        post_data_viz_tweet()

    elif mode == "quote_tweet":
        from modes.quote_tweet import post_quote_tweet
        post_quote_tweet()

    elif mode == "reply_mode":
        from modes.reply_mode import post_reply_tweet
        post_reply_tweet()

    elif mode == "like_mode":
        from modes.like_mode import post_like_tweets
        post_like_tweets()

    elif mode == "retweet_mode":
        from modes.retweet_mode import post_retweet
        post_retweet()

    else:
        # Saat bazlı fallback (geriye uyumluluk + manuel test)
        hour = datetime.now(timezone.utc).hour
        if hour == 7:
            from modes.morning import post_morning_tweet
            post_morning_tweet()
        elif hour == 9:
            from modes.news import post_decrypt_tweet
            post_decrypt_tweet()
        elif hour == 11:
            from modes.artwork import post_artwork_tweet
            post_artwork_tweet()
        elif hour == 13:
            from modes.news import post_venturebeat_tweet
            post_venturebeat_tweet()
        elif hour in [15, 18]:
            from modes.evening import post_evening_tweet
            post_evening_tweet()
        elif hour in [16, 20]:
            from modes.evening import post_controversial_evening_tweet
            post_controversial_evening_tweet()
        else:
            print(f"Test Mode (Hour: {hour} UTC). Running Evening routine...")
            from modes.evening import post_evening_tweet
            post_evening_tweet()


if __name__ == "__main__":
    main()
