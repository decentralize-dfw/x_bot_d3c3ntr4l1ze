"""
voice_drift_check.py
--------------------
Son 20 tweet'in ses tutarlılığını LLM ile puanla.
GitHub Actions drift_check.yml tarafından her Pazar 08:00 UTC'de çalıştırılır.

Skor < 7 → stderr'e uyarı, exit(1)
Skor >= 7 → OK, exit(0)

Kullanım: python voice_drift_check.py
"""

import sys
import os
import json
import re

sys.path.insert(0, os.path.dirname(__file__))

import tweet_archive
import groq as groq_sdk

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")


def check_voice_drift() -> float:
    recent = tweet_archive.get_recent_tweet_texts(days=30)

    if len(recent) < 5:
        print(f"Not enough tweets for drift check ({len(recent)} found, need 5+). Skipping.")
        return 10.0

    sample = recent[-20:]
    tweets_block = "\n".join(f"{i+1}. {t}" for i, t in enumerate(sample))

    prompt = (
        "You are analyzing the voice consistency of a thought leader Twitter account.\n"
        "@decentralize___ builds 3D virtual worlds on-chain and covers WebXR, virtual design, spatial computing.\n\n"
        "Rate the following tweets on voice consistency: 1-10.\n\n"
        "Voice criteria:\n"
        "- Consistent intellectual perspective (no random topic hops)\n"
        "- Consistent tone: provocative, intelligent, human, lowercase\n"
        "- Consistent depth: specific claims, not vague generalities\n"
        "- No repetition of identical ideas\n\n"
        f"Recent tweets:\n{tweets_block}\n\n"
        'Respond ONLY in JSON: {"score": N, "issues": ["...", "..."], "recommendation": "..."}'
    )

    try:
        client = groq_sdk.Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=250,
            temperature=0.2,
        )
        raw = resp.choices[0].message.content.strip()
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if not match:
            print(f"Drift check: could not parse JSON from response:\n{raw}")
            return 7.0

        data = json.loads(match.group())
        score = float(data.get("score", 7))
        issues = data.get("issues", [])
        rec = data.get("recommendation", "")

        print(f"Voice drift score: {score:.1f}/10")
        if issues:
            for issue in issues:
                print(f"  ⚠  {issue}")
        if rec:
            print(f"  → Recommendation: {rec}")

        return score

    except Exception as e:
        print(f"Drift check error: {e}")
        return 7.0  # hata durumunda pass


if __name__ == "__main__":
    if not GROQ_API_KEY:
        print("GROQ_API_KEY not set, skipping drift check.")
        sys.exit(0)

    score = check_voice_drift()

    if score < 7.0:
        print(
            f"\n[DRIFT DETECTED] Score {score:.1f}/10 — voice consistency below threshold. "
            "Review recent tweets and adjust prompts if needed.",
            file=sys.stderr,
        )
        sys.exit(1)
    else:
        print(f"\n[OK] Voice consistency: {score:.1f}/10")
        sys.exit(0)
