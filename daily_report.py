"""
daily_report.py
---------------
Günlük tarama sonrası PDF bilan üret.

Yapı:
  0. PUANLAMA ÖZETİ  — kaç tweet tarandı, kaçı kabul/ret edildi
  1. RET LİSTESİ     — reddedilen tweetler + neden
  2. QUOTE RT        — 5 aday, IQ3 skoru, "Ne yazılacak:" taslak
  3. RETWEET         — 5 aday, IQ3 skoru
  4. REPLY           — 20 aday, IQ3 skoru, "Ne yazılacak:" taslak

IQ3 = (O+S+C)/3 * 16.5  (10 ortalama = 165 IQ, P ekseni hariç)
IQ  = (O+S+P+C)/4 * 16.5 (kendi tweetlerimiz için)

Çıktı: bilan_quotidienne/YYYY-MM-DD.pdf
"""
import json
import os
from datetime import datetime, timezone
from itertools import zip_longest

from fpdf import FPDF

SCAN_PATH     = os.path.join(os.path.dirname(__file__), "scan_results.json")
REJECTED_PATH = os.path.join(os.path.dirname(__file__), "scan_rejected.json")
OUTPUT_DIR    = os.path.join(os.path.dirname(__file__), "bilan_quotidienne")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

_PAGE_W = 190  # A4 — sol+sağ margin 10mm


# ── Veri yükleme ──────────────────────────────────────────────────────────────

def _load_scan() -> list:
    try:
        with open(SCAN_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"scan_results.json yüklenemedi: {e}")
        return []


def _load_rejected() -> list:
    try:
        with open(REJECTED_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


# ── Kategori bazlı aday seçimi ────────────────────────────────────────────────

def _by_category(tweets: list, category: str, n: int) -> list:
    """scan_results'tan belirli kategorideki tweetleri IQ3'e göre sıralı döndür.

    daily_scan.py tarafından atanmış "category" alanını kullanır.
    Kategori boşsa boş liste döner — başka kategorilerden çalmaz.
    """
    primary = [t for t in tweets if t.get("category") == category]
    return sorted(primary, key=lambda t: t.get("scores", {}).get("iq3", 0), reverse=True)[:n]


def _select_sections(tweets: list):
    """Tüm bölümler için aday listelerini döndür.

    Her kategori kendi tweet'lerini alır — başka kategorilerden çalmaz.
    daily_scan.py'nin atadığı "category" alanı kullanılır.
    Returns: (quote_rt, rt_tweets, reply_tweets)
    """
    quote_rt    = _by_category(tweets, "quote_rt", 5)
    rt_tweets   = _by_category(tweets, "rt",       5)
    reply_tweets = _by_category(tweets, "reply",   20)
    return quote_rt, rt_tweets, reply_tweets


# ── PDF yardımcıları ──────────────────────────────────────────────────────────

def _safe(text: str) -> str:
    return text.replace("\n", " ").encode("latin-1", errors="replace").decode("latin-1")


def _safe_wrap(text: str) -> str:
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _generate_reply_draft(tweet_text: str) -> str:
    if not GROQ_API_KEY:
        return ""
    try:
        from core.llm import generate_reply_comment
        return generate_reply_comment(tweet_text)
    except Exception as e:
        print(f"  reply draft failed: {e}")
        return ""


def _generate_quote_draft(tweet_text: str) -> str:
    if not GROQ_API_KEY:
        return ""
    try:
        from core.llm import generate_quote_commentary
        return generate_quote_commentary(tweet_text)
    except Exception as e:
        print(f"  quote draft failed: {e}")
        return ""


class _DailyPDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 14)
        self.cell(_PAGE_W, 10, "@d3c3Ntr4L1z3 - Bilan Quotidien",
                  new_x="LMARGIN", new_y="NEXT", align="C")
        self.set_font("Helvetica", "", 10)
        date_str = datetime.now(timezone.utc).strftime("%d %B %Y - %H:%M UTC")
        self.cell(_PAGE_W, 7, date_str, new_x="LMARGIN", new_y="NEXT", align="C")
        self.ln(3)
        self.set_draw_color(80, 80, 80)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")


def _section_header(pdf: _DailyPDF, title: str, rgb: tuple) -> None:
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_fill_color(*rgb)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(_PAGE_W, 8, _safe(f"  {title}"),
             new_x="LMARGIN", new_y="NEXT", fill=True)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(3)


def _iq_bar_label(iq: int) -> str:
    if iq >= 148:  return "EXCELLENT"
    if iq >= 132:  return "GOOD"
    if iq >= 115:  return "OK"
    if iq >= 99:   return "LOW"
    return "REJECT"


