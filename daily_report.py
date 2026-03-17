"""
daily_report.py
---------------
Günlük tarama sonrası PDF bilan üret.

scan_results.json'dan şunları seçer:
  - 5 Quote RT adayı  (içerik zengin, yüksek engagement)
  - 5 Retweet adayı   (yüksek engagement, kısa ve net)
  - 20 Reply adayı    (reply_settings=everyone, çeşitli yazarlar)

Çıktı: bilan_quotidienne/YYYY-MM-DD.pdf
"""
import json
import os
from datetime import datetime, timezone

from fpdf import FPDF

SCAN_PATH = os.path.join(os.path.dirname(__file__), "scan_results.json")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "bilan_quotidienne")


def _load_scan() -> list:
    try:
        with open(SCAN_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Could not load scan_results.json: {e}")
        return []


def _select_quote_rt(tweets: list, n: int = 5) -> list:
    """Quote RT adayları: içerik zengin (uzun metin) + yüksek engagement."""
    scored = sorted(
        tweets,
        key=lambda t: t["engagement_score"] + len(t["text"]) // 15,
        reverse=True,
    )
    return scored[:n]


def _select_rt(tweets: list, exclude_ids: set, n: int = 5) -> list:
    """RT adayları: saf engagement sıralaması, zaten seçilmişler hariç."""
    candidates = [t for t in tweets if t["tweet_id"] not in exclude_ids]
    return sorted(candidates, key=lambda t: t["engagement_score"], reverse=True)[:n]


def _select_reply(tweets: list, exclude_ids: set, n: int = 20) -> list:
    """Reply adayları: reply_settings=everyone, yazar çeşitliliği öncelikli."""
    candidates = [
        t for t in tweets
        if t["tweet_id"] not in exclude_ids
        and t.get("reply_settings", "everyone") == "everyone"
    ]
    seen_authors: set = set()
    result = []
    for t in sorted(candidates, key=lambda t: t["engagement_score"], reverse=True):
        if t["author"] not in seen_authors:
            result.append(t)
            seen_authors.add(t["author"])
        elif len(result) < n // 2:
            result.append(t)
        if len(result) >= n:
            break
    # Eksik kalırsa kalan adaylardan tamamla
    for t in candidates:
        if t not in result:
            result.append(t)
        if len(result) >= n:
            break
    return result[:n]


def _safe_text(text: str) -> str:
    """PDF için latin-1 uyumlu metin döndür."""
    return text.replace("\n", " ").encode("latin-1", errors="replace").decode("latin-1")


class _DailyPDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 14)
        self.cell(0, 10, "@d3c3Ntr4L1z3 - Bilan Quotidien", new_x="LMARGIN", new_y="NEXT", align="C")
        self.set_font("Helvetica", "", 10)
        date_str = datetime.now(timezone.utc).strftime("%d %B %Y - %H:%M UTC")
        self.cell(0, 7, date_str, new_x="LMARGIN", new_y="NEXT", align="C")
        self.ln(3)
        self.set_draw_color(80, 80, 80)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")


def _add_section(pdf: _DailyPDF, title: str, tweets: list, rgb: tuple) -> None:
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_fill_color(*rgb)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 8, _safe_text(f"  {title}"), new_x="LMARGIN", new_y="NEXT", fill=True)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)

    for i, t in enumerate(tweets, 1):
        # Başlık satırı
        pdf.set_font("Helvetica", "B", 9)
        header = _safe_text(f"#{i}  @{t['author']}   eng:{t['engagement_score']}")
        pdf.cell(0, 6, header, new_x="LMARGIN", new_y="NEXT")

        # Tweet metni
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(0, 5, _safe_text(t["text"]))

        # Link
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(60, 100, 200)
        url = f"https://x.com/{t['author']}/status/{t['tweet_id']}"
        pdf.cell(0, 5, url, new_x="LMARGIN", new_y="NEXT", link=url)
        pdf.set_text_color(0, 0, 0)
        pdf.ln(2)

    pdf.ln(5)


def generate_daily_report() -> str:
    """PDF rapor üret, dosya yolunu döndür."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    tweets = _load_scan()

    quote_rt = _select_quote_rt(tweets, 5)
    used = {t["tweet_id"] for t in quote_rt}

    rt_tweets = _select_rt(tweets, used, 5)
    used.update(t["tweet_id"] for t in rt_tweets)

    reply_tweets = _select_reply(tweets, used, 20)

    pdf = _DailyPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    _add_section(pdf, f"QUOTE RETWEET — {len(quote_rt)}/5 adaylar", quote_rt, (40, 80, 160))
    _add_section(pdf, f"RETWEET — {len(rt_tweets)}/5 adaylar", rt_tweets, (50, 130, 70))
    _add_section(pdf, f"REPLY — {len(reply_tweets)}/20 adaylar", reply_tweets, (160, 80, 40))

    # Alt özet
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(230, 230, 230)
    pdf.set_text_color(0, 0, 0)
    summary = _safe_text(
        f"  Tarama: {len(tweets)} tweet  |  "
        f"QuoteRT:{len(quote_rt)}  RT:{len(rt_tweets)}  Reply:{len(reply_tweets)}  |  "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M UTC')}"
    )
    pdf.cell(0, 7, summary, new_x="LMARGIN", new_y="NEXT", fill=True)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    output_path = os.path.join(OUTPUT_DIR, f"{date_str}.pdf")
    pdf.output(output_path)
    print(f"Daily report generated: {output_path}")
    return output_path


if __name__ == "__main__":
    generate_daily_report()