def _tweet_block(pdf: _DailyPDF, i: int, t: dict, draft: str = "") -> None:
    scores = t.get("scores", {})
    iq3 = scores.get("iq3", "?")
    iq  = scores.get("iq",  "?")
    avg = scores.get("avg", "?")
    o   = scores.get("o",   "?")
    s   = scores.get("s",   "?")
    p   = scores.get("p",   "?")
    c   = scores.get("c",   "?")
    cat = t.get("category", "?")
    label = _iq_bar_label(iq3 if isinstance(iq3, int) else 0)

    # ── Başlık satırı ──
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(245, 245, 245)
    header_line = _safe(
        f"#{i}  @{t['author']}   eng:{t['engagement_score']}   cat:{cat}"
        f"   |   IQ3:{iq3} [{label}]  IQ:{iq}  avg:{avg}  O:{o} S:{s} P:{p} C:{c}"
    )
    pdf.cell(_PAGE_W, 6, header_line, new_x="LMARGIN", new_y="NEXT", fill=True)

    # ── Tweet metni ──
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(30, 30, 30)
    pdf.multi_cell(_PAGE_W, 5, _safe_wrap(t["text"]),
                   new_x="LMARGIN", new_y="NEXT")

    # ── Bot cevabı / taslak ──
    if draft:
        pdf.ln(1)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(0, 80, 0)
        pdf.cell(_PAGE_W, 5, ">> BOT YAZACAK:",
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(0, 60, 0)
        pdf.set_fill_color(230, 255, 230)
        pdf.multi_cell(_PAGE_W, 5, _safe_wrap(draft),
                       new_x="LMARGIN", new_y="NEXT", fill=True)
        pdf.set_text_color(0, 0, 0)
    elif not GROQ_API_KEY:
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(150, 100, 0)
        pdf.cell(_PAGE_W, 5, "(GROQ_API_KEY yok -- taslak uretilmedi)",
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)

    # ── Link ──
    pdf.ln(1)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(40, 80, 180)
    url = f"https://x.com/{t['author']}/status/{t['tweet_id']}"
    pdf.multi_cell(_PAGE_W, 5, url, new_x="LMARGIN", new_y="NEXT", link=url)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(3)


def generate_daily_report() -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    tweets   = _load_scan()
    rejected = _load_rejected()

    quote_rt, rt_tweets, reply_tweets = _select_sections(tweets)

    # ── Taslakları üret ──
    print("Quote taslakları uretiliyor...")
    quote_drafts = []
    for t in quote_rt:
        d = _generate_quote_draft(t["text"])
        print(f"  @{t['author']}: {d[:70] if d else '(bos)'}")
        quote_drafts.append(d)

    print("Reply taslakları uretiliyor...")
    reply_drafts = []
    for t in reply_tweets:
        d = _generate_reply_draft(t["text"])
        print(f"  @{t['author']}: {d[:70] if d else '(bos)'}")
        reply_drafts.append(d)

    # ── PDF ──
    pdf = _DailyPDF()
    pdf.set_margins(left=10, top=10, right=10)
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # 0. PUANLAMA ÖZETİ
    _section_header(pdf, "PUANLAMA OZETI", (60, 60, 60))
    pdf.set_font("Helvetica", "", 9)
    total_scanned = len(tweets) + len(rejected)
    # Kategori sayıları
    cat_counts = {}
    for t in tweets:
        cat = t.get("category", "?")
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
    cat_str = "  ".join(f"{k}:{v}" for k, v in sorted(cat_counts.items()))
    pdf.multi_cell(
        _PAGE_W, 5,
        _safe(
            f"Taranan: {total_scanned} tweet\n"
            f"Kabul (IQ3>=99+): {len(tweets)} tweet  |  {cat_str}\n"
            f"Ret: {len(rejected)} tweet\n"
            f"IQ3 olcegi: (O+S+C)/3*16.5  (P ekseni hariç — baskasının tweeti)  |  "
            f"Esik: IQ3>=115 (tur1)  IQ3>=99 (tur2-3)  IQ3>=82 (tur4 kurtarma)"
        ),
        new_x="LMARGIN", new_y="NEXT",
    )
    pdf.ln(4)

    # 1. RET LİSTESİ (varsa)
    if rejected:
        _section_header(pdf, f"REDDEDILENLER -- {len(rejected)} tweet", (180, 50, 50))
        pdf.set_font("Helvetica", "", 8)
        for r in rejected:
            line = _safe(f"@{r.get('author','?')}  {r.get('reason','?')}  |  {r.get('text','')[:80]}...")
            pdf.multi_cell(_PAGE_W, 4, line, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)

    # 2. QUOTE RT
    # BUG FIX #5: zip_longest ile draft listesi kısa kalsa da tweet kaybı olmaz
    _section_header(pdf, f"QUOTE RETWEET -- {len(quote_rt)}/5 aday", (40, 80, 160))
    for i, (t, draft) in enumerate(zip_longest(quote_rt, quote_drafts, fillvalue=""), 1):
        if not t:
            continue
        _tweet_block(pdf, i, t, draft or "")
    pdf.ln(4)

    # 3. RETWEET
    _section_header(pdf, f"RETWEET -- {len(rt_tweets)}/5 aday", (50, 130, 70))
    for i, t in enumerate(rt_tweets, 1):
        _tweet_block(pdf, i, t)
    pdf.ln(4)

    # 4. REPLY
    # BUG FIX #5: zip_longest ile draft listesi kısa kalsa da reply kaybı olmaz
    _section_header(pdf, f"REPLY -- {len(reply_tweets)}/20 aday", (160, 80, 40))
    for i, (t, draft) in enumerate(zip_longest(reply_tweets, reply_drafts, fillvalue=""), 1):
        if not t:
            continue
        _tweet_block(pdf, i, t, draft or "")

    # Alt özet
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(230, 230, 230)
    summary = _safe(
        f"  Tarama: {total_scanned} tweet  |  "
        f"QuoteRT:{len(quote_rt)}  RT:{len(rt_tweets)}  Reply:{len(reply_tweets)}  |  "
        f"Ret:{len(rejected)}  |  "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M UTC')}"
    )
    pdf.cell(_PAGE_W, 7, summary, new_x="LMARGIN", new_y="NEXT", fill=True)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    output_path = os.path.join(OUTPUT_DIR, f"{date_str}.pdf")
    pdf.output(output_path)
    print(f"Rapor olusturuldu: {output_path}")
    return output_path


if __name__ == "__main__":
    generate_daily_report()
